"""재무제표에서 항목별 값을 뽑는다.

경로가 둘이다. 어느 쪽을 쓸지는 문서가 태그를 갖고 있는지로 갈린다.

    태그 경로    ACODE 로 항목, ACONTEXT 로 축, ADECIMAL 로 단위를 안다
                 판정할 것이 없어 실수도 없다. 2025년 이후 문서 483건
    표 파싱      계정 이름으로 행을 찾고 columns() 로 열을, 제목 표에서 단위를 읽는다
                 2023~2024년 568건. 회계 태그가 아예 없다

2023~2024년 문서에도 ACODE 가 있으나 그것은 DART 서식 필드 코드다.
CRP_NM(회사명) EST_DT(설립일) 같은 것으로 임원 명단·종속회사 목록에 붙는다.
재무제표 본문에 회계 태그가 붙은 것은 2025년부터다.

표기 목록은 태그 있는 문서에서 자동으로 모았다. 그것이 태그 없는 기간에도
통하는지 전수로 확인했다. 기업×항목 415쌍 중 414쌍(99.8%)이 덮인다.
"""
from __future__ import annotations

import re

from fsdoc import columns, parse_doc, _cells, _tag, _NUMCELL

# ── 항목 정의 ────────────────────────────────────────────────────────
# item_code → 어느 표에서 뽑는가. 자본총계를 bs 로 묶는 이유는 자본변동표에
# 같은 값이 (기말자본) 이라는 이름으로 있기 때문이다. 값은 같아도 근거로
# 자본변동표를 대면 답변의 출처가 어긋난다.
KIND = {
    # 1차 — 골든 데이터셋이 덮는 범위
    "total_assets": ("bs",),
    "total_liabilities": ("bs",),
    "total_equity": ("bs",),
    "revenue": ("is", "ci"),
    "operating_income": ("is", "ci"),
    "net_income": ("is", "ci"),
    "net_interest_income": ("is", "ci"),
    "insurance_result": ("is", "ci"),
    # 2차 — 계산 질의에 필요하다. 골든에 정답지가 없는 대신 자체 항등식이 있다
    #        매출액 − 매출원가 = 매출총이익
    #        매출총이익 − 판매비와관리비 = 영업이익
    #        유동자산 + 비유동자산 = 자산총계
    "current_assets": ("bs",),
    "noncurrent_assets": ("bs",),
    "current_liabilities": ("bs",),
    "noncurrent_liabilities": ("bs",),
    # 매각예정 처분집단. K-IFRS 1105호가 유동·비유동 어느 쪽에도 넣지 않고
    # 따로 표시하게 한다. 팔기로 한 사업부는 계속 쓰는 자산과 구분해야 한다.
    # 이것을 담지 않으면 유동+비유동=총계 검산이 어긋난다.
    "held_for_sale_assets": ("bs",),
    "held_for_sale_liabilities": ("bs",),
    # 금융업 자산·부채. 금융 자회사를 둔 지주회사가 일반 영업과 나눠 적는다.
    # 카카오가 그렇다. 유동+비유동에 이것을 더해야 총계가 된다.
    "financial_business_assets": ("bs",),
    "financial_business_liabilities": ("bs",),
    "cost_of_sales": ("is", "ci"),
    "gross_profit": ("is", "ci"),
    "sga": ("is", "ci"),
    "pretax_income": ("is", "ci"),
    "net_fee_income": ("is", "ci"),
    "insurance_revenue": ("is", "ci"),
    # 현금흐름표. capex 가 D2 설비투자다
    "cf_operating": ("cf",),
    "cf_investing": ("cf",),
    "cf_financing": ("cf",),
    "capex": ("cf",),
}

# item_code → XBRL 태그. 영업이익과 보험은 태그가 둘이다.
# 국제 표준에 없는 항목을 금융감독원이 dart_ 접두어로 따로 만들어 병존한다.
CODE = {
    "total_assets": {"ifrs-full_Assets"},
    "total_liabilities": {"ifrs-full_Liabilities"},
    "total_equity": {"ifrs-full_Equity"},
    "revenue": {"ifrs-full_Revenue"},
    "operating_income": {"dart_OperatingIncomeLoss",
                         "ifrs-full_ProfitLossFromOperatingActivities"},
    "net_income": {"ifrs-full_ProfitLoss"},
    "net_interest_income": {"ifrs-full_InterestRevenueExpense"},
    "insurance_result": {"ifrs-full_InsuranceServiceResult",
                         "dart_InsuranceRevenueExpense"},
    "current_assets": {"ifrs-full_CurrentAssets"},
    "noncurrent_assets": {"ifrs-full_NoncurrentAssets"},
    "current_liabilities": {"ifrs-full_CurrentLiabilities"},
    "noncurrent_liabilities": {"ifrs-full_NoncurrentLiabilities"},
    "held_for_sale_assets": {
        "ifrs-full_NoncurrentAssetsOrDisposalGroupsClassifiedAsHeldForSale"},
    "held_for_sale_liabilities": {
        "ifrs-full_LiabilitiesIncludedInDisposalGroupsClassifiedAsHeldForSale"},
    "financial_business_assets": {"dart_FinancialBusinessAssets"},
    "financial_business_liabilities": {"dart_FinancialBusinessLiabilities"},
    "cost_of_sales": {"ifrs-full_CostOfSales"},
    "gross_profit": {"ifrs-full_GrossProfit"},
    "sga": {"dart_TotalSellingGeneralAdministrativeExpenses",
            "ifrs-full_SellingGeneralAndAdministrativeExpense"},
    "pretax_income": {"ifrs-full_ProfitLossBeforeTax"},
    "net_fee_income": {"ifrs-full_FeeAndCommissionIncomeExpense"},
    "insurance_revenue": {"dart_OperatingIncomeInsurance"},
    "cf_operating": {"ifrs-full_CashFlowsFromUsedInOperatingActivities"},
    "cf_investing": {"ifrs-full_CashFlowsFromUsedInInvestingActivities"},
    "cf_financing": {"ifrs-full_CashFlowsFromUsedInFinancingActivities"},
    "capex": {"ifrs-full_PurchaseOfPropertyPlantAndEquipment"
              "ClassifiedAsInvestingActivities"},
}
TAG2CODE = {t: c for c, ts in CODE.items() for t in ts}

# 기업이 직접 만든 확장 태그. entity{법인등록번호}_ 접두어가 붙어 이름을
# 고정할 수 없다. 접두어를 뗀 뒷부분으로 견준다.
#
# 표준 태그에 없는 항목을 기업이 정의한 것인데, 우리 26개 항목 중에는
# 매각예정 처분집단과 금융업 자산·부채만 걸린다. 문서 14건이다.
# 전수에서 entity 태그 상위를 보니 나머지는 자본변동표 세부 항목과
# 현금흐름 유출입 내역이라 우리 범위 밖이다.
#
# 뒷부분을 완전 일치로 견주는 이유가 있다. 부분 일치를 쓰면 세부 항목이
# 함께 걸린다. 금융업자산 대분류는 OfAssetsAbstract 로 끝나고
# 그 안의 현금및현금성자산은 OfAssetsFromFinancialServiceSegment… 로 이어진다.
ENTITY_TAIL = {
    "AssetsFromFinancialServiceSegmentOfAssetsAbstract":
        "financial_business_assets",
    "LiabilitiesFromFinancialServiceSegmentOfLiabilitiesAbstract":
        "financial_business_liabilities",
    "NonCurrentAssetsOrDisposalGroupsClassifiedAsHeldForSaleOr"
    "AsHeldForDistributionToOwnersOfAssetsAbstract":
        "held_for_sale_assets",
    "LiabilitiesincludedindisposalgroupsclassifiedasheldforsaleOf"
    "LiabilitiesAbstract":
        "held_for_sale_liabilities",
}
_ENT_PREFIX = re.compile(r"^entity\d+_")


def tag_to_code(acode: str) -> str | None:
    """ACODE 를 item_code 로. 표준 태그를 먼저 보고 확장 태그를 뒤에 본다."""
    tag = (acode or "").split("|")[0]
    code = TAG2CODE.get(tag)
    if code:
        return code
    if not tag.startswith("entity"):
        return None
    tail = _ENT_PREFIX.sub("", tag)
    for k, v in ENTITY_TAIL.items():
        if tail.lower() == k.lower():
            return v
    return None

# item_code → 계정 이름. 태그 없는 문서에서 쓴다.
# 완전 일치로만 견준다. 부분 일치를 쓰면 "당기순이익(손실)" 이
# "지배기업의 소유주에게 귀속되는 당기순이익(손실)" 에 걸려 지분별 분해 값을 뽑는다.
# 삼성전자라면 4조 가까이 어긋난다.
NAMES = {
    "total_assets": ("자산총계", "자산 총계", "자산 합계", "총자산", "자산합계"),
    "total_liabilities": ("부채총계", "부채 총계", "부채 합계", "총부채", "부채합계"),
    "total_equity": ("자본총계", "자본 총계", "총자본", "자본합계"),
    "revenue": ("매출액", "영업수익", "수익(매출액)", "매출", "매출액 및 기타수익"),
    "operating_income": ("영업이익", "영업이익(손실)", "영업손익", "영업손실",
                         "영업이익 (손실)", "영업활동으로부터의 이익(손실)"),
    "net_income": (
        "당기순이익", "당기순이익(손실)", "분기순이익", "반기순이익",
        "분기순이익(손실)", "반기순이익(손실)", "당기순손익", "분기순손익",
        "반기순손익", "연결당기순이익", "연결분기순이익", "연결반기순이익",
        "당기순손실", "분기순손실", "반기순손실", "연결당기순이익(손실)",
        "분기순이익 (손실)", "당분기순이익", "분기손이익(손실)",
        "반기손이익(손실)", "반기연결순이익",
    ),
    "net_interest_income": ("순이자손익", "순이자이익", "이자손익", "이자수익(비용)"),
    "insurance_result": ("보험손익", "보험서비스결과", "순보험손익", "보험관련손익",
                         "보험서비스손익", "보험계약관련손익"),
    # 2차. 완전 일치만 쓴다. 부분 일치를 쓰면 세부 항목이 걸린다.
    #   "기타유동자산" 346회 · "매각예정비유동자산" 84회
    #   "투자활동으로 인한 현금유입액" 102회 — 순액이 아니라 총액이다
    #   "영업활동으로 인한 자산 부채의 변동" 99회 — 구성 항목이다
    "current_assets": ("유동자산", "유동자산 합계", "유동자산합계"),
    "noncurrent_assets": ("비유동자산", "비유동자산 합계", "비유동자산합계"),
    "current_liabilities": ("유동부채", "유동부채 합계", "유동부채합계"),
    "noncurrent_liabilities": ("비유동부채", "비유동부채 합계", "비유동부채합계"),
    "held_for_sale_assets": ("매각예정으로 분류된 처분집단의 자산",
                             "매각예정비유동자산", "매각예정자산",
                             "매각예정으로 분류된 자산",
                             "매각예정으로분류된처분집단의자산",
                             "매각예정 또는 소유주에 대한 분배예정으로 분류된 자산",
                             "매각예정비유동자산및처분자산집단",
                             "매각예정으로 분류된 비유동자산"),
    "held_for_sale_liabilities": ("매각예정으로 분류된 처분집단의 부채",
                                  "매각예정비유동부채", "매각예정부채",
                                  "매각예정으로 분류된 부채",
                                  "매각예정으로분류된처분집단의부채",
                                  "매각예정처분자산집단에 포함된 부채",
                                  "매각예정으로 분류된 처분자산집단에 포함된 부채"),
    "financial_business_assets": ("금융업자산", "금융업 자산"),
    "financial_business_liabilities": ("금융업부채", "금융업 부채"),
    "cost_of_sales": ("매출원가", "영업원가"),
    "gross_profit": ("매출총이익", "매출총이익(손실)", "매출총손익"),
    "sga": ("판매비와관리비", "판매관리비", "판매 및 일반관리비", "판매비와 관리비",
            "판매비및관리비", "일반관리비"),
    "pretax_income": ("법인세비용차감전순이익", "법인세비용차감전순이익(손실)",
                      "법인세차감전순이익", "법인세비용차감전순손익",
                      "법인세비용차감전이익", "법인세비용차감전순손실",
                      "법인세차감전순이익(손실)", "법인세비용차감전계속영업순이익",
                      "법인세비용차감전계속사업순이익", "법인세비용차감전손익"),
    "net_fee_income": ("순수수료손익", "순수수료이익", "수수료손익"),
    "insurance_revenue": ("보험영업수익", "보험수익", "보험서비스수익"),
    "cf_operating": ("영업활동현금흐름", "영업활동으로 인한 현금흐름",
                     "영업활동순현금흐름", "영업활동으로인한현금흐름",
                     "영업활동으로부터의 현금흐름", "영업활동 현금흐름",
                     "영업활동으로 인한 순현금흐름", "영업활동으로부터의현금흐름"),
    "cf_investing": ("투자활동현금흐름", "투자활동으로 인한 현금흐름",
                     "투자활동순현금흐름", "투자활동으로인한현금흐름",
                     "투자활동 현금흐름", "투자활동으로부터의 현금흐름",
                     "투자활동으로 인한 순현금흐름", "투자활동으로부터의현금흐름"),
    "cf_financing": ("재무활동현금흐름", "재무활동으로 인한 현금흐름",
                     "재무활동순현금흐름", "재무활동으로인한현금흐름",
                     "재무활동으로부터의 현금흐름", "재무활동 현금흐름",
                     "재무활동으로 인한 순현금흐름", "재무활동으로부터의현금흐름"),
    "capex": ("유형자산의 취득", "유형자산의취득", "유형자산 취득",
              "투자활동으로 분류된 유형자산의 취득", "유형자산취득"),
}

# 자산총계가 없고 이것만 있는 문서가 있다. 회계 항등식의 오른쪽을 적은 행이라
# 값이 자산총계와 같다. 자산총계 행을 먼저 찾고 없을 때만 쓴다.
ALT_ASSETS = ("자본과부채총계", "부채와자본총계", "부채및자본총계",
              "부채 및 자본총계", "부채와 자본총계", "부채와 자본 총계",
              "부채 및 자본 총계", "부채와자본 총계")

# 부호 규약을 항목마다 정한다.
#
# capex 는 두 경로가 반대로 낸다. XBRL 의 PurchaseOfPropertyPlantAndEquipment…
# 는 "취득한 금액" 이라 양수로 태깅하고, 현금흐름표는 현금이 나간 것이라
# (47,522,179) 로 적는다. 전수 532쌍 중 199쌍이 부호만 반대였고 절댓값은 같다.
# 답변에서 "설비투자 5조" 라고 말하고 "규모가 더 큰 기업" 을 물으므로
# 양수로 통일한다. 부호가 섞이면 크기 비교가 뒤집힌다.
#
# cf_operating · cf_investing · cf_financing 은 부호 자체가 정보다.
# 영업활동이 음수면 영업으로 현금을 벌지 못했다는 뜻이다. 두 경로가
# 1,654쌍 전부 일치하므로 그대로 둔다.
# 비용 항목도 같다. 원문이 매출원가를 (7,426,107) 로 적는 문서가 있고
# XBRL 도 일부 문서에서 음수로 태깅한다. 같은 항목이 문서마다 갈린다.
#   cost_of_sales  + 2,258 / − 141
#   sga            + 2,359 / − 270
# 절댓값으로 맞추니 매출총이익 항등식 어긋남이 142건에서 2건으로 줄었다.
# 비용은 "얼마를 썼나" 이고 답변에서도 양수로 말한다.
ABS_ITEMS = {"capex", "cost_of_sales", "sga"}

_ROMAN = "ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩIVX"
_MULT = {"-6": 1_000_000, "-3": 1_000, "0": 1, "": None, "INF": 1}


def clean_name(s: str) -> str:
    """계정 이름에서 군더더기를 뗀다.

    앞의 번호와 뒤의 주석 표시를 떼지 않으면 같은 계정이 수십 가지로 갈린다.
        'Ⅲ.영업이익(손실)'          →  '영업이익(손실)'
        '매출액 (주26,31,32,40,44)'  →  '매출액'
        '부채총계 (A) (단위: 백만원)'  →  '부채총계'
    """
    n = re.sub(r"\s+", " ", s or "").strip()
    n = re.sub(rf"^[\(（]?[{_ROMAN}0-9]+[\)）]?\s*[\.\)]\s*", "", n)
    n = re.sub(r"\s*\(주[^)]*\)", "", n)
    n = re.sub(r"\s*\(단위[^)]*\)", "", n)
    n = re.sub(r"\s*\([A-Za-z]\)", "", n)
    n = re.sub(r"[\*※]", "", n)
    return re.sub(r"\s+", " ", n).strip()


def is_note_ref(s: str) -> bool:
    """주석 번호인가. 값이 아니다.

    재무제표 첫 값 열이 주석 번호인 문서가 있다.

        매출 │ 4,23,29 │ 1,048,977,927 │ 885,023,767
              └── 주석 4·23·29 번을 보라는 뜻

    쉼표가 들어 있어 숫자처럼 보인다. 두 가지로 가른다.
    천 단위 쉼표는 세 자리씩 끊고, 주석 번호는 조각이 전부 한두 자리다.
    주석은 1번부터 쉰 번 남짓이라 세 자리가 나오지 않는다.
    둘 다 아니면 값으로 둔다. 애매한 것을 버리면 값을 잃지만
    남기면 크기 검산이 나중에 잡는다.

        '4,23,29'      [4, 23, 29]        전부 1~2자리   주석
        '2,131.0'      [2, 131]           뒤가 3자리     값
        '455,905,980'  [455, 905, 980]    전부 3자리     값
    """
    t = re.sub(r"[\(\)△▲\-\s]", "", s or "").split(".")[0]
    if "," not in t:
        return False
    parts = t.split(",")
    if all(len(p) == 3 for p in parts[1:]):
        return False
    return all(1 <= len(p) <= 2 for p in parts)


def to_num(s: str) -> int | None:
    """표기된 숫자를 정수로. 음수 표기가 셋이다. (1,234) △1,234 -1,234"""
    t = (s or "").strip()
    if not t or not re.fullmatch(r"[\(\)△▲\-\d,\.\s]+", t):
        return None
    if is_note_ref(t):
        return None
    neg = t.startswith(("(", "△", "▲", "-"))
    digits = re.sub(r"[^\d]", "", t)
    if not digits:
        return None
    return -int(digits) if neg else int(digits)


def _attr(cell, name: str, acode: str) -> str:
    """속성을 읽는다. 문서 79건은 ACODE 하나에 파이프로 다 넣는다.

        ACODE="dart:OperatingIncomeInsurance|CFY2023dTQQ_..._ConsolidatedMember|-6|KRW|"
                └── 태그 ──┘└──────── 컨텍스트 ────────┘└─┘└─┘
                                                        소수점 통화
    """
    v = cell.get(name) or cell.get(name.lower()) or ""
    if v:
        return v
    if "|" not in acode:
        return ""
    parts = acode.split("|")
    idx = {"ACONTEXT": 1, "ADECIMAL": 2}.get(name.upper())
    return parts[idx] if idx is not None and len(parts) > idx else ""


# ── 태그 경로 ────────────────────────────────────────────────────────

def _parse_context(ctx: str) -> dict:
    """ACONTEXT 를 뜯는다.

        CFY2025eFY_ifrs-full_ConsolidatedAndSeparate…Axis_ifrs-full_ConsolidatedMember
        └─┬┘└─┬─┘│└┬┘                                              └───────┬───────┘
          │   │  │ │                                                       │
          │   │  │ └─ FY 연간 · HY 반기 · TQ 3분기 · FQ 1분기
          │   │  └─── e 시점(재무상태표) · d 기간(손익·현금흐름)
          │   └────── 회계연도
          └────────── CFY 당기 · PFY 전기 · BPFY 전전기

    끝에 A(누적) 또는 Q(분기)가 더 붙기도 한다. 그것이 period_type 을 가른다.
    """
    m = re.match(r"(BPFY|PFY|CFY)(\d{4})([de])(FY|HY|TQ|FQ)([AQ])?", ctx or "")
    if not m:
        return {}
    era, year, inst, span, acc = m.groups()
    basis = ("연결" if "ConsolidatedMember" in ctx
             else "별도" if "SeparateMember" in ctx else "")
    if inst == "e":
        period = "instant"      # 재무상태표. 시점의 잔액이다
    elif span == "FY":
        period = "annual"
    elif acc == "Q":
        period = "quarter"
    else:
        period = "cumulative"
    return {"era": era, "year": int(year), "instant": inst == "e",
            "span": span, "period_type": period, "basis": basis}


def from_tags(root) -> list[dict]:
    """태그가 붙은 문서에서 값을 낸다. 판정할 것이 없다.

    같은 항목이 여러 번 나오면 전부 낸다. 당기·전기·전전기, 연결·별도,
    누적·3개월이 각각 다른 행이기 때문이다. 고르는 것은 부르는 쪽 몫이다.
    """
    from fsdoc import _OPEN, _CLOSE, _sq

    out, infs = [], False
    for e in root.iter():
        t = _tag(e)
        if t == "TITLE":
            x = _sq("".join(e.itertext()))
            if _OPEN.match(x):
                infs = True
            elif infs and _CLOSE.match(x):
                infs = False
            continue
        if not infs or t not in ("TE", "TU"):
            continue
        acode = e.get("ACODE") or e.get("acode") or ""
        tag = acode.split("|")[0]
        code = tag_to_code(acode)
        if not code:
            continue
        ctx = _attr(e, "ACONTEXT", acode)
        # 축이 둘 이상이면 부문별·지역별 분해 값이다. 전체가 아니다.
        if len(re.findall(r"Member", ctx)) > 1:
            continue
        val = to_num("".join(e.itertext()))
        if val is None:
            continue
        dec = _attr(e, "ADECIMAL", acode).strip()
        mult = _MULT.get(dec)
        if mult is None and dec:
            try:
                mult = 10 ** (-int(dec))
            except ValueError:
                mult = None
        c = _parse_context(ctx)
        if e.get("ANEGATED", "").upper() == "Y":
            val = -val
        if code in ABS_ITEMS:
            val = abs(val)
        out.append({"item_code": code, "value": val, "mult": mult,
                    "tag": tag, "source": "xbrl", **c})
    return out


# ── 표 파싱 경로 ─────────────────────────────────────────────────────

def from_tables(raw: str) -> list[dict]:
    """태그 없는 문서에서 값을 낸다. 계정 이름과 열 머리글로 찾는다."""
    out = []
    for basis, kind, table, src, unit in parse_doc(raw):
        if not kind:
            continue
        cols = columns(table)
        rows = []
        for tr in table.iter():
            if _tag(tr) != "TR":
                continue
            cs = _cells(tr)
            if len(cs) < 2:
                continue
            # 주석 번호 칸을 앞에서 떼고 값 목록을 만든다. 떼지 않으면
            # 주석이 있는 행과 없는 행에서 열 위치가 한 칸 밀린다.
            #
            #   매출      4,23,29 │ 1,048,977,927 │ 885,023,767
            #   매출총이익           151,343,929 │ 133,451,345
            #
            # 앞은 주석 칸이 있고 뒤는 없다. to_num 이 주석을 None 으로
            # 내주므로 그대로 두면 매출총이익의 당기 값 자리에 전기 값이 온다.
            cells = list(cs[1:])
            while cells and is_note_ref(cells[0]):
                cells.pop(0)
            vals = [to_num(c) for c in cells]
            if all(v is None for v in vals):
                continue
            rows.append((clean_name(cs[0]), vals))
        for code, kinds in KIND.items():
            if kind not in kinds:
                continue
            names = NAMES[code]
            hit = [(n, v) for n, v in rows if n in names]
            if not hit and code == "total_assets":
                hit = [(n, v) for n, v in rows if n in ALT_ASSETS]
            if not hit:
                continue
            name, vals = hit[0]
            for i, v in enumerate(vals):
                if v is None:
                    continue
                # 재무상태표는 어느 시점의 잔액이라 누적·3개월이라는 개념이 없다.
                # columns() 가 기수 행의 "반기" 를 보고 cumulative 로 판정하므로
                # 여기서 덮어쓴다. 손익·현금흐름만 기간의 값이다.
                pt = "instant" if kind == "bs" else (
                    cols[i] if i < len(cols) else None)
                if code in ABS_ITEMS:
                    v = abs(v)
                out.append({"item_code": code, "value": v,
                            "mult": unit[1], "unit": unit[0],
                            "basis": basis, "kind": kind, "name": name,
                            "period_type": pt, "col": i, "source": "table",
                            "dup": len(hit) > 1})
    return out


# ── 진입점 ───────────────────────────────────────────────────────────

def has_fs_tags(raw: str) -> bool:
    """재무제표 본문에 회계 태그가 붙어 있는가.

    "ACODE" 문자열 유무로 판정하면 안 된다. 2023~2024년 문서에도 ACODE 가
    있으나 그것은 DART 서식 필드 코드다. 임원 명단·종속회사 목록에 붙는
    CRP_NM(회사명) EST_DT(설립일) 같은 것으로 재무제표와 무관하다.
    우리가 쓰는 회계 태그가 실제로 있는지로 판정한다.
    """
    return any(t in raw for t in TAG2CODE)


def extract(raw: str) -> list[dict]:
    """문서 하나에서 항목별 값을 낸다. 경로는 태그 유무로 고른다."""
    from lxml import etree
    from fsdoc import _P

    if has_fs_tags(raw):
        try:
            root = etree.fromstring(raw.encode("utf-8"), parser=_P)
        except Exception:
            return from_tables(raw)
        got = from_tags(root)
        # 태그가 있어도 쓸 수 없는 경우가 둘이다. 어느 쪽이든 표 파싱이 낫다.
        #
        #   ACONTEXT 없음   계정은 알지만 당기인지 전기인지, 누적인지
        #                   3개월치인지 모른다. 반기보고서 7건이 그렇고
        #                   태그가 3개월치를 골라 누적과 두 배 어긋났다
        #   ADECIMAL 없음   단위를 몰라 원 단위로 환산할 수 없다
        #                   KB금융·메리츠금융지주 2건. 표 파싱은 제목 표에서
        #                   단위를 읽으므로 처리한다
        if got:
            half = len(got) / 2
            has_ctx = sum(1 for g in got if g.get("period_type")) >= half
            has_unit = sum(1 for g in got if g.get("mult")) >= half
            if has_ctx and has_unit:
                return _fill_units(got, raw)
    return from_tables(raw)


def _fill_units(rows: list[dict], raw: str) -> list[dict]:
    """단위가 빠진 항목만 표 파싱 값으로 채운다.

    문서 전체가 아니라 항목 몇 개만 ADECIMAL 이 없는 경우가 있다.
    LG씨엔에스 2024년 문서 둘에서 sga·현금흐름 항목이 그랬다.
    문서 단위로 경로를 고르면 그 소수가 값 없이 남는다.
    """
    missing = {r["item_code"] for r in rows if not r.get("mult")}
    if not missing:
        return rows
    tb = from_tables(raw)
    by = {}
    for t in tb:
        if t["item_code"] in missing and t.get("mult"):
            by.setdefault(t["item_code"], t)
    if not by:
        return rows
    out = []
    for r in rows:
        if r.get("mult") or r["item_code"] not in by:
            out.append(r)
            continue
        t = by[r["item_code"]]
        # 표기값이 같으면 같은 값이다. 배수만 받아 채운다.
        if t["value"] == r["value"]:
            out.append({**r, "mult": t["mult"], "unit": t.get("unit")})
        else:
            out.append(r)
    return out


# 무엇을 물었는지 모를 때 고르는 순서. 앞의 것이 없으면 뒤로 넘어간다.
# 누적을 3개월치보다 앞에 둔 이유는 "상반기 매출" 이 통상 누적을 뜻하기 때문이다.
_PERIOD_ORDER = ("annual", "cumulative", "quarter")


def pick(rows: list[dict], code: str, *, basis: str = "연결",
         period: str | None = None, current: bool = True) -> dict | None:
    """뽑아낸 값 중 하나를 고른다.

    태그 경로는 era 로 당기를 가르고 표 파싱은 열 순서로 가른다.
    둘의 구분이 달라 여기서 흡수한다.

    period 를 주지 않으면 연간 → 누적 → 3개월 순으로 찾는다. 반기보고서에서
    첫 열은 3개월치인데 그것을 상반기 실적으로 답하면 절반만 말하는 셈이다.
    """
    cand = [r for r in rows if r["item_code"] == code]
    if basis:
        cand = [r for r in cand if r.get("basis") in (basis, None, "")]
    if current:
        tagged = [r for r in cand if r.get("era")]
        if tagged:
            cand = [r for r in tagged if r["era"] == "CFY"] or tagged
        else:
            # 표 파싱은 당기 표시가 없다. 앞쪽 두 열이 당기다
            # (3개월·누적). 전기는 그 뒤에 온다.
            cand = [r for r in cand if r.get("col", 0) <= 1] or cand
    if period:
        return next((r for r in cand if r.get("period_type") == period), None)
    for p in _PERIOD_ORDER:
        hit = [r for r in cand if r.get("period_type") == p]
        if hit:
            return hit[0]
    return cand[0] if cand else None
