"""정형 표에서 값을 꺼낸다. 출처를 함께 낸다.

## 왜 따로 두는가

"매출액이 얼마인가" 는 숫자 하나를 정확히 답해야 한다. 본문에서 찾으면
같은 매출이 여러 절에 다른 모습으로 나와 어느 것을 골랐는지 알기 어렵다.
`build_fs.py` 가 XBRL 에서 미리 뽑아 둔 `fact_financial` 을 조회하면
값이 하나로 정해지고 출처도 확실하다.

본문 검색을 대신하지 않는다. 값을 근거에 하나 더 얹는다. 생성 모델이
숫자는 이쪽에서, 맥락은 본문에서 가져가게 한다.

## 어느 행을 고르나 — 중복이 많다

같은 (기업·항목·연도·기준) 조합에 행이 여럿이다. 9,597 조합 중 8,002 에
중복이 있다. 분기보고서·반기보고서·사업보고서가 같은 연도의 값을 각각
싣기 때문이다.

    삼성전자 revenue 2025 연결
       분기보고서(2025.03)  quarter      79,140,503 백만원
       반기보고서(2025.06)  cumulative  153,706,820 백만원
       분기보고서(2025.09)  cumulative  239,768,567 백만원
       사업보고서(2025.12)  annual      333,605,938 백만원   ← 연간

`period_type` 이 갈라 준다. 연간을 물으면 `annual` 을 골라야 한다.
그냥 최신 행을 집으면 분기 누적을 연간이라고 답하게 된다.

## 연결과 별도

`basis` 칸이 갈라 놓았다. 질의에 어느 쪽인지 없으면 연결을 기본으로 하되
답변에 그렇게 밝힌다. 상장사 재무는 연결이 기본이기 때문이다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["ITEMS", "Fact", "find_items", "lookup", "as_context"]

# 질의에 나오는 말 → 표의 항목 코드.
#
# 긴 것부터 찾는다. "매출" 이 "매출원가" 와 "매출총이익" 안에도 있어서,
# 짧은 것부터 찾으면 매출원가를 물었는데 매출액을 준다.
ITEMS: list[tuple[str, str, str]] = [
    # (질의에 나오는 말, 항목 코드, 사람이 읽는 이름)
    ("법인세비용차감전순이익", "pretax_income", "법인세비용차감전순이익"),
    ("영업활동현금흐름", "cf_operating", "영업활동현금흐름"),
    ("투자활동현금흐름", "cf_investing", "투자활동현금흐름"),
    ("재무활동현금흐름", "cf_financing", "재무활동현금흐름"),
    ("영업활동 현금흐름", "cf_operating", "영업활동현금흐름"),
    ("투자활동 현금흐름", "cf_investing", "투자활동현금흐름"),
    ("재무활동 현금흐름", "cf_financing", "재무활동현금흐름"),
    ("판매비와관리비", "sga", "판매비와관리비"),
    ("판매비와 관리비", "sga", "판매비와관리비"),
    ("비유동부채", "noncurrent_liabilities", "비유동부채"),
    ("비유동자산", "noncurrent_assets", "비유동자산"),
    ("매출총이익", "gross_profit", "매출총이익"),
    ("순이자이익", "net_interest_income", "순이자이익"),
    ("순수수료이익", "net_fee_income", "순수수료이익"),
    ("보험서비스결과", "insurance_result", "보험서비스결과"),
    ("당기순이익", "net_income", "당기순이익"),
    ("유형자산 취득", "capex", "유형자산의 취득"),
    ("유형자산취득", "capex", "유형자산의 취득"),
    ("설비투자", "capex", "유형자산의 취득"),
    ("매출원가", "cost_of_sales", "매출원가"),
    ("영업이익", "operating_income", "영업이익"),
    ("자산총계", "total_assets", "자산총계"),
    ("부채총계", "total_liabilities", "부채총계"),
    ("자본총계", "total_equity", "자본총계"),
    ("자기자본", "total_equity", "자본총계"),
    ("유동부채", "current_liabilities", "유동부채"),
    ("유동자산", "current_assets", "유동자산"),
    ("보험수익", "insurance_revenue", "보험수익"),
    ("총자산", "total_assets", "자산총계"),
    ("총부채", "total_liabilities", "부채총계"),
    ("순이익", "net_income", "당기순이익"),
    ("판관비", "sga", "판매비와관리비"),
    ("매출액", "revenue", "매출액"),
    ("영업수익", "revenue", "매출액"),
    ("매출", "revenue", "매출액"),
]
ITEMS.sort(key=lambda x: -len(x[0]))

# 연결·별도를 가리키는 말
BASIS_WORDS = {"연결": "연결", "연결기준": "연결", "연결 기준": "연결",
               "별도": "별도", "별도기준": "별도", "별도 기준": "별도",
               "개별": "별도"}


@dataclass
class Fact:
    """표에서 꺼낸 값 하나. 출처를 함께 들고 다닌다."""

    item: str            # 사람이 읽는 항목 이름
    value: int           # 원 단위
    raw: int | None      # 보고서에 적힌 숫자
    unit: str            # 그 숫자의 단위. 예 "백만원"
    basis: str           # 연결 · 별도
    year: int            # 회계연도
    period: str          # annual · cumulative · quarter
    corp: str
    report: str          # 보고서명
    rcept: str           # 접수일 YYYYMMDD

    def source(self) -> str:
        d = f"{self.rcept[:4]}-{self.rcept[4:6]}-{self.rcept[6:8]}" \
            if len(self.rcept or "") == 8 else ""
        parts = [self.corp, self.report]
        if d:
            parts.append(f"접수 {d}")
        return " · ".join(x for x in parts if x)

    def amount(self) -> str:
        """보고서에 적힌 대로. 원 단위 환산도 함께 보인다."""
        if self.raw is not None and self.unit:
            return f"{self.raw:,} {self.unit} ({self.value:,} 원)"
        return f"{self.value:,} 원"

    def line(self) -> str:
        p = {"annual": "연간", "cumulative": "누적", "quarter": "분기"}.get(
            self.period, self.period or "")
        return (f"{self.corp} {self.year}년 {self.item} ({self.basis}"
                f"{' · ' + p if p else ''}) = {self.amount()}")


def find_items(text: str) -> list[tuple[str, str]]:
    """질의에서 물어본 항목들. (항목 코드, 사람이 읽는 이름)."""
    t = text
    out: list[tuple[str, str]] = []
    seen = set()
    for word, code, label in ITEMS:
        if word in t:
            if code not in seen:
                out.append((code, label))
                seen.add(code)
            # 찾은 말은 지운다. "매출원가" 를 찾은 뒤 "매출" 이 또 걸리지 않게
            t = t.replace(word, " ")
    return out


def find_basis(text: str) -> str | None:
    """질의가 연결인지 별도인지 밝혔는가. 안 밝혔으면 None."""
    for w, b in BASIS_WORDS.items():
        if w in text:
            return b
    return None


def lookup(corp: str, items: list[tuple[str, str]], years: list[int],
           basis: str | None = None, want_annual: bool = True,
           limit: int = 6) -> list[Fact]:
    """표에서 값을 꺼낸다. 없으면 빈 목록.

    연도를 안 주면 그 기업의 가장 최근 회계연도를 쓴다. 기준을 안 주면
    연결을 먼저 찾고 없으면 별도를 쓴다.
    """
    if not items:
        return []
    from db import connect
    con = connect()

    if not years:
        r = con.execute(
            """SELECT MAX(f.fiscal_year) FROM fact_financial f
               JOIN document d ON f.doc_id = d.doc_id
               WHERE d.corp_name = ? AND d.doc_subtype = 'annual'""",
            (corp,)).fetchone()
        years = [r[0]] if r and r[0] else []
    if not years:
        return []

    bases = [basis] if basis else ["연결", "별도"]
    out: list[Fact] = []
    for code, label in items:
        for y in years[:2]:
            got = None
            for b in bases:
                # 연간을 먼저 찾는다. 분기 누적을 연간으로 답하지 않게 한다.
                for cond in (("annual", "annual"), (None, None)):
                    sql = """
                        SELECT f.value, f.value_raw, f.unit_label, f.basis,
                               f.fiscal_year, f.period_type, d.corp_name,
                               d.report_nm, d.rcept_dt
                        FROM fact_financial f
                        JOIN document d ON f.doc_id = d.doc_id
                        WHERE d.corp_name = ? AND f.item_code = ?
                          AND f.fiscal_year = ? AND f.basis = ?
                          AND d.doc_group = 'periodic'"""
                    args = [corp, code, y, b]
                    if want_annual and cond[0]:
                        sql += " AND d.doc_subtype = ? AND f.period_type = ?"
                        args += [cond[0], cond[1]]
                    sql += " ORDER BY d.rcept_dt DESC LIMIT 1"
                    r = con.execute(sql, args).fetchone()
                    if r:
                        got = r
                        break
                if got:
                    break
            if got:
                out.append(Fact(
                    item=label, value=got[0], raw=got[1], unit=got[2] or "",
                    basis=got[3] or "", year=got[4], period=got[5] or "",
                    corp=got[6] or corp, report=got[7] or "",
                    rcept=str(got[8] or "")))
                if len(out) >= limit:
                    return out
    return out


def as_context(facts: list[Fact]) -> str:
    """근거 덩어리로 만든다. 본문 근거와 같은 형식이다."""
    parts = []
    for i, f in enumerate(facts, 1):
        parts.append(f"[값 {i}] {f.source()} · 재무제표 표 조회\n{f.line()}")
    return "\n\n".join(parts)
