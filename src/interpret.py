# -*- coding: utf-8 -*-
"""S1 — 질의 해석.

자연어 질의를 정해진 아홉 필드의 JSON 으로 바꾼다. 뒤 단계가 전부 이 결과에 의존한다.

모델 호출은 CLOVA Studio 의 Structured Outputs 로 한다. 스키마를 주면 모델이 그
형태로만 답하게 강제하는 기능이다. 호출부는 따로 떼어 두었으므로 키가 없어도
스키마 검사 · 프롬프트 조립 · 응답 파싱 · S2 연결까지는 그대로 돌려볼 수 있다.

실행
    python src/interpret.py --check          저장된 예시로 전수 점검. 키 없이 된다
    python src/interpret.py "질의"            모델을 호출한다. CLOVA_API_KEY 필요
"""

from __future__ import annotations

import json
import os
import sys

__all__ = ["SCHEMA", "FEWSHOT", "build_prompt", "validate", "parse", "interpret"]

# ── 값 목록 ────────────────────────────────────────────────────────────────
#
# 목록을 주는 이유는 둘이다. Structured Outputs 가 이 안에서만 고르게 강제하고,
# 각 값을 라우팅 대상과 1:1 로 붙일 수 있다. 목록이 없으면 모델이 "정리" "요약"
# "설명" 을 섞어 내놓고 뒤 단계가 분기를 못 한다.

의도값 = ("조회", "비교", "증감", "정리", "존재확인", "추론")
유형값 = ("정량", "정성")
형식값 = ("open", "closed")
기준값 = ("연결", "별도")
판본값 = ("최신", "원본대조")

# 의도와 라우팅 대상의 대응. S1 이 존재하는 이유가 이 표다 (docs/PIPELINE.md S1).
# 다만 의도만으로 다 갈리지는 않는다. 항목의 유형이 정량이면 S6, 정성이면 S7 이다.
라우팅 = {
    "조회": "S6",
    "비교": "S6 · S8",
    "증감": "S6 · S8",
    "정리": "S7",
    "존재확인": "S3 · 존재 판정",
    "추론": "S7 · S9",
}

# ── 스키마 ─────────────────────────────────────────────────────────────────
#
# docs/PIPELINE.md S1 의 여섯 필드에서 출발해 실제 평가 문항 16개로 재며 넓혔다.
# 여섯 필드일 때 온전히 표현된 것이 16개 중 4개였고, 아홉 필드에서 11개다
# (실측 2026-08-19). 넓힌 지점과 이유는 docs/proposal/W7_STATUS.md 에 있다.
#
# 항목을 문자열이 아니라 {이름, 유형} 쌍의 배열로 둔 것이 핵심이다.
# "실적 변화와 그 배경" 처럼 한 질의에 정량과 정성이 섞이는 경우가 16개 중 4개였다.
# 이름 배열과 유형 배열을 따로 두면 순서가 어긋날 때 배경이 정량이 되어버린다.

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["기업", "기간", "항목", "의도", "형식", "기준", "조건", "문서종류", "판본"],
    "properties": {
        # 질의에 나온 이름. 회사인지 분류(섹터·산업·시장)인지는 S2 가 판정하므로
        # 여기서 구분하지 않는다. 다섯 컬럼 값이 서로 겹치지 않아 판별된다.
        "기업": {"type": "array", "items": {"type": "string"}},

        # "2025" · "2025-03" · "2023-01-20". 여러 개면 전부 넣는다.
        "기간": {"type": "array", "items": {"type": "string"}},

        # 무엇을 묻는가. 유형이 S6 로 갈지 S7 로 갈지를 가른다.
        "항목": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["이름", "유형"],
                "properties": {
                    "이름": {"type": "string"},
                    "유형": {"type": "string", "enum": list(유형값)},
                },
            },
        },

        "의도": {"type": "string", "enum": list(의도값)},

        # 한 질의에 "있는가?" 와 "있으면 설명해줘" 가 같이 오는 경우가 있어 배열이다.
        "형식": {"type": "array", "items": {"type": "string", "enum": list(형식값)}},

        # 연결과 별도. 명시가 없으면 null 이고 기본값은 D4 가 정해지면 채운다.
        "기준": {"type": ["string", "null"], "enum": list(기준값) + [None]},

        # 걸러야 할 조건. "연체 90일 이상" 처럼 S8 이 필터로 쓴다.
        # 항목 이름 안에 녹이면 코드가 꺼낼 수 없어 따로 뺐다.
        "조건": {"type": "array", "items": {"type": "string"}},

        "문서종류": {"type": "array", "items": {"type": "string"}},

        # 원본대조면 S4 판본 해소를 켠다. "최초 결정과 비교해" 같은 질의다.
        "판본": {"type": "string", "enum": list(판본값)},
    },
}

# ── few-shot ───────────────────────────────────────────────────────────────
#
# PLAN.md W7 이 "few-shot 예시를 넣기에 가장 값어치가 큰 지점" 이라고 한 곳이다.
# 자체 평가지 16문항 중 서로 다른 유형 7개를 골랐다. 고른 기준은 아래다.
#
#   단순 조회 · 두 기업 비교 · 기간이 둘인 증감 · 정성 · 조건이 둘 ·
#   항목이 셋이고 정량정성 혼재 · 판본 대조

FEWSHOT = [
    ("삼성전자의 2025년 연결기준 매출액은 얼마인가?",
     {"기업": ["삼성전자"], "기간": ["2025"], "항목": [{"이름": "매출액", "유형": "정량"}],
      "의도": "조회", "형식": ["closed"], "기준": "연결", "조건": [],
      "문서종류": ["사업보고서"], "판본": "최신"}),

    ("삼성전자와 SK하이닉스 중 2025년 연결 영업이익이 더 큰 기업은 어디인가?",
     {"기업": ["삼성전자", "SK하이닉스"], "기간": ["2025"],
      "항목": [{"이름": "영업이익", "유형": "정량"}],
      "의도": "비교", "형식": ["closed"], "기준": "연결", "조건": [],
      "문서종류": ["사업보고서"], "판본": "최신"}),

    ("LG에너지솔루션의 연결 매출액은 2023년 대비 2025년에 몇 퍼센트 변동했는가?",
     {"기업": ["LG에너지솔루션"], "기간": ["2023", "2025"],
      "항목": [{"이름": "매출액", "유형": "정량"}],
      "의도": "증감", "형식": ["closed"], "기준": "연결", "조건": [],
      "문서종류": ["사업보고서"], "판본": "최신"}),

    ("jyp에서 전속 연예인들에 대해서 어떠한 회계계정을 적용하여 회계처리 하는지 알아봐줘.",
     {"기업": ["jyp"], "기간": [],
      "항목": [{"이름": "전속 연예인 회계처리", "유형": "정성"}],
      "의도": "조회", "형식": ["open"], "기준": None, "조건": [],
      "문서종류": ["사업보고서"], "판본": "최신"}),

    ("코스닥 기업들 중에서 90일 이상 연체된 채권의 비율이 전체 채권의 0.5% 이상인 기업은?",
     {"기업": ["코스닥"], "기간": [],
      "항목": [{"이름": "연체채권비율", "유형": "정량"}],
      "의도": "조회", "형식": ["open"], "기준": None,
      "조건": ["연체 90일 이상", "비율 0.5% 이상"],
      "문서종류": ["사업보고서"], "판본": "최신"}),

    ("전력기기 산업의 매출액과 영업이익을 비교해주고, 해당 산업에 속한 기업들의 주요 사업의 차이에 대해서 설명해줘.",
     {"기업": ["전력기기"], "기간": [],
      "항목": [{"이름": "매출액", "유형": "정량"},
             {"이름": "영업이익", "유형": "정량"},
             {"이름": "주요 사업 차이", "유형": "정성"}],
      "의도": "비교", "형식": ["closed", "open"], "기준": "연결", "조건": [],
      "문서종류": ["사업보고서"], "판본": "최신"}),

    ("한화에어로스페이스가 2025년 3월에 결정한 유상증자는 최종적으로 어떤 조건으로 확정되었는지, 최초 결정과 비교해 설명해줘.",
     {"기업": ["한화에어로스페이스"], "기간": ["2025-03"],
      "항목": [{"이름": "유상증자", "유형": "정량"}],
      "의도": "비교", "형식": ["open"], "기준": None, "조건": [],
      "문서종류": ["주요사항보고서"], "판본": "원본대조"}),
]

_지침 = """너는 공시 질의를 구조화하는 해석기다. 질의를 아래 아홉 필드로만 바꾼다.

기업      질의에 나온 이름을 그대로 넣는다. 회사인지 산업인지 판단하지 않는다
기간      "2025" "2025-03" "2023-01-20". 여러 개면 전부 넣는다
항목      묻는 대상을 {이름, 유형} 으로 넣는다. 유형은 정량이면 표에서 꺼낼 숫자,
          정성이면 본문을 읽어야 나오는 서술이다. 대상이 여럿이면 여럿 넣는다
의도      조회 비교 증감 정리 존재확인 추론 중 하나
형식      closed 는 값 하나로 끝나는 답, open 은 서술형. 둘 다면 둘 다 넣는다
기준      연결 또는 별도. 질의에 없으면 null
조건      걸러야 할 요건. "연체 90일 이상" 처럼 항목과 분리해서 넣는다
문서종류   근거를 특정 문서로 한정했으면 넣는다
판본      "최초 결정과 비교" 처럼 원본과 정정본을 대조해야 하면 원본대조, 아니면 최신

없는 값을 지어내지 않는다. 질의에 없으면 빈 배열이나 null 이다."""


def build_prompt(질의: str) -> str:
    """모델에 보낼 프롬프트를 만든다. 지침 · 예시 · 질의 순이다."""
    parts = [_지침, ""]
    for q, j in FEWSHOT:
        parts.append(f"질의: {q}")
        parts.append(f"출력: {json.dumps(j, ensure_ascii=False)}")
        parts.append("")
    parts.append(f"질의: {질의}")
    parts.append("출력:")
    return "\n".join(parts)


def validate(obj) -> list[str]:
    """스키마 위반을 목록으로 돌려준다. 빈 목록이면 통과다.

    예시가 스키마를 못 지키면 모델도 못 지킨다. --check 가 예시부터 검사하는 이유다.
    """
    bad: list[str] = []
    if not isinstance(obj, dict):
        return ["최상위가 객체가 아님"]

    필수 = set(SCHEMA["required"])
    if set(obj) != 필수:
        bad.append(f"필드 불일치: 남는 것 {sorted(set(obj) - 필수)} 빠진 것 {sorted(필수 - set(obj))}")

    for k in ("기업", "기간", "조건", "문서종류", "형식", "항목"):
        if k in obj and not isinstance(obj[k], list):
            bad.append(f"{k} 가 배열이 아님")

    항목 = obj.get("항목")
    if isinstance(항목, list):
        for a in 항목:
            if not isinstance(a, dict) or set(a) != {"이름", "유형"}:
                bad.append(f"항목 원소 형태 오류: {a}")
            elif a["유형"] not in 유형값:
                bad.append(f"항목 유형 밖: {a['유형']}")

    if obj.get("의도") not in 의도값:
        bad.append(f"의도 밖: {obj.get('의도')}")

    형식 = obj.get("형식")
    if isinstance(형식, list):
        for x in 형식:
            if x not in 형식값:
                bad.append(f"형식 밖: {x}")

    if obj.get("기준") is not None and obj.get("기준") not in 기준값:
        bad.append(f"기준 밖: {obj.get('기준')}")
    if obj.get("판본") not in 판본값:
        bad.append(f"판본 밖: {obj.get('판본')}")
    return bad


def parse(text: str) -> dict:
    """모델 응답에서 JSON 을 꺼낸다. 앞뒤에 말을 붙여 오는 경우를 대비한다."""
    text = text.strip()
    i, j = text.find("{"), text.rfind("}")
    if i < 0 or j < 0:
        raise ValueError("응답에 JSON 이 없다")
    return json.loads(text[i:j + 1])


def _call_clova(prompt: str) -> str:
    """CLOVA Studio 호출. 여기만 바꾸면 된다.

    HyperCLOVA X 외의 생성 모델을 넣지 않는다. 넣으면 평가 대상에서 제외된다
    (CLAUDE.md 절대 금지 1).
    """
    key = os.environ.get("CLOVA_API_KEY")
    if not key:
        raise RuntimeError(
            "CLOVA_API_KEY 가 없다. 스키마·프롬프트·파싱 점검은 --check 로 키 없이 된다.")
    raise NotImplementedError("CLOVA Studio 호출부 미구현. 키를 받은 뒤 채운다.")


def interpret(질의: str, call=None) -> dict:
    """질의 하나를 해석한다. call 을 주면 그것을 쓰고 없으면 CLOVA 를 부른다."""
    prompt = build_prompt(질의)
    raw = (call or _call_clova)(prompt)
    obj = parse(raw)
    bad = validate(obj)
    if bad:
        raise ValueError(f"스키마 위반: {bad}")
    return obj


def _check() -> int:
    """키 없이 되는 점검. 예시의 스키마 적합 · 파싱 왕복 · S2 연결을 본다."""
    실패 = 0

    print("1. 저장된 예시가 스키마를 지키는가")
    for q, j in FEWSHOT:
        bad = validate(j)
        print(f"   {'통과' if not bad else '실패'}  {q[:46]}")
        if bad:
            print(f"          {bad}")
            실패 += 1

    print("\n2. 프롬프트 조립과 파싱 왕복")
    for q, j in FEWSHOT:
        try:
            back = parse("설명을 덧붙이면 " + json.dumps(j, ensure_ascii=False) + " 입니다")
            ok = back == j
        except Exception as e:
            ok, back = False, e
        print(f"   {'통과' if ok else '실패'}  {q[:46]}")
        if not ok:
            실패 += 1
    print(f"   프롬프트 길이 {len(build_prompt('시험')):,}자")

    print("\n3. 깨진 응답을 막는가")
    나쁜예 = [
        ("필드 누락", {"기업": [], "기간": []}),
        ("의도 밖", {**FEWSHOT[0][1], "의도": "요약"}),
        ("항목 형태", {**FEWSHOT[0][1], "항목": ["매출액"]}),
        ("판본 밖", {**FEWSHOT[0][1], "판본": "아무거나"}),
    ]
    for 이름, o in 나쁜예:
        잡힘 = bool(validate(o))
        print(f"   {'통과' if 잡힘 else '실패'}  {이름} 를 잡아냄")
        if not 잡힘:
            실패 += 1

    print("\n4. 해석 결과가 S2 로 넘어가는가")
    try:
        from match import match_targets
        이름들 = [n for _, j in FEWSHOT for n in j["기업"]]
        for r in match_targets(이름들):
            상태 = (f"{r['kind']} / {r['matched_by']} / {len(r['companies'])}개사"
                  if r["status"] == "resolved" else r["status"])
            표시 = "통과" if r["status"] != "out_of_scope" else "범위밖"
            print(f"   {표시}  {r['raw']:<18} {상태}")
    except Exception as e:
        print(f"   실패  S2 연결 불가: {e}")
        실패 += 1

    print("\n5. 의도별 라우팅 대응")
    for k, v in 라우팅.items():
        print(f"   {k:<8} → {v}")

    print(f"\n{'전부 통과' if 실패 == 0 else f'실패 {실패}건'}")
    return 실패


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] == "--check":
        sys.exit(_check())
    try:
        print(json.dumps(interpret(args[0]), ensure_ascii=False, indent=2))
    except (RuntimeError, NotImplementedError) as e:
        print(f"{e}\n\n조립된 프롬프트\n{'-' * 60}\n{build_prompt(args[0])}")
        sys.exit(1)
