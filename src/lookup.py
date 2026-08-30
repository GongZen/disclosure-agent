"""S6 값 조회. fact_financial 에서 숫자를 꺼낸다.

여러 후보가 있으면 하나를 고르지 않고 전부 낸다.

    "삼성전자 2025년 매출액" 을 물으면 후보가 일곱이다.
        3월 누적 79.1조 · 3월 3개월 79.1조
        6월 누적 153.7조 · 6월 3개월 74.6조
        9월 누적 239.8조 · 9월 3개월 86.1조
        12월 연간 333.6조

    어느 것을 뜻하는지 프롬프트만으로 판단하기 어렵다. 임의로 하나를 고르면
    사용자는 그것이 무엇인지 모르는 채로 받는다. 조건을 붙여 다 보여주고
    고르게 하는 편이 낫다. 답변이 길어지는 대신 빠뜨려 틀리는 일이 없다.

기간 유형 넷의 뜻이다.
    instant      어느 시점의 잔액. 재무상태표
    annual       그 해 열두 달. 사업보고서
    cumulative   그 해 첫날부터 그 시점까지 누적
    quarter      그 석 달만
"""
from __future__ import annotations

# item_code → 사람이 쓰는 이름. 답변에 이대로 쓴다.
LABEL = {
    "total_assets": "자산총계", "total_liabilities": "부채총계",
    "total_equity": "자본총계",
    "current_assets": "유동자산", "noncurrent_assets": "비유동자산",
    "current_liabilities": "유동부채", "noncurrent_liabilities": "비유동부채",
    "held_for_sale_assets": "매각예정자산",
    "held_for_sale_liabilities": "매각예정부채",
    "financial_business_assets": "금융업자산",
    "financial_business_liabilities": "금융업부채",
    "revenue": "매출액", "cost_of_sales": "매출원가",
    "gross_profit": "매출총이익", "sga": "판매비와관리비",
    "operating_income": "영업이익", "pretax_income": "법인세비용차감전순이익",
    "net_income": "당기순이익",
    "net_interest_income": "순이자손익", "net_fee_income": "순수수료손익",
    "insurance_result": "보험손익", "insurance_revenue": "보험수익",
    "cf_operating": "영업활동현금흐름", "cf_investing": "투자활동현금흐름",
    "cf_financing": "재무활동현금흐름", "capex": "유형자산 취득(설비투자)",
}

# 질의에 나오는 말 → item_code. 여럿이 걸리면 전부 조회한다.
KEYWORD = {
    "매출": "revenue", "매출액": "revenue", "수익": "revenue",
    "영업이익": "operating_income", "영업손익": "operating_income",
    "순이익": "net_income", "당기순이익": "net_income",
    "자산": "total_assets", "자산총계": "total_assets", "총자산": "total_assets",
    "부채": "total_liabilities", "부채총계": "total_liabilities",
    "자본": "total_equity", "자본총계": "total_equity",
    "유동자산": "current_assets", "유동부채": "current_liabilities",
    "매출원가": "cost_of_sales", "매출총이익": "gross_profit",
    "판관비": "sga", "판매비와관리비": "sga",
    "설비투자": "capex", "유형자산 취득": "capex", "capex": "capex",
    "영업활동현금흐름": "cf_operating", "투자활동현금흐름": "cf_investing",
    "재무활동현금흐름": "cf_financing",
    "순이자": "net_interest_income", "순이자손익": "net_interest_income",
    "순수수료": "net_fee_income", "보험손익": "insurance_result",
    "보험수익": "insurance_revenue",
    "법인세차감전": "pretax_income",
}

_PERIOD_KO = {
    "instant": "시점 잔액", "annual": "연간",
    "cumulative": "누적", "quarter": "3개월",
}

# 배수 → 단위 이름. XBRL 경로는 ADECIMAL 로 배수만 주고 이름을 주지 않는다.
# unit_label 이 20,286건 비어 있는데 unit_mult 는 전부 채워져 있어
# 여기서 되돌린다. 근거를 댈 때 "백만원 단위로 기재" 라고 말하려면 이름이 필요하다.
_UNIT_NAME = {1: "원", 1_000: "천원", 1_000_000: "백만원",
              100_000_000: "억원", 1_000_000_000: "십억원"}


def describe(row) -> str:
    """이 값이 어느 조건의 값인지 한 줄로.

    답변에 이 문구를 붙여 무엇을 준 것인지 밝힌다.
    """
    y, m = row["fiscal_year"], row["base_month"]
    pt, basis = row["period_type"], row["basis"]
    if pt == "annual":
        when = f"{y}년 연간"
    elif pt == "instant":
        when = f"{y}년 {m}월 말 현재"
    elif pt == "cumulative":
        when = f"{y}년 1월~{m}월 누적"
    else:
        s = {3: "1~3월", 6: "4~6월", 9: "7~9월", 12: "10~12월"}.get(m, f"{m}월")
        when = f"{y}년 {s} (3개월)"
    return f"{when} · {basis}"


def lookup(con, corp_codes: list[str], item_codes: list[str],
           years: list[int] | None = None,
           months: list[int] | None = None,
           period_types: list[str] | None = None,
           basis: str | None = None) -> list[dict]:
    """조건에 맞는 값을 전부 낸다. 하나로 좁히지 않는다."""
    sql = ["""SELECT f.item_code, f.value, f.value_raw, f.unit_label, f.unit_mult,
                     f.fiscal_year, f.base_month, f.period_type, f.basis,
                     f.source, f.doc_id, c.corp_name, d.report_nm
              FROM fact_financial f
              JOIN company c ON f.corp_code = c.corp_code
              JOIN document d ON f.doc_id = d.doc_id
              WHERE 1=1"""]
    args: list = []
    if corp_codes:
        sql.append(f"AND f.corp_code IN ({','.join('?' * len(corp_codes))})")
        args += corp_codes
    if item_codes:
        sql.append(f"AND f.item_code IN ({','.join('?' * len(item_codes))})")
        args += item_codes
    if years:
        sql.append(f"AND f.fiscal_year IN ({','.join('?' * len(years))})")
        args += years
    if months:
        sql.append(f"AND f.base_month IN ({','.join('?' * len(months))})")
        args += months
    if period_types:
        sql.append(f"AND f.period_type IN ({','.join('?' * len(period_types))})")
        args += period_types
    if basis:
        sql.append("AND f.basis = ?")
        args.append(basis)
    sql.append("""ORDER BY c.corp_name, f.item_code, f.fiscal_year,
                           f.base_month, f.basis, f.period_type""")
    rows = con.execute(" ".join(sql), args).fetchall()
    return [{"item_code": r["item_code"], "label": LABEL.get(r["item_code"], r["item_code"]),
             "value": r["value"], "value_raw": r["value_raw"],
             "unit": r["unit_label"] or _UNIT_NAME.get(r["unit_mult"], ""),
             "when": describe(r),
             "corp_name": r["corp_name"], "report": r["report_nm"],
             "doc_id": r["doc_id"], "source": r["source"],
             "fiscal_year": r["fiscal_year"], "base_month": r["base_month"],
             "period_type": r["period_type"], "basis": r["basis"]}
            for r in rows]


def pick_items(text: str) -> list[str]:
    """질의에서 항목을 읽는다. 긴 말을 먼저 본다."""
    found = []
    for kw in sorted(KEYWORD, key=len, reverse=True):
        if kw in text and KEYWORD[kw] not in found:
            found.append(KEYWORD[kw])
    return found


def fmt(v: int | None) -> str:
    """원 단위 값을 읽기 쉽게. 조·억 단위로."""
    if v is None:
        return "확인되지 않음"
    neg = v < 0
    n = abs(v)
    if n >= 10 ** 12:
        s = f"{n/10**12:,.2f}조원"
    elif n >= 10 ** 8:
        s = f"{n/10**8:,.0f}억원"
    else:
        s = f"{n:,}원"
    return ("-" if neg else "") + s


def group_for_answer(rows: list[dict]) -> str:
    """조회 결과를 답변에 넣을 형태로 정리한다.

    조건을 앞에 붙여 무엇의 값인지 밝힌다. 여러 후보를 다 보여주되
    사용자가 어느 것을 찾는지 스스로 고를 수 있게 한다.

        2025년 연간 · 연결        333.61조원   사업보고서 (2025.12)
        2025년 1월~9월 누적 · 연결  239.77조원   분기보고서 (2025.09)

    하나로 좁히지 않는 이유가 있다. "2025년 매출" 이 연간인지 최근 분기인지
    프롬프트만으로 판단하기 어렵다. 임의로 고르면 사용자는 그것이 무엇인지
    모르는 채로 받는다.
    """
    if not rows:
        return "해당 조건의 값을 찾지 못했습니다."
    out = []
    by_key: dict = {}
    for r in rows:
        by_key.setdefault((r["corp_name"], r["label"]), []).append(r)
    for (corp, label), items in by_key.items():
        out.append(f"[{corp} · {label}]")
        # 연간 → 누적 → 3개월 → 시점 순으로. 흔히 찾는 것을 앞에
        order = {"annual": 0, "cumulative": 1, "quarter": 2, "instant": 3}
        for r in sorted(items, key=lambda x: (order.get(x["period_type"], 9),
                                              -(x["base_month"] or 0))):
            out.append(f"   {r['when']:<26}{fmt(r['value']):>14}"
                       f"   {r['report']}")
    return "\n".join(out)
