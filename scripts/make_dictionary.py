"""후보에서 잡음을 걷어내 최종 사전을 만든다.

`build_terms.py` 가 뽑은 14,933개에는 표 서식과 날짜가 섞여 있다.

    20230101기초자본     날짜와 항목이 붙었다
    기준일2023년12월31일   표 머리글이다
    수준1 · 기타2 · 등급1  표 항목 번호다
    3개월 · 2023년        기간 표시다

선별 지표는 쓰지 않는다. "쪼개면 나빠지는 것" 을 지표로 가리려다 두 번
어긋났다(독립비율·혼동도). 대신 넣을 자격만 본다. 사전에 넣어서 나빠진
사례를 실측에서 하나도 못 찾았기 때문이다.

    compare_tokenize 결과 — 사전을 넣어 나빠진 항목 0
    자산총계  쪼개면 3/10 → 사전 10/10
    부채총계  쪼개면 9/10 → 사전 10/10
    나머지    변화 없음

산출물은 둘이다.

    dictionary.csv   사전에 넣을 낱말과 그 조각
    review_top.csv   빈도 상위 300개. 사람이 눈으로 볼 것
"""
import csv
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8")

OUT = ROOT / "data" / "terms"

# ── 제외 규칙 ────────────────────────────────────────────────────────
# 연속 숫자 4자리 이상. 날짜와 코드다. "20230101기초자본" · "기준일2023년"
_LONGNUM = re.compile(r"\d{4,}")
# 숫자로 시작하는 짧은 것. 기간 표시다. "3개월" · "1년이내" · "5년초과"
_NUMHEAD = re.compile(r"^\d")
NUMHEAD_MAXLEN = 7
# 짧은데 숫자가 든 것. 표 항목 번호다. "수준1" · "기타2" · "등급1"
_HASNUM = re.compile(r"\d")
HASNUM_MINLEN = 8
# 표 서식 낱말. 계정명이 아니고 쪼개져도 뜻이 안 변한다.
#
# 기간 표시어는 여기 넣지 않는다. 빼면 잘못 잘린 채로 남기 때문이다.
#
#     당분기  →  당분 · 기      '당분' 은 설탕이라는 뜻이다
#     전기말  →  전기 · 말      '말' 이 한 글자라 버려져 '전기' 만 남는다
#
# 표 머리글이라 검색어로는 잘 안 쓰이지만, 사전에 넣어 두면 엉뚱한 토큰이
# 만들어지는 것을 막는다. 넣어서 손해 보는 경우는 실측에서 못 찾았다.
_FORM = re.compile(r"^(구분|구 분|합계|합 계|소계|소 계|비고|해당사항없음"
                   r"|해당없음|해당사항)$")

MIN_FREQ = 20


def keep(term: str, freq: int) -> tuple[bool, str]:
    """넣을지 판정하고 뺄 때는 이유를 낸다."""
    if freq < MIN_FREQ:
        return False, "빈도 미달"
    if _LONGNUM.search(term):
        return False, "연속 숫자 4자리 이상"
    if _NUMHEAD.match(term) and len(term) <= NUMHEAD_MAXLEN:
        return False, "숫자로 시작하는 짧은 말"
    if _HASNUM.search(term) and len(term) < HASNUM_MINLEN:
        return False, "짧은데 숫자가 들었다"
    if _FORM.match(term):
        return False, "표 서식 낱말"
    return True, ""


def main() -> int:
    src = OUT / "candidates_raw.csv"
    rows = list(csv.DictReader(src.open(encoding="utf-8-sig")))
    print(f"후보 {len(rows):,}행")

    keepers, dropped = [], []
    for r in rows:
        ok, why = keep(r["term"], int(r["freq"]))
        (keepers if ok else dropped).append((r, why))

    # lookup.py 가 정한 말은 규칙과 무관하게 넣는다
    from lookup import LABEL, KEYWORD
    have = {r["term"] for r, _ in keepers}
    forced = []
    for v in list(LABEL.values()) + list(KEYWORD):
        v = re.sub(r"\s*\(.*?\)\s*", "", v).strip().replace(" ", "")
        if 3 <= len(v) <= 14 and v not in have:
            forced.append(v)
    if forced:
        from kiwipiepy import Kiwi
        kiwi = Kiwi()
        for v in sorted(set(forced)):
            parts = [t.form for t in kiwi.tokenize(v)
                     if (t.tag.startswith("NN") or t.tag in ("XR", "SL", "SH"))
                     and len(t.form) > 1]
            if len(kiwi.tokenize(v)) > 1:
                keepers.append(({"term": v, "freq": "0",
                                 "parts": " ".join(parts)}, ""))
                have.add(v)

    print(f"\n── 제외 사유별")
    from collections import Counter
    c = Counter(w for _, w in dropped)
    for w, k in c.most_common():
        print(f"   {w:<22}{k:>7,}")
    print(f"   {'남은 것':<22}{len(keepers):>7,}")

    keepers.sort(key=lambda x: -int(x[0]["freq"]))
    p = OUT / "dictionary.csv"
    with p.open("w", encoding="utf-8-sig", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["term", "freq", "parts"])
        for r, _ in keepers:
            # 조각의 중복을 없앤다. "현금및현금성자산" 이 현금·현금·자산 으로
            # 나와 같은 낱말이 두 번 들어가는 경우가 있다.
            seen, uniq = set(), []
            for x in r["parts"].split():
                if x not in seen:
                    seen.add(x)
                    uniq.append(x)
            wr.writerow([r["term"], r["freq"], " ".join(uniq)])
    print(f"\n   {p}  {len(keepers):,}행")

    p2 = OUT / "dropped.csv"
    with p2.open("w", encoding="utf-8-sig", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["term", "freq", "why"])
        for r, w in sorted(dropped, key=lambda x: -int(x[0]["freq"])):
            wr.writerow([r["term"], r["freq"], w])
    print(f"   {p2}  {len(dropped):,}행")

    p3 = OUT / "review_top.csv"
    with p3.open("w", encoding="utf-8-sig", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["순위", "term", "freq", "parts"])
        for i, (r, _) in enumerate(keepers[:300], 1):
            wr.writerow([i, r["term"], r["freq"], r["parts"]])
    print(f"   {p3}  300행  ← 눈으로 확인할 것")

    print("\n── 남은 것 상위 25")
    for r, _ in keepers[:25]:
        print(f"   {int(r['freq']):>7,}  {r['term']:<20} → {r['parts']}")
    print("\n── 뺀 것 상위 15")
    for r, w in sorted(dropped, key=lambda x: -int(x[0]["freq"]))[:15]:
        print(f"   {int(r['freq']):>7,}  {r['term']:<20} {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
