# -*- coding: utf-8 -*-
"""거래소 계약 공시 파싱.

서식이 다섯 갈래다. 같은 "단일판매·공급계약체결"인데 항목 이름이 다르다.
1,169건 전수를 열어 확인한 결과다.

    의무      유가증권 의무공시     - 체결계약명 · 계약금액(원) · 7. 계약(수주)일자
    자율      유가증권 자율공시     - 세부내용   · 계약금액(원) · 7. 계약(수주)일
    코스닥    코스닥              1. 판매ㆍ공급계약 내용 · 계약금액 총액(원) · 8. 계약(수주)일자
    해지      계약 해지           - 해지계약명 · 해지금액(원) · 6. 해지일자
    시설투자   신규시설투자         - 투자대상   · 투자금액(원) · 5. 이사회결의일(결정일)

번호로 항목을 찾으면 안 된다. 같은 뜻의 항목이 서식마다 다른 번호를 달고 있다.
이름으로 찾는다.
"""

from __future__ import annotations

import re
import unicodedata as ud

__all__ = ["detect_form", "body_of", "extract", "normalize_title",
           "to_int", "to_date", "FORMS"]

FORMS = ("의무", "자율", "코스닥", "해지", "시설투자")

# 항목 이름 앞에 붙을 수 있는 것 — 번호, 하이픈, 또는 없음.
# "- 체결계약명" 의 하이픈을 빠뜨려 1,022건을 통째로 못 읽은 적이 있다.
_PRE = r"\n[ \t]*(?:\d{1,2}[ \t]*[.．][ \t]*)?[-－ㆍ·]?[ \t]*"


def _mark(name: str) -> re.Pattern:
    return re.compile(_PRE + name + r"[ \t]*\n")


_MARKS = {
    "체결계약명": _mark("체결계약명"),
    "세부내용": _mark("세부내용"),
    "판공내용": _mark(r"판매ㆍ공급계약[ \t]*내용"),
    "확정계약금액": _mark(r"확정[ \t]*계약금액"),
    "총액": _mark(r"계약금액[ \t]*총액\(원\)"),
    "해지계약명": _mark("해지계약명"),
    "해지금액": _mark(r"해지금액\(원\)"),
    "투자금액": _mark(r"투자금액\(원\)"),
    "자기자본": _mark(r"자기자본\(원\)"),
}

# 서식별 항목 이름. 왼쪽이 우리가 쓸 이름, 오른쪽이 원문 표기다.
FIELDS: dict[str, dict[str, str]] = {
    "의무": {
        "title": "- 체결계약명", "amount": "계약금액(원)", "base_amount": "최근매출액(원)",
        "ratio_stated": "매출액대비(%)", "is_large_corp": "대규모법인여부",
        "counterparty": "3. 계약상대", "counterparty_rel": "- 회사와의 관계",
        "region": "4. 판매ㆍ공급지역", "signed_at": "7. 계약(수주)일자",
        "hold_until": "유보기한", "hold_reason": "유보사유",
        "category": "1. 판매ㆍ공급계약 구분",
    },
    "자율": {
        "title": "- 세부내용", "amount": "계약금액(원)", "base_amount": "최근매출액(원)",
        "ratio_stated": "매출액대비(%)", "is_large_corp": "대규모법인여부",
        "counterparty": "3. 계약상대", "counterparty_rel": "- 회사와의 관계",
        "region": "4. 판매ㆍ공급지역", "signed_at": "7. 계약(수주)일",
        "category": "1. 판매ㆍ공급계약 구분",
    },
    "코스닥": {
        "title": "1. 판매ㆍ공급계약 내용", "amount": "계약금액 총액(원)",
        "amount_fixed": "확정 계약금액", "amount_cond": "조건부 계약금액",
        "base_amount": "최근 매출액(원)", "ratio_stated": "매출액 대비(%)",
        "counterparty": "3. 계약상대방", "region": "4. 판매ㆍ공급지역",
        "signed_at": "8. 계약(수주)일자",
        "hold_until": "유보기한", "hold_reason": "유보사유",
    },
    "해지": {
        "title": "- 해지계약명", "amount": "해지금액(원)", "base_amount": "최근매출액(원)",
        "ratio_stated": "매출액대비(%)", "is_large_corp": "대규모법인여부",
        "counterparty": "3. 계약상대", "counterparty_rel": "- 회사와의 관계",
        "terminate_reason": "5. 해지 주요사유", "signed_at": "6. 해지일자",
        "category": "1. 판매ㆍ공급계약 해지 구분",
        "hold_until": "유보기한", "hold_reason": "유보사유",
    },
    "시설투자": {
        "title": "- 투자대상", "amount": "투자금액(원)", "base_amount": "자기자본(원)",
        "ratio_stated": "자기자본대비(%)", "is_large_corp": "대규모법인여부",
        "purpose": "3. 투자목적", "signed_at": "5. 이사회결의일(결정일)",
        "category": "1. 투자구분", "hold_until": "유보기한", "hold_reason": "유보사유",
    },
}

# 시작일·종료일은 절 안에 중첩돼 있다. 절 이름이 서식마다 다르다.
_PERIOD = {"의무": "5. 계약기간", "자율": "5. 계약기간", "코스닥": "5. 계약기간",
           "해지": "4. 계약기간", "시설투자": "4. 투자기간"}

_BASE_KIND = {"시설투자": "equity"}      # 나머지는 revenue

# 정정본은 앞쪽에 정정사항 표가 있고 거기에 정정 전 값이 들어 있다.
# 그냥 항목을 찾으면 옛 값이 먼저 걸린다. 본문 표지 뒤부터 읽어야 한다.
_BODY = re.compile(r"\n\s*(?:단일판매ㆍ공급계약\s*(?:체결|해지)|신규\s*시설투자\s*등)"
                   r"(?:\(자율공시\))?\s*\n")

_ALT_TITLE = {"해지": "- 세부물건"}       # 한미약품 건은 해지계약명 대신 이것을 쓴다


def detect_form(text: str) -> str:
    """다섯 서식 중 어느 것인지 가려낸다.

    제목 문자열이 아니라 항목 이름으로 판별한다. 제목은 회사마다 표기가
    흔들리지만 항목 이름은 서식이 정한 것이라 흔들리지 않는다.
    """
    sig = {k for k, p in _MARKS.items() if p.search(text)}
    if "투자금액" in sig and "자기자본" in sig:
        return "시설투자"
    if "해지금액" in sig or "해지계약명" in sig:
        return "해지"
    if "확정계약금액" in sig or "총액" in sig or "판공내용" in sig:
        return "코스닥"
    if "체결계약명" in sig:
        return "의무"
    if "세부내용" in sig:
        return "자율"
    return ""


def body_of(text: str) -> str:
    """정정사항 표를 잘라내고 본문만 남긴다.

    마지막 표지를 쓴다. 문서 첫 줄의 제목에도 같은 말이 들어 있기 때문이다.
    """
    ms = list(_BODY.finditer(text))
    return text[ms[-1].end():] if ms else text


def normalize_title(s: str) -> str:
    """같은 계약을 같다고 알아보기 위한 키.

    1,169건 전수에서 단계별 병합 결과를 보고 정했다. 공백 제거는 5개 그룹을
    묶었고 전부 같은 계약이었다. 괄호 제거는 10개 그룹을 묶었는데 대부분
    다른 계약이었다. (Tobelo)와 (Sumbawa)는 다른 지역이고 (S-04)와 (S-05)는
    다른 공구다. 그래서 괄호는 건드리지 않는다.
    """
    if not s:
        return ""
    t = re.sub(r"\s+", "", ud.normalize("NFKC", s)).strip()
    if t.endswith("사업") and len(t) > 4:
        t = t[:-2]
    return t


def to_int(s: str | None) -> int | None:
    """금액 문자열을 정수로. 값이 없으면 None."""
    if not s:
        return None
    t = str(s).replace(",", "").strip()
    if t in ("-", "", "–", "—"):
        return None
    m = re.fullmatch(r"-?\d+", t)
    return int(m.group()) if m else None


def to_float(s: str | None) -> float | None:
    if not s:
        return None
    t = str(s).replace(",", "").replace("%", "").strip()
    try:
        return float(t)
    except ValueError:
        return None


def to_date(s: str | None) -> str | None:
    """날짜를 YYYYMMDD 로 맞춘다. document.rcept_dt 와 같은 표기를 쓴다."""
    if not s:
        return None
    m = re.search(r"(\d{4})[-.년\s]*(\d{1,2})[-.월\s]*(\d{1,2})", str(s))
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return None
    return f"{y:04d}{mo:02d}{d:02d}"


def _period(lines: list[str], sect: str) -> tuple[str | None, str | None]:
    for i, ln in enumerate(lines):
        if ln != sect:
            continue
        seg = lines[i:i + 8]
        s = e = None
        for j, x in enumerate(seg):
            if x == "시작일" and j + 1 < len(seg):
                s = seg[j + 1]
            if x == "종료일" and j + 1 < len(seg):
                e = seg[j + 1]
        return s, e
    return None, None


def extract(text: str) -> dict:
    """계약 공시 하나에서 값을 뽑는다. 서식 판별부터 한다."""
    form = detect_form(text)
    if not form:
        return {"form": ""}

    labels = FIELDS[form]
    body = body_of(text)
    lines = [x.strip() for x in body.split("\n")]
    known = set(labels.values()) | set(_PERIOD.values()) | {"시작일", "종료일"}

    raw: dict[str, str] = {}
    for key, label in labels.items():
        for i, ln in enumerate(lines):
            if ln != label:
                continue
            val = lines[i + 1] if i + 1 < len(lines) else ""
            # 다음 줄이 또 다른 항목명이면 이 항목은 빈 값이다
            raw[key] = "" if val in known else val
            break

    if form in _ALT_TITLE and not raw.get("title"):
        alt = _ALT_TITLE[form]
        for i, ln in enumerate(lines):
            if ln == alt and i + 1 < len(lines):
                raw["title"] = lines[i + 1]
                break

    s, e = _period(lines, _PERIOD[form])

    amount = to_int(raw.get("amount"))
    base = to_int(raw.get("base_amount"))
    stated = to_float(raw.get("ratio_stated"))
    calc = round(amount / base * 100, 4) if amount and base else None
    # 공시는 소수 첫째~둘째 자리까지 적으므로 반올림 오차를 허용한다
    match = None
    if calc is not None and stated is not None:
        match = int(abs(calc - stated) <= max(0.05, stated * 0.005))

    return {
        "form": form,
        "title": raw.get("title") or None,
        "title_norm": normalize_title(raw.get("title", "")) or None,
        "category": raw.get("category") or None,
        "counterparty": raw.get("counterparty") or None,
        "counterparty_rel": raw.get("counterparty_rel") or None,
        "region": raw.get("region") or None,
        "amount_krw": amount,
        "amount_fixed": to_int(raw.get("amount_fixed")),
        "amount_cond": to_int(raw.get("amount_cond")),
        "base_amount": base,
        "base_kind": _BASE_KIND.get(form, "revenue"),
        "ratio_stated": stated,
        "ratio_calc": calc,
        "ratio_match": match,
        "start_date": to_date(s),
        "end_date": to_date(e),
        "signed_at": to_date(raw.get("signed_at")),
        "purpose": raw.get("purpose") or None,
        "terminate_reason": raw.get("terminate_reason") or None,
        "hold_until": to_date(raw.get("hold_until")),
        "hold_reason": raw.get("hold_reason") or None,
        "is_large_corp": {"해당": 1, "미해당": 0}.get(raw.get("is_large_corp", "").strip()),
    }
