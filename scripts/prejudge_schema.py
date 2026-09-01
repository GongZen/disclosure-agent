"""절 스키마의 낱말을 셋으로 미리 갈라 둔다. 사람이 검토하기 쉽게 하려는 것이다.

`section_schema.csv` 에 `AI뺄것` · `AI남길것` · `AI애매` · `AI근거` 네 칸을 더한다.
사람이 채우는 `뺄것` · `더할것` · `메모` 는 건드리지 않는다.

## 왜 AI 가 먼저 하는가

`I/5 정관에 관한 사항` 한 행을 사람과 AI 가 각각 판정해 봤더니 10개 중 9개가
같았다. 1차를 자동으로 깔고 검토를 받는 편이 빠르다.

다만 먼저 제시하면 검토가 "동의/부동의" 로 좁아진다. 그래서 셋으로 나누고
애매한 것을 넉넉히 남긴다. 규칙을 코드에 두는 것도 같은 이유다. 감이 아니라
규칙이 보여야 반박할 수 있다.

## 판정 규칙

순서대로 본다. 먼저 걸리는 규칙이 이긴다.

    뺌   1  잡음        숫자만 · 한 글자 · 숫자 섞임
         2  STOP        query.STOP 에 있는 말. 검색어 단계에서 이미 빠진다

    남김 3  절 이름     그 절의 이름에 든 낱말. 정관에관한사항 → 정관
         4  높은 확률   확률 40% 이상. 그 낱말이 나오면 이 절일 가능성이 높다

    뺌   5  일반어      아래 GENERIC 목록. 어느 질의에도 붙을 수 있는 말
         6  공통어      작업 대상 5개 절 이상에서 낱말로 뽑힌 말

    애매 7  나머지

잡음과 STOP 을 맨 앞에 둔다. 확률과 무관하게 쓸모가 없기 때문이다. 실제로
`주식의 총수 등` 에서 `c` 가 확률 86% 로 나와 남김으로 갔던 적이 있다.
표 머리글의 `a` · `b` · `c` 다. STOP 은 질의에서 이미 빠지므로 남겨도 안 걸린다.

그 다음이 남김이다. `유형자산` 은 7개 절에 걸쳐 나와 규칙 6에 걸리지만
`유형자산` 절에서는 핵심어다. 절 이름 규칙이 공통어 규칙보다 먼저여야 한다.

규칙 6은 데이터에서 나왔다. 여러 절에서 대표 낱말로 뽑히는 말은 정의상
그 절만의 낱말이 아니다. 실측하면 `공시금액` 24절 · `유동` 18절 ·
`이자` 15절 · `장부금액` 14절 같은 것이 나온다.

`다른 절 이름에 든 낱말` 은 뺌으로 하지 않는다. `I/5` 에서 `이사회` 는
`VI/1` 것으로 보이지만 `총회` 는 정관 변경이 주총 안건이라 이 절에도 정당하다.
둘을 기계가 못 가른다. 그래서 애매로 두고 근거에 어느 절 이름인지만 적는다.

## 규칙이 못 잡는 것

`이유` 는 어느 규칙에도 안 걸려 GENERIC 에 손으로 넣었다. `정관 변경 이유`
칸에서 왔는데 질의의 "이유" 와 뜻이 다르다. 이런 것은 용례를 봐야 안다.

즉 이 스크립트는 명백한 것만 치우는 도구다. 판정은 사람이 한다.
"""
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8")

PROB_KEEP = 40      # 이 확률 이상이면 남김으로 본다
SHARED_MIN = 5      # 이 개수 이상의 절에서 뽑히면 공통어로 본다

# 어느 질의에도 붙을 수 있는 말. 손으로 관리한다.
# 절 이름에 든 낱말은 규칙 4가 먼저 잡으므로 여기 있어도 남는다.
GENERIC = {
    # 시간·순서
    "최근", "이후", "이전", "당기", "전기", "현재", "기준", "시점", "기간",
    "정기", "수시", "매년", "연간", "당해", "해당",
    # 사유·상태
    "이유", "사유", "원인", "결과", "내용", "사항", "경우", "정도", "수준",
    "상태", "현황", "변동", "변경", "발생", "여부", "관련", "기타",
    # 동작
    "개최", "실시", "진행", "완료", "예정", "영위", "보유", "취득", "처분",
    "신설", "폐지", "설정", "적용", "포함", "제외", "확인", "검토",
    # 조직·사람 일반어
    "회사", "당사", "기업", "법인", "임원", "직원", "담당", "책임",
    # 문서·서식
    "보고서", "공시", "자료", "서류", "첨부", "별첨", "참조", "위치", "이동",
    "본문", "상세", "요약", "표시", "기재", "작성",
    # 수량·단위
    "합계", "총계", "소계", "이상", "이하", "미만", "초과", "약", "등",
    # 그 밖
    "전자", "일반", "주요", "각각", "모든", "전체", "부분", "구분", "종류",
}

NOISE = re.compile(r"^[\d.,%]+$|^.$|^\D*\d")


def load_stop() -> set[str]:
    try:
        import query as Q
        return set(Q.STOP)
    except Exception as e:
        print(f"   STOP 을 못 읽었다: {e}")
        return set()


def judge(word: str, prob: int, name: str, stop: set[str],
          shared: dict[str, int], owner: dict[str, str]) -> tuple[str, str]:
    """(판정, 근거) 를 낸다. 규칙 순서는 문서 주석 참조."""
    if NOISE.match(word):
        return "뺌", "잡음"
    if word in stop:
        return "뺌", "STOP"
    if word in name:
        return "남김", "절 이름"
    if prob >= PROB_KEEP:
        return "남김", f"확률 {prob}%"
    if word in GENERIC:
        return "뺌", "일반어"
    if shared.get(word, 0) >= SHARED_MIN:
        return "뺌", f"{shared[word]}개 절 공통"
    if word in owner:
        return "애매", f"{owner[word]} 이름"
    return "애매", ""


def main() -> int:
    p = ROOT / "data" / "eval" / "section_schema.csv"
    with p.open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    stop = load_stop()
    print(f"{len(rows)}행 · STOP {len(stop)}개 · 일반어 {len(GENERIC)}개")

    # 1차 — 낱말이 몇 개 절에서 뽑히는지 센다. 작업 대상만 본다
    shared: dict[str, int] = {}
    for r in rows:
        if not r["작업대상"]:
            continue
        for tok in r["낱말"].split():
            w = tok.rsplit(":", 2)[0]
            shared[w] = shared.get(w, 0) + 1

    # 다른 절의 이름에 든 낱말인지 보려고 이름 목록을 만든다
    owner: dict[str, str] = {}
    for r in rows:
        if not r["작업대상"]:
            continue
        for tok in r["낱말"].split():
            w = tok.rsplit(":", 2)[0]
            if w in r["열쇠"] and w not in owner:
                owner[w] = r["열쇠뜻"]

    tally = {"뺌": 0, "남김": 0, "애매": 0}
    for r in rows:
        name = r["열쇠"] + r["열쇠뜻"].replace(" ", "")
        out = {"뺌": [], "남김": [], "애매": []}
        why: list[str] = []
        for tok in r["낱말"].split():
            w, _s, pr = tok.rsplit(":", 2)
            own = {k: v for k, v in owner.items() if v != r["열쇠뜻"]}
            v, reason = judge(w, int(pr.rstrip("%")), name, stop, shared, own)
            out[v].append(w)
            if reason and v != "남김":
                why.append(f"{w}({reason})")
        for k in tally:
            tally[k] += len(out[k])
        r["AI뺄것"] = ", ".join(out["뺌"])
        r["AI남길것"] = ", ".join(out["남김"])
        r["AI애매"] = ", ".join(out["애매"])
        r["AI근거"] = " ".join(why)

    # 사람 칸이 맨 뒤로 가게 순서를 잡는다
    head = [c for c in rows[0] if not c.startswith("AI")
            and c not in ("뺄것", "더할것", "메모")]
    cols = head + ["AI뺄것", "AI남길것", "AI애매", "AI근거", "뺄것", "더할것", "메모"]
    with p.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    tot = sum(tally.values())
    print(f"\n낱말 {tot:,}개")
    for k, v in tally.items():
        print(f"   {k:<5}{v:>6,}  {v/tot*100:>5.1f}%")
    tg = [r for r in rows if r["작업대상"]]
    n = sum(len(r["AI애매"].split(", ")) if r["AI애매"] else 0 for r in tg)
    print(f"\n작업 대상 {len(tg)}행의 애매 낱말 {n:,}개 · 행당 평균 {n/len(tg):.1f}개")
    return 0


if __name__ == "__main__":
    sys.exit(main())
