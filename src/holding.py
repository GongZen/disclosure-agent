# -*- coding: utf-8 -*-
"""지분공시(대량보유상황보고서) 파싱.

5% 룰 공시다. 어떤 회사 주식을 5% 이상 가지면 신고해야 하고, 이후
1% 이상 변동마다 다시 낸다. 1,083건이 있다.

한 문서에 표가 20~28개이고 역할이 다르다. 요약정보·보유현황·특별관계자
명단·세부 변동내역이 한 문서에 섞여 있고, 문서 하나에 항목이 최대 1만
7천 개까지 나온다. 외국 자산운용사가 보고하면 산하 펀드마다 한 줄씩
붙기 때문이다.

그래서 표마다 역할(section)을 남긴다. 질의가 요구하는 구간만 꺼내
넘기기 위한 것이고, 그래야 입력 한도 문제와 묻지 않은 개인 신상이
답변에 딸려 나가는 문제를 함께 막을 수 있다.
"""

from __future__ import annotations

import re

__all__ = ["SECTION_RULES", "classify_section", "has_pii", "extract"]

# 앞에 오는 규칙이 먼저 적용된다. 표 제목(첫 행)의 낱말로 판정한다.
SECTION_RULES = [
    ("summary",        ("요약정보",)),
    ("issuer",         ("회사코드",)),
    ("report_type",    ("보고구분",)),
    ("correction",     ("정정사항", "정정요구", "정정사유", "정정대상")),
    ("reporter",       ("법적성격", "자산총액", "대표자")),
    ("related_party",  ("보고자와의구체적관계",)),
    ("fund_list",      ("대상집합투자기구",)),
    ("change_detail",  ("변동일", "증감주식등의내역", "취득/처분방법")),
    ("holding_detail", ("보유주식등의내역", "소유에준하는보유")),
    ("holding_total",  ("의결권있는발행주식총수", "보유잠재주식의수")),
    ("holding",        ("보고서작성기준일", "직전보고서", "이번보고서")),
    ("account",        ("계정별내역",)),
    ("funding_source", ("자기자금", "취득자금등의조성경위")),
    ("loan",           ("대출금액", "담보유지비율", "채무자")),
    ("contract",       ("주요계약", "신탁ㆍ담보", "신탁·담보", "보고자와의관계")),
    ("purpose",        ("보유목적", "경영권영향", "제154조", "영향력을행사")),
    ("change_method",  ("변동방법",)),
    ("note",           ("단위", "주소는", "본인은", "보고자본인은", "서식", "상기", "주)")),
]

# 이 낱말이 표 제목에 있으면 그 표는 개인 신상을 담는다.
# 성명·생년월일·주소가 들어가고, 질의가 묻지 않았으면 넘기지 않는다.
_PII_MARKS = ("생년월일", "주소(소재지)", "여권")


def _sq(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def classify_section(first_cells: list[str]) -> str:
    key = _sq(" ".join(first_cells))
    for name, pats in SECTION_RULES:
        if any(p in key for p in pats):
            return name
    # 칸이 하나뿐인 긴 문장은 안내문이다
    if len(first_cells) <= 1 and (not first_cells or len(first_cells[0]) > 30):
        return "note"
    return "other"


def has_pii(first_cells: list[str]) -> bool:
    key = _sq(" ".join(first_cells))
    return any(_sq(p) in key for p in _PII_MARKS)


def to_num(s):
    if not s:
        return None
    t = str(s).replace(",", "").strip()
    if t in ("-", "", "–", "—"):
        return None
    m = re.fullmatch(r"-?\d+(?:\.\d+)?", t)
    return float(m.group()) if m else None


def to_date(s):
    if not s:
        return None
    m = re.search(r"(\d{4})\s*[-.년]\s*(\d{1,2})\s*[-.월]\s*(\d{1,2})", str(s))
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    return f"{y:04d}{mo:02d}{d:02d}" if 1 <= mo <= 12 and 1 <= d <= 31 else None


def extract(items: list[tuple[int, str, str, str, int]], form: str) -> dict:
    """items = [(seq, section, name, value, pii)] 에서 요약 축을 뽑는다.

    요약정보 표는 이렇게 담긴다.
        보유주식등의 수 및 보유비율 > 직전 보고서 = 940,946      주식수
        보유주식등의 수 및 보유비율 > 940,946    = 5.05         바로 다음이 비율

    머리글이 두 줄이라 열 이름이 밀리지만, 등장 순서는 고정돼 있어
    "직전 보고서" 다음 항목이 비율이라는 관계는 흔들리지 않는다.
    """
    out: dict = {"form": form}
    S = [(n, v) for _s, sec, n, v, _p in items if sec == "summary"]

    def after(pat: str):
        for i, (n, _v) in enumerate(S):
            if pat in _sq(n) and "보유주식등의수및보유비율" in _sq(n):
                return S[i + 1][1] if i + 1 < len(S) else None
        return None

    def val(pat: str, sec: str | None = None):
        for _s, se, n, v, _p in items:
            if sec and se != sec:
                continue
            if pat in _sq(n):
                return v
        return None

    out["prev_shares"] = to_num(val("직전보고서", "summary"))
    out["prev_ratio"] = to_num(after("직전보고서"))
    out["curr_shares"] = to_num(val("이번보고서", "summary"))
    out["curr_ratio"] = to_num(after("이번보고서"))
    out["report_type"] = val("보고구분", "summary")
    out["report_reason"] = val("보고사유", "summary")

    # 서식이 목적에 따라 갈린다. 일반은 경영권에 영향을 주려는 경우이고
    # 약식은 단순투자거나 전문투자자다. 약식만 요약정보에 목적을 적는다.
    out["purpose"] = val("보유목적", "summary") or ("경영권 영향" if form == "일반" else None)

    # "의결권있는발행주식 총수(I) > …" 는 머리글이라 값이 숫자가 아니다.
    # 총괄표의 "이번보고서 > 의결권 있는 발행주식총수(주)" 를 집는다.
    for pat in ("이번보고서>의결권있는발행주식총수", "직전보고서>의결권있는발행주식총수",
                "의결권있는발행주식총수(주)"):
        for _s, se, n, v, _p in items:
            if pat in _sq(n) and to_num(v):
                out["total_shares"] = to_num(v)
                break
        if out.get("total_shares"):
            break

    for key in ("이번보고서>보고자", "직전보고서>보고자"):
        for _s, se, n, v, _p in items:
            if se in ("holding_total", "holding") and key in _sq(n):
                out["holder_name"] = v
                break
        if out.get("holder_name"):
            break

    out["base_date"] = to_date(val("보고서작성기준일"))
    out["obligation_date"] = to_date(val("보고의무발생일"))

    # 검산 — 보유수 ÷ 발행주식총수 × 100 = 보유비율
    #
    # 공시가 쓰는 정식 산정식은 [A+H / I+H-(E+F+G)] × 100 이다.
    # H 는 보유잠재주식, E·F·G 는 그중 교환사채권·증권예탁증권·기타다.
    # 잠재주식 세부까지 뽑으면 정확해지지만 표가 문서마다 여러 개라
    # 합계 행을 가리는 규칙이 따로 필요하다. 지금은 근사식을 쓴다.
    # 회사에 따라 분모에 잠재주식을 더하기도 하고 안 더하기도 한다.
    s, t, r = out.get("curr_shares"), out.get("total_shares"), out.get("curr_ratio")
    if s and t and r is not None:
        lat = 0.0
        for _s, se, n, v, _p in items:
            if "보유잠재주식의수" in _sq(n) and to_num(v):
                lat = to_num(v)
                break
        cands = [s / t * 100] + ([s / (t + lat) * 100] if lat else [])
        out["ratio_calc"] = round(cands[0], 4)
        out["ratio_match"] = int(any(
            abs(round(c, 2) - r) <= 0.05 or abs(round(c, 1) - r) <= 0.05 for c in cands))
    return out
