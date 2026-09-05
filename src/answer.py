"""질의 하나를 받아 API 응답 4필드를 만든다. 파이프라인의 마지막 조립부다.

검색(층 1~3)까지는 `query.py` 와 `retrieval.py` 가 이미 한다. 이 파일은 그
결과를 생성 모델에 넘길 형태로 다듬고(S9), 답을 만들고(S10), 그 답이 근거에
실제로 있는지 검사한다(S11).

## 무엇을 돌려주나

과제가 요구하는 네 필드다. `docs/BRIEF.md` API 스키마 참조.

    question_id        받은 것을 그대로 돌려준다
    question           받은 질의 원문
    retrieved_context  답변에 쓴 공시 원문.  평가지표 2(근거 완전성)의 창구다
    think_trace        어떻게 그 답에 이르렀는지.  평가지표 5(추론 논리성)의 창구다
    answer             최종 답변

## think_trace 를 문자열이 아니라 구조로 만든다

`docs/PLAN.md` W8 에 적힌 대로다. 실행 기록을 JSON 으로 두면 같은 데이터가
`think_trace` 와 데모 화면 양쪽에서 쓰인다. 문자열로 만들면 나중에 파싱해서
되살려야 한다. 내보낼 때만 사람이 읽을 문자열로 바꾼다.

## 근거를 절 단위로 올린다

검색은 조각(chunk) 단위로 하고 근거는 절(section) 단위로 넘긴다. 조각은 찾기
위한 단위이고 답을 주는 단위는 절이기 때문이다. `retrieval.search()` 가 이미
절 단위로 하나씩만 돌려주므로 여기서는 길이만 자른다.

## 확인할 수 없으면 확인할 수 없다고 한다

대회 규칙 4번이다. 공시에 없는 것을 지어내지 않는다. 이건 감점이 아니라
평가 항목(지표 7)이라 프롬프트에 명시하고 출력 검증에서 한 번 더 본다.

## D2·D5 해석을 답변에 밝힌다

`DECISIONS.md` 2026-08-31 두 건이다. "최근" 을 3개년으로 읽었다는 것과,
연도를 정기공시는 회계연도로 수시공시는 접수일로 읽었다는 것을 답변에 적는다.
사용자가 답이 맞는지 확인할 수 있어야 하기 때문이다.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import query as Q
from hcx import Chat
from retrieval import Corpus, search

MAX_CTX = 6000      # 생성 모델에 넣을 근거의 글자 상한
TOP_K = 8           # 검색 상위 몇 개를 근거 후보로 볼 것인가
MAX_SEC = 4         # 그중 실제로 넣을 절의 수

SYSTEM = """너는 공시 자료만 근거로 답하는 도우미다. 규칙을 반드시 지킨다.

1. 아래 근거에 있는 내용만으로 답한다. 근거에 없으면 지어내지 않는다.
2. 확인할 수 없으면 "공시에서 확인되지 않음" 이라고 분명히 적는다.
3. 주가 전망·투자 추천·매수매도 의견을 내지 않는다.
4. 수치를 말할 때는 근거에 적힌 숫자를 그대로 쓴다. 계산했으면 과정을 밝힌다.
5. 되묻지 않는다. 답할 수 있는 만큼 답하고 못 한 부분을 밝힌다.

출처를 반드시 적는다:
- 각 근거 덩어리는 [근거 N] 또는 [값 N] 으로 시작하고 그 줄에 출처가 적혀 있다.
  예) [근거 1] 삼성전자 사업보고서 (2025.12) · III/6 6. 배당에 관한 사항 · 접수 2026-03-10
- 답변 끝에 "근거:" 로 시작하는 줄을 두고, 실제로 쓴 근거의 머리글 줄을
  글자 하나 바꾸지 말고 그대로 복사해 적는다.
- 여러 근거를 썼으면 줄을 나눠 모두 적는다.
- 출처를 지어내지 않는다. 위 머리글에 없는 보고서 이름이나 절 이름을
  만들어 붙이지 않는다. 특히 목차 번호(III/6 같은 것)를 임의로 쓰지 않는다.
- "자료의 첫 번째 부분" 처럼 가리키는 말로 대신하지 않는다.
- [값 N] 을 근거로 썼으면 그 머리글을 그대로 적는다. 거기에 절 이름을
  덧붙이지 않는다. 표에서 꺼낸 값이라 절이 없다.

답변 형식:
- 결론을 먼저 한두 문장으로
- 그 근거를 이어서
- 답하지 못한 부분이 있으면 왜인지 (범위 밖 · 미공시 · 추출 실패로 구분)
- 마지막 줄에 근거 출처
"""


def build_context(hits, max_ctx: int = MAX_CTX, max_sec: int = MAX_SEC,
                  head: str = "") -> tuple[str, list[dict]]:
    """검색 결과를 생성 모델에 넣을 근거로 다듬는다. (본문, 출처목록).

    `head` 는 표에서 꺼낸 값이다. 있으면 맨 앞에 두고 그만큼 본문 자리를
    줄인다. 값이 뒤에 묻히면 모델이 본문의 다른 숫자를 집을 수 있다.
    """
    parts, used, total = [], [], 0
    if head:
        parts.append(head)
        total += len(head)
    for h in hits[:max_sec]:
        body = (h.text or "").strip()
        room = max_ctx - total
        if room <= 200:
            break
        if len(body) > room:
            body = body[:room] + " …(이하 생략)"
        head = f"[근거 {len(used) + 1}] {h.source()}"
        parts.append(f"{head}\n{body}")
        total += len(body) + len(head)
        used.append({"순위": h.rank, "절": h.title, "경로": h.path,
                     "출처": h.source(), "글자": len(body)})
    return "\n\n".join(parts), used


def verify(answer: str, context: str, used: list[dict] | None = None) -> list[str]:
    """답변을 검사한다. 숫자가 근거에 있는지, 출처를 적었는지.

    출처 검사를 넣은 이유는 과제 자료가 "모든 답변에는 근거 공시를 표시할
    것" 을 요구하기 때문이다. 지시만 주고 확인을 안 하면 모델이 빠뜨려도
    모른다. 실제로 근거에 절 제목만 넣던 때는 "자료의 첫 번째 부분에서
    명시되어 있습니다" 로 끝나는 답이 나왔다.
    """
    import re
    warn = []
    nums = re.findall(r"[\d,]{4,}", answer)
    flat = context.replace(",", "")
    for n in set(nums):
        if n.replace(",", "") not in flat:
            warn.append(f"근거에 없는 수치: {n}")

    if used:
        # 답변이 출처를 하나라도 언급했는가. 보고서명이 들어갔는지로 본다.
        names = {u.get("출처", "") for u in used}
        reports = {p.strip() for s in names for p in s.split("·")}
        hit = any(p and p in answer for p in reports)
        if not hit:
            warn.append("답변에 근거 출처가 안 적혔다")
        # 근거에 없는 보고서를 지어내지 않았는가
        for m in re.findall(r"(사업|반기|분기)보고서\s*\(([\d.]+)\)", answer):
            s = f"{m[0]}보고서 ({m[1]})"
            if not any(s in n for n in names):
                warn.append(f"근거에 없는 보고서: {s}")
        # 근거에 없는 절을 지어내지 않았는가.
        #
        # 실측에서 모델이 매출액을 물었는데 "III/6 6. 배당 관련 사항" 을
        # 출처로 적었다. 보고서명은 맞아서 위 검사를 통과했다. 목차 경로가
        # 근거에 실제로 있었는지 따로 본다.
        paths = {u.get("경로", "") for u in used}
        for p in set(re.findall(r"\b((?:XII|XI|X|IX|VIII|VII|VI|V|IV|III|II|I)"
                                r"(?:/[0-9-]+)*)\b", answer)):
            if p not in paths and not any(p in q for q in paths if q):
                warn.append(f"근거에 없는 절: {p}")
    return warn[:5]


def answer(question: str, question_id: str = "", cp: Corpus | None = None,
           chat: Chat | None = None) -> dict:
    """질의 하나를 처리해 응답 4필드와 실행 기록을 낸다."""
    t0 = time.time()
    trace: list[dict] = []
    mark = [t0]

    def step(name: str, **kw) -> None:
        """단계를 기록하고 그 단계에 걸린 시간을 함께 남긴다."""
        now = time.time()
        trace.append({"단계": name, "초": round(now - mark[0], 1), **kw})
        mark[0] = now

    p = Q.parse(question)
    step("S1 질의 해석", 기업=p.corps, 연도=p.years,
         보고서=p.subtype, 검색어=p.terms)

    if not p.corps:
        return {
            "question_id": question_id, "question": question,
            "retrieved_context": "",
            "think_trace": trace + [{"단계": "중단", "이유": "대상 기업을 못 찾았다"}],
            "answer": "질의에서 대상 기업을 찾지 못했다. 기업명을 밝혀 주면 답할 수 있다.",
        }

    reused = cp is not None
    cp = cp or Corpus(p.corps, subtype=p.subtype or "annual")
    step("S3 후보 구성", 조각=len(cp.rows), 기업=p.corps, 재사용=reused)

    from openai_emb import OpenAIEmbedder, normalize
    got, st_emb = OpenAIEmbedder().embed_many([question])
    if not got:
        return {
            "question_id": question_id, "question": question,
            "retrieved_context": "",
            "think_trace": trace + [{"단계": "중단", "이유": f"질의 임베딩 실패: {st_emb}"}],
            "answer": "검색 준비 단계에서 실패했다.",
        }
    qv = normalize(got[0])
    step("S2 질의 임베딩")
    hits = search(cp, p.corps[0], qv, p.terms, topk=TOP_K, use_path=True)
    step("S7 본문 검색", 찾은_절=len(hits), 상위=[h.title for h in hits[:4]])

    if not hits:
        return {
            "question_id": question_id, "question": question,
            "retrieved_context": "",
            "think_trace": trace + [{"단계": "중단", "이유": "근거를 못 찾았다"}],
            "answer": "공시에서 관련 내용을 확인하지 못했다.",
        }

    # S6 값 조회. 표준 재무 항목을 물었으면 표에서 값을 꺼내 근거 맨 앞에 둔다.
    #
    # 본문 검색을 대신하지 않는다. 숫자는 표에서, 맥락은 본문에서 가져가게
    # 한다. 같은 매출이 본문의 여러 절에 다른 모습으로 나오므로, 어느 값을
    # 골랐는지 분명히 해 두는 편이 낫다.
    import facts as F
    fcs = F.lookup(p.corps[0], F.find_items(question), p.years,
                   F.find_basis(question))
    step("S6 값 조회", 찾은_값=[f.line() for f in fcs] or "없음")

    ctx, used = build_context(hits, head=F.as_context(fcs) if fcs else "")
    for f in fcs:
        used.insert(0, {"순위": 0, "절": f.item, "경로": "표",
                        "출처": f.source(), "글자": len(f.line())})
    step("S9 근거 조립", 쓴_절=used, 글자=len(ctx))

    chat = chat or Chat()
    prompt = (f"[질의]\n{question}\n\n[근거]\n{ctx}\n\n"
              "위 근거만으로 답하라. 근거에 없으면 확인되지 않음이라고 적어라.")
    text, st = chat.ask(prompt, system=SYSTEM, max_tokens=1200)
    step("S10 답변 생성", 상태=st, n429=chat.n_429, 글자=len(text or ""))

    if not text:
        return {
            "question_id": question_id, "question": question,
            "retrieved_context": ctx,
            "think_trace": trace + [{"단계": "중단", "이유": f"생성 실패: {st}"}],
            "answer": "답변 생성에 실패했다.",
        }

    # 출처를 안 적었으면 우리가 붙인다.
    #
    # 과제 자료가 "모든 답변에는 근거 공시를 표시할 것" 을 요구한다. 생성
    # 지시로만 두면 답변이 길어졌을 때 상한에 걸려 출처 줄에 닿기 전에
    # 잘린다. 실측에서 감사위원회 질의가 그랬다.
    #
    # 붙이는 내용은 지어낸 것이 아니라 우리가 실제로 넘긴 근거의 머리글이다.
    warn = verify(text, ctx, used)
    added = False
    if "답변에 근거 출처가 안 적혔다" in warn:
        lines = [u["출처"] for u in used if u.get("출처")]
        if lines:
            text = text.rstrip() + "\n\n근거:\n" + "\n".join(
                f"- {s}" for s in dict.fromkeys(lines))
            added = True
            warn = verify(text, ctx, used)
    step("S11 출력 검증", 경고=warn or "없음", 출처보완=added)
    trace.append({"단계": "완료", "총초": round(time.time() - t0, 1)})

    return {"question_id": question_id, "question": question,
            "retrieved_context": ctx, "think_trace": trace, "answer": text}


if __name__ == "__main__":
    import json
    sys.stdout.reconfigure(encoding="utf-8")
    q = " ".join(sys.argv[1:]) or "삼성전자의 주주환원 정책이 어떻게 되는지 알려줘"
    r = answer(q, "Q-TEST")
    print("=" * 66)
    print(f"질의  {r['question']}")
    print("=" * 66)
    for step in r["think_trace"]:
        print(f"  {json.dumps(step, ensure_ascii=False)[:150]}")
    print("-" * 66)
    print(f"근거  {len(r['retrieved_context']):,}자")
    print("-" * 66)
    print(r["answer"])
