# -*- coding: utf-8 -*-
"""S1 질의 해석. 사람이 던진 문장을 필터와 검색어로 가른다.

## 왜 필요한가

질의 문장을 통째로 검색어로 쓰면 엉뚱한 것이 걸린다. 평가 질의로 실측한
결과다.

    질의   "SK하이닉스가 어떤 사업을 하는 회사인지 사업보고서 기준으로 알려줘"
    1위    IV/1  1. 예측정보에 대한 주의사항
           그 절의 본문이 "본 사업보고서의 …" 로 시작한다

`사업보고서` 는 어느 문서를 볼지 정하는 조건이지 찾을 낱말이 아니다. 그런데
문장 전체를 검색어로 쓰니 그 낱말이 든 절이 걸렸다. 평가 질의 8개 중 3개가
이 이유로 실패했다.

    질의를 그대로 쓸 때   1위 적중 25% · 8위 안 50%

## 무엇을 가르는가

    필터    어느 문서를 볼 것인가
              기업 · 업종 · 시장 · 연도 · 보고서 종류 · 날짜

    의도    어떻게 답할 것인가
              비교 · 정리 · 계산 · 존재 확인
              검색어로는 안 쓰지만 W8 에서 답변 방식을 정할 때 쓴다

    검색어   무엇을 찾을 것인가
              위 둘을 뺀 나머지

## 왜 규칙으로 하는가

평가 질의 28개로 재보니 규칙만으로 27개에서 대상을 뽑아냈다. 기업명이
안 나오는 질의도 업종이나 시장으로 걸린다.

    "전력기기 산업"           → sector = 전력기기
    "코스닥 기업들 중에서"     → market = KOSDAQ
    "필수소비재 산업"          → sector = 소비재·유통

규칙은 빠르고 재현되며 비용이 없다. 생성 모델을 부르면 질의마다 호출이
하나 늘고 같은 질의에 다른 답이 나올 수 있다.

다만 규칙으로 안 되는 것이 있다.

    "어떤 사업을 하는" → "사업의 개요"        용어 확장
    "전속 연예인" → "전속계약금" → "무형자산"   개념 연결

이건 사전이나 생성 모델이 필요하다. 아직 안 만들었다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

__all__ = ["Query", "parse"]

# ── 문서 종류 ────────────────────────────────────────────────────────
# 질의에 나오는 말 → document.doc_subtype
# 이 낱말들은 필터로 옮기고 검색어에서 뺀다. 거의 모든 절에 나와서
# 검색어로 쓰면 변별력이 없다.
SUBTYPE = {
    "사업보고서": "annual", "연간보고서": "annual",
    "반기보고서": "half", "반기": "half",
    "분기보고서": "quarter", "분기": "quarter",
}
# 정기공시가 아닌 것을 가리키는 말. doc_group 으로 좁힌다.
DOCGROUP = {
    "공급계약": "exchange", "공시": None,
    "주요사항보고서": "major", "대량보유": "holding",
    "지분공시": "holding", "거래소공시": "exchange",
}

# ── 감사보고서를 열어야 하는 질의인가 ──────────────────────────────────
#
# 감사보고서는 기본으로 안 뒤진다. 검색을 나쁘게 만들기 때문이다. 실측에서
# 질의 30개 중 20개(67%)의 상위 8위 안에 감사보고서 조각이 끼어들었고,
# 빼니 1위 적중이 14/37 에서 17/37 로 올랐다.
#
# 그런데 감사보고서에만 있는 내용이 있다. 아래 말이 질의에 나오면 연다.
#
#     주요 감사실시내용        사업보고서 0조각 · 감사보고서 32조각
#     감사인의 중요성 금액      사업보고서 0조각 · 감사보고서 4조각
#     감사보고서 원문          감사보고서 463조각
#     감사참여자 상세          사업보고서 2조각 · 감사보고서 415조각
#
# 목록을 좁게 잡았다. "감사" 나 "감사위원회" 로 열면 안 된다. 그 질의들의
# 답은 사업보고서에 있다. 실측에서 "감사위원회 구성" 은 VI/2 감사제도에
# 관한 사항이, "외부감사인과 감사보수" 는 V/1 외부감사에 관한 사항이
# 정답이었고, 둘 다 감사보고서를 빼야 순위가 올랐다.
#
# 감사의견·핵심감사사항도 넣지 않았다. 사업보고서 쪽에 더 많다.
# 핵심감사사항이 사업보고서 1,653조각 대 감사보고서 764조각이다.
AUDIT_WORDS = (
    "감사보고서", "감사 보고서",
    "감사실시내용", "감사 실시내용", "감사실시 내용",
    "감사절차", "감사 절차",
    "감사인의 중요성", "중요성 금액",
    "감사참여자", "감사 참여자",
    "감사투입인원", "감사 투입인원",
    "감사인의 감사", "감사수행", "감사 수행",
)

# 낱말 하나로는 못 잡는 표현. 두 조각이 한 문장에 같이 있으면 연다.
#
# "감사인이 어떤 절차로 감사했는지" 같은 물음이 이렇다. 낱말 목록에
# "감사절차" 를 넣어도 띄어쓰기와 어미가 달라 안 걸린다. 그렇다고 "절차"
# 하나로 열면 감사와 무관한 질의까지 열린다.
AUDIT_PAIRS = (
    ("감사인", "절차"), ("감사인", "방법"), ("감사인", "수행"),
    ("감사", "투입"), ("감사", "실시내용"),
)


def want_audit(text: str) -> bool:
    """질의가 감사보고서 자체를 겨냥하는가.

    좁게 잡는다. "감사" 나 "감사위원회" 로 열면 안 된다. 그 질의들의 답은
    사업보고서 V/1 외부감사에 관한 사항과 VI/2 감사제도에 관한 사항에 있고,
    감사보고서를 열면 오히려 순위가 떨어진다는 것을 실측했다.
    """
    if any(w in text for w in AUDIT_WORDS):
        return True
    return any(a in text and b in text for a, b in AUDIT_PAIRS)


# ── 질의 의도 ────────────────────────────────────────────────────────
# 검색어로는 안 쓰지만 버리지도 않는다. 답변 방식을 정하는 정보다.
INTENT = [
    ("비교", r"비교|차이|다른지|어느 (쪽|기업|회사)|중에서 (더|어디)"),
    ("정리", r"정리해|모두 알려|내역을|현황을|목록"),
    ("계산", r"몇 퍼센트|얼마나 (늘|줄|증가|감소)|대비|비중|성향"),
    ("존재확인", r"있는가|있어|존재하는가|찾아줘|어디가 있"),
    ("설명", r"설명해|어떻게 되는지|어떤|무엇인지|알려줘|알아봐"),
    ("단일값", r"얼마인가|얼마야|언제|누구"),
]

# ── 검색어에서 뺄 말 ──────────────────────────────────────────────────
# 질의에만 나오고 문서에는 거의 없거나, 있어도 다른 뜻인 말이다.
#
#   알리 · 정리 · 설명   "알려줘" · "정리해줘" 의 어간
#                        문서에 거의 없어 IDF 가 높다. 우연히 든 조각이 튀어오른다
#   기준 · 근거 · 대하   "기준으로" · "근거로" · "대해서"
#                        거의 모든 절에 있어 잡음만 는다
STOP = {
    "알리", "알", "정리", "설명", "확인", "비교", "알아보", "찾",
    "기준", "근거", "대하", "관하", "위하", "통하", "따르",
    "얼마", "무엇", "어디", "언제", "누구", "어떻", "어떤",
    "보고서", "공시", "내용", "사항", "경우", "정도", "수준",
    "주세요", "바랍니다", "싶다", "하다", "되다", "있다", "없다",
}


@dataclass
class Query:
    """해석된 질의. 필터와 검색어를 나눠 담는다."""
    raw: str
    corps: list[str] = field(default_factory=list)
    sectors: list[str] = field(default_factory=list)
    markets: list[str] = field(default_factory=list)
    years: list[int] = field(default_factory=list)
    dates: list[str] = field(default_factory=list)
    subtype: str | None = None
    doc_group: str | None = None
    intents: list[str] = field(default_factory=list)
    terms: list[str] = field(default_factory=list)
    search_text: str = ""
    doc_groups: tuple[str, ...] = ("periodic",)   # 어떤 문서를 뒤질 것인가

    def summary(self) -> str:
        p = []
        if self.corps:
            p.append(f"기업 {'·'.join(self.corps)}")
        if self.sectors:
            p.append(f"업종 {'·'.join(self.sectors)}")
        if self.markets:
            p.append(f"시장 {'·'.join(self.markets)}")
        if self.years:
            p.append(f"연도 {'·'.join(map(str, self.years))}")
        if self.subtype:
            p.append(f"종류 {self.subtype}")
        if self.intents:
            p.append(f"의도 {'·'.join(self.intents)}")
        return " | ".join(p) or "(필터 없음)"


@lru_cache(maxsize=1)
def _universe():
    """기업 이름·별칭·업종·시장 목록. 긴 이름부터 찾아야 한다.

    "삼성전자" 와 "삼성전기" 처럼 앞부분이 겹치는 이름이 있어,
    짧은 것부터 찾으면 잘못 잡는다.
    """
    from corpus import load_universe
    u = load_universe()
    name2corp: dict[str, str] = {}
    for _, r in u.iterrows():
        for c in ("corp_name", "listed_name"):
            v = r.get(c)
            if isinstance(v, str) and v.strip():
                name2corp[v.strip()] = r["corp_name"]
    # 코퍼스에 없는 통칭. 사람이 흔히 쓰는 말이다.
    for alias, real in {
        "엔씨소프트": "NC", "엔씨": "NC", "네이버": "NAVER",
        "현대차": "현대자동차", "기아차": "기아", "하이브": "하이브",
        "JYP": "JYP Ent", "jyp": "JYP Ent", "제이와이피": "JYP Ent",
        "포스코": "POSCO홀딩스", "케이비금융": "KB금융",
        "엘지에너지솔루션": "LG에너지솔루션", "엘지화학": None,
        "한화에어로": "한화에어로스페이스", "삼바": "삼성바이오로직스",
    }.items():
        if real and real in set(u["corp_name"]):
            name2corp.setdefault(alias, real)
    names = sorted(name2corp, key=len, reverse=True)
    sectors = sorted(u["sector"].dropna().unique(), key=len, reverse=True)
    markets = sorted(u["market"].dropna().unique())
    return name2corp, names, sectors, markets


# 업종을 부르는 다른 말. universe 의 sector 값과 이어 준다.
SECTOR_ALIAS = {
    "필수소비재": "소비재·유통", "소비재": "소비재·유통",
    "유통": "소비재·유통", "식품": "소비재·유통",
    "제약": "바이오·제약", "바이오": "바이오·제약",
    "반도체": "반도체·전자부품", "전자부품": "반도체·전자부품",
    "자동차": "자동차·모빌리티", "모빌리티": "자동차·모빌리티",
    "금융": "금융·보험", "보험": "금융·보험", "은행": "금융·보험",
    "방산": "방산·항공우주", "항공우주": "방산·항공우주",
    "물류": "운송·물류", "운송": "운송·물류",
    "엔터": "엔터테인먼트", "플랫폼": "AI소프트웨어·플랫폼",
    "소프트웨어": "AI소프트웨어·플랫폼",
}
MARKET_ALIAS = {"코스닥": "KOSDAQ", "코스피": "KOSPI",
                "유가증권": "KOSPI", "유가증권시장": "KOSPI"}


def _find_years(text: str) -> list[int]:
    """연도를 뽑는다. "2025년" · "25년" · "2025." 을 다 잡는다.

    접수번호(20260310002820)에 들어 있는 숫자를 연도로 오인하지 않도록
    앞뒤로 숫자가 붙지 않은 것만 본다.
    """
    out = []
    for m in re.finditer(r"(?<!\d)(20\d\d)(?!\d)", text):
        out.append(int(m.group(1)))
    for m in re.finditer(r"(?<!\d)(\d\d)년", text):
        v = int(m.group(1))
        if 20 <= v <= 30:
            out.append(2000 + v)
    return sorted(set(out))


def parse(text: str) -> Query:
    """질의를 필터·의도·검색어로 가른다."""
    from search import tokenize

    name2corp, names, sectors, markets = _universe()
    q = Query(raw=text)
    rest = text

    # 1  기업. 긴 이름부터 찾아 지운다
    #
    # 이름 뒤에 붙은 조사도 함께 지운다. "삼성전자가" 에서 이름만 빼면
    # 남은 "가" 가 다음 낱말에 붙어 "가 공시한" → "가공시" 로 잘린다.
    for n in names:
        if n in rest:
            c = name2corp[n]
            if c not in q.corps:
                q.corps.append(c)
            rest = re.sub(re.escape(n) + r"(이|가|은|는|을|를|의|와|과|에서|에|도|만)?",
                          " ", rest)

    # 2  업종·시장
    #
    # 업종은 "산업" · "업종" · "기업" 이 뒤에 붙을 때만 인정한다. 그러지
    # 않으면 "반도체 위탁생산" 같은 제품 설명이 업종 필터로 잡힌다.
    # 실제로 문항 2 에서 그렇게 잘못 잡혔다.
    for s in sectors:
        if re.search(re.escape(s) + r"\s*(산업|업종|업체|기업|회사|섹터|분야)", rest):
            q.sectors.append(s)
            rest = rest.replace(s, " ")
    for a, real in SECTOR_ALIAS.items():
        if re.search(re.escape(a) + r"\s*(산업|업종|업체|기업|회사|섹터|분야)", rest) \
                and real not in q.sectors:
            q.sectors.append(real)
            rest = rest.replace(a, " ")
    for a, real in MARKET_ALIAS.items():
        if a in rest and real not in q.markets:
            q.markets.append(real)
            rest = rest.replace(a, " ")
    for m in markets:
        if m in rest and m not in q.markets:
            q.markets.append(m)
            rest = rest.replace(m, " ")

    # 3  날짜와 연도
    for m in re.finditer(r"20\d\d년\s*\d{1,2}월\s*\d{1,2}일", text):
        q.dates.append(m.group(0))
    q.years = _find_years(text)
    rest = re.sub(r"(?<!\d)20\d\d(?!\d)년?", " ", rest)
    rest = re.sub(r"(?<!\d)\d\d년", " ", rest)
    rest = re.sub(r"\d{1,2}월\s*\d{1,2}일", " ", rest)

    # 4  문서 종류. 필터로 옮기고 검색어에서 뺀다
    #
    # "상반기" · "하반기" 는 기간을 가리키지 반기보고서를 뜻하지 않는다.
    # "2024년 상반기에 공시한" 은 그 기간에 나온 거래소공시를 묻는 것이다.
    # 그래서 "보고서" 가 붙은 것만 문서 종류로 본다.
    for w, st in SUBTYPE.items():
        if w.endswith("보고서") and w in rest:
            q.subtype = q.subtype or st
            rest = rest.replace(w, " ")
    if q.subtype is None:
        for w, st in SUBTYPE.items():
            if not w.endswith("보고서") and re.search(
                    r"(?<![상하])" + re.escape(w) + r"(?!기)", rest):
                q.subtype = st
                rest = re.sub(r"(?<![상하])" + re.escape(w) + r"(?!기)", " ", rest)
                break
    for w, dg in DOCGROUP.items():
        if w in rest and dg:
            q.doc_group = q.doc_group or dg
    # "사업보고서" 같은 합성어만 지운다. 낱개 "보고서" · "공시" 는 STOP 이
    # 맡는다. 두 곳에서 같은 일을 하면 STOP 을 바꿔도 효과가 없어
    # 어느 규칙이 듣는지 잴 수 없다. 실제로 그 상태에서 갈래별 비교를
    # 돌렸다가 모든 설정이 같은 결과를 내 원인을 못 찾을 뻔했다.
    rest = re.sub(r"사업보고서|반기보고서|분기보고서", " ", rest)

    # 5  의도. 원문에서 본다. 지우지 않는다
    for name, pat in INTENT:
        if re.search(pat, text):
            q.intents.append(name)

    # 6  남은 것에서 검색어를 뽑는다
    q.search_text = re.sub(r"\s+", " ", rest).strip()
    q.terms = [t for t in tokenize(q.search_text) if t not in STOP]

    # 7  감사보고서를 열 것인가. 원문에서 본다.
    #    4번에서 "감사보고서" 를 지우지 않으므로 여기서 볼 수 있다.
    if want_audit(text):
        q.doc_groups = ("periodic", "audit")
    return q
