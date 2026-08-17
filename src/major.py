# -*- coding: utf-8 -*-
"""주요사항보고서에서 값을 뽑는다.

사건 종류가 28가지이고 유형마다 항목이 다르다. 고유 항목이 678개라
전부 컬럼으로 만들 수 없으므로, 여기서는 자주 쓰는 축만 뽑는다.
나머지는 major_item 에 원문 이름 그대로 담긴다.

항목을 찾을 때 마지막 매칭을 쓴다. 정정본에는 같은 항목이 정정사항 표와
본문에 두 번 나오고, 세 번 나오는 것도 있다. 첫 매칭을 잡으면 정정 전
값을 읽는다. 실제로 검산 불일치 4건이 그 원인이었다.
"""

from __future__ import annotations

import re

__all__ = ["extract", "USE_KEYS", "AMOUNT_SRC"]

# 자금조달의 목적 여섯 갈래. 유상증자와 사채류 다섯 유형이 같은 틀을 쓴다.
USE_KEYS = {
    "use_facility": "시설자금",
    "use_business": "영업양수자금",
    "use_operation": "운영자금",
    "use_debt": "채무상환자금",
    "use_acquire": "타법인증권취득자금",
    "use_other": "기타자금",
}

# 주된 금액이 유형마다 다른 항목에 있다. 어디서 왔는지 amount_src 에 남긴다.
AMOUNT_SRC: dict[str, tuple[str, ...]] = {
    "전환사채권발행결정": ("사채의권면", "총액"),
    "교환사채권발행결정": ("사채의권면", "총액"),
    "상각형조건부자본증권발행결정": ("사채의권면", "총액"),
    "자본으로인정되는채무증권발행결정": ("사채의권면", "총액"),
    "자기전환사채매도결정": ("사채의권면", "총액"),
    "자기주식처분결정": ("처분예정금액", "보통주식"),
    "자기주식취득결정": ("취득예정금액", "보통주식"),
    "자기주식취득신탁계약체결결정": ("계약금액",),
    "자기주식취득신탁계약해지결정": ("계약금액",),
    "유형자산양수결정": ("양수금액",),
    "유형자산양도결정": ("양도금액",),
    "영업양수결정": ("양수금액",),
    "타법인주식및출자증권양수결정": ("양수금액",),
    "타법인주식및출자증권양도결정": ("양도금액",),
}

# 사건이 없던 일이 된 경우를 가리키는 말. 공시유보와 구분해야 한다.
_WITHDRAWN = re.compile(r"철회|취소|해제|무효|가처분|중단")


def _sq(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def to_int(s):
    if s is None:
        return None
    t = str(s).replace(",", "").strip()
    if t in ("-", "", "–", "—"):
        return None
    m = re.fullmatch(r"-?\d+", t)
    return int(m.group()) if m else None


def to_float(s):
    if s is None:
        return None
    t = str(s).replace(",", "").replace("%", "").strip()
    if t in ("-", "", "–", "—"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def to_date(s):
    if not s:
        return None
    m = re.search(r"(\d{4})\s*[-.년]\s*(\d{1,2})\s*[-.월]\s*(\d{1,2})", str(s))
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return None
    return f"{y:04d}{mo:02d}{d:02d}"


def pick(items, *pats: str, first: bool = False):
    """항목 경로에 패턴이 모두 들어간 값. 기본은 마지막 매칭이다.

    정정본은 같은 항목이 정정사항 표와 본문에 나온다. 본문이 뒤에 있으므로
    마지막을 잡아야 정정 후 값을 읽는다.
    """
    found = None
    for _seq, name, val in items:
        sq = _sq(name)
        if all(_sq(p) in sq for p in pats):
            if first:
                return val
            found = val
    return found


def correction_reason(items) -> str | None:
    """정정사유를 찾는다.

    정정사항 표는 네 칸이다.  항목 │ 정정사유 │ 정정 전 │ 정정 후
    값 앞 칸을 이름으로 잡는 규칙 때문에 사유가 항목 경로 안으로 들어간다.

        name = "1. 신주의 종류와 수 > 신주 및 전환사채 발행금지가처분 인용 결정에 따른 계약 해제"
        value = "1,230,000"

    같은 사유가 표의 모든 행에 반복되므로, 경로 조각 중 가장 자주 나오는
    긴 문장을 사유로 본다.
    """
    from collections import Counter
    cnt: Counter = Counter()
    for _seq, name, _val in items[:80]:      # 정정사항 표는 문서 앞쪽에 있다
        for part in name.split(">"):
            p = part.strip()
            # 항목명은 짧고 번호가 붙는다. 사유는 길고 서술형이다
            if len(p) < 8 or re.match(r"^\d{1,2}\s*[.．]", p):
                continue
            if re.fullmatch(r"[\d,.\s%()-]+", p):
                continue
            cnt[p] += 1
    if not cnt:
        return None
    # 철회를 뜻하는 말이 들어갔으면 한 번만 나와도 채택한다.
    # OCI홀딩스 건은 "주식매매 및 현물출자계약 해제에 따른 유상증자 결정 철회"가
    # 표 첫 행에만 있어서 반복 조건으로는 걸리지 않았다.
    for p, _n in cnt.most_common():
        if _WITHDRAWN.search(p):
            return p
    # 그 외에는 두 번 이상 반복된 것만. 한 번뿐이면 항목명일 가능성이 높다
    top = [(p, n) for p, n in cnt.most_common() if n >= 2]
    return top[0][0] if top else None


def extract(items: list[tuple[int, str, str]], kind: str) -> dict:
    """항목-값 목록에서 컬럼에 담을 값을 뽑는다."""
    out: dict = {"major_kind": kind}

    # 회사합병 서식에는 이사회결의일이 넷이다. 합병결정·합병승인·합병보고가
    # 각각 있고 뒤의 둘은 미래 날짜다. 마지막 매칭을 쓰면 그것을 잡는다.
    # 번호가 붙은 표준 항목 "14. 이사회결의일(결정일)" 을 먼저 찾는다.
    out["decided_at"] = (to_date(pick(items, "이사회결의일(결정일)"))
                         or to_date(pick(items, "이사회결의일", first=True))
                         or to_date(pick(items, "결정일", first=True)))
    out["start_date"] = to_date(pick(items, "시작일"))
    out["end_date"] = to_date(pick(items, "종료일"))

    # 자금 용도 여섯 갈래
    total = 0
    for col, label in USE_KEYS.items():
        v = to_int(pick(items, "자금조달의목적", label))
        out[col] = v
        total += v or 0
    out["use_total"] = total or None

    # 주된 금액
    src = AMOUNT_SRC.get(kind)
    if src:
        out["amount_krw"] = to_int(pick(items, *src))
        out["amount_src"] = " > ".join(src)
    if out.get("amount_krw") is None and kind == "유상증자결정":
        # 유상증자는 총액 항목이 없다. 신주 수 × 발행가액으로 만든다
        s = to_int(pick(items, "신주의종류와수", "보통주식"))
        p = to_int(pick(items, "신주발행가액", "보통주식"))
        if s and p:
            out["amount_krw"] = s * p
            out["amount_src"] = "신주 수 × 발행가액"
    if out.get("amount_krw") is None and total:
        out["amount_krw"] = total
        out["amount_src"] = "자금조달의 목적 합계"

    # 외화 발행
    fx = pick(items, "권면", "총액", "통화단위")
    if fx:
        out["amount_foreign"] = to_float(fx)
    cur = pick(items, "기준환율") or pick(items, "통화단위")
    if cur and not re.fullmatch(r"[\d,.\s-]*", str(cur)):
        m = re.search(r"[A-Z]{3}", str(cur))
        out["currency"] = m.group() if m else None

    out["shares_common"] = to_int(pick(items, "신주의종류와수", "보통주식")) or \
        to_int(pick(items, "처분예정주식", "보통주식")) or \
        to_int(pick(items, "취득예정주식", "보통주식"))
    out["shares_other"] = to_int(pick(items, "신주의종류와수", "기타주식")) or \
        to_int(pick(items, "처분예정주식", "기타주식")) or \
        to_int(pick(items, "취득예정주식", "기타주식"))
    out["shares_before"] = to_int(pick(items, "증자전발행주식총수", "보통주식"))
    out["price_share"] = to_int(pick(items, "신주발행가액", "보통주식")) or \
        to_int(pick(items, "처분대상주식가격", "보통주식")) or \
        to_int(pick(items, "취득예정금액", "1주당")) or None

    # 자기주식 처분 — D1 이 요구한 건별 판정 재료
    if "자기주식처분" in kind:
        out["disposal_purpose"] = pick(items, "처분목적")
        out["method_market"] = to_int(pick(items, "처분방법", "시장을통한매도"))
        out["method_block"] = to_int(pick(items, "처분방법", "시간외대량매매"))
        out["method_otc"] = to_int(pick(items, "처분방법", "장외처분"))
        out["method_etc"] = to_int(pick(items, "처분방법", "기타"))

    reason = correction_reason(items)
    out["correct_reason"] = reason
    out["is_withdrawn"] = int(bool(reason and _WITHDRAWN.search(reason)))

    # 검산 — 유형별로 성립하는 관계가 다르다
    out["check_ok"] = _verify(out, items, kind)
    return out


def _verify(out: dict, items, kind: str):
    """검산. 맞으면 1, 틀리면 0, 검산할 관계가 없으면 None."""
    if kind in ("전환사채권발행결정", "교환사채권발행결정",
                "상각형조건부자본증권발행결정", "자본으로인정되는채무증권발행결정"):
        tot = to_int(pick(items, "사채의권면", "총액"))
        if tot and out.get("use_total"):
            return int(abs(out["use_total"] - tot) < 1)
        return None
    if kind == "유상증자결정":
        s, p, u = out.get("shares_common"), out.get("price_share"), out.get("use_total")
        if s and p and u:
            gross = s * p
            # 용도 합계는 발행제비용을 뺀 금액이라 조달금액보다 작은 것이 보통이다.
            # LG씨엔에스 건이 1.0% 적었고 대표주관 3사가 붙은 대형 공모였다.
            # 반대로 미세하게 큰 경우도 있는데, 발행가액이 반올림된 값이라
            # 곱셈이 근사치이기 때문이다. 한화오션 건의 차이가 20,451원이다.
            return int(-gross * 0.0001 <= gross - u <= gross * 0.03)
        return None
    if "자기주식처분" in kind or "자기주식취득결정" in kind:
        q = out.get("shares_common")
        p = out.get("price_share")
        a = out.get("amount_krw")
        if q and p and a:
            return int(abs(q * p - a) <= max(1, a * 0.001))
        return None
    return None
