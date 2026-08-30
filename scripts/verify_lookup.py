"""S6 값 조회를 골든 데이터셋으로 검증한다.

사람이 원문을 읽어 적은 값이 정답지다. 질의를 흉내 내어 조회했을 때
그 값이 결과에 들어 있는지 본다.
"""
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8")

from db import connect
from resolve import Resolver
from lookup import lookup, fmt

ITEM = {
    "자산총계": "total_assets", "부채총계": "total_liabilities",
    "자본총계": "total_equity",
    "매출액": "revenue", "수익(매출액)": "revenue", "영업수익": "revenue",
    "매출액 또는 영업수익": "revenue",
    "영업이익": "operating_income",
    "당기순이익": "net_income", "분기순이익": "net_income",
    "반기순이익": "net_income", "연결당기순이익": "net_income",
    "순이자손익": "net_interest_income", "순이자이익": "net_interest_income",
    "보험서비스결과": "insurance_result", "보험손익": "insurance_result",
}


def to_num(s):
    s = (s or "").replace(",", "").strip()
    try:
        return int(s)
    except ValueError:
        return None


def main():
    con = connect()
    rz = Resolver(con)
    tg = {t["no"]: t for t in csv.DictReader(
        (ROOT / "data/golden/golden_targets.csv").open(encoding="utf-8-sig"))}
    rows = list(csv.DictReader(
        (ROOT / "data/golden/golden_values.csv").open(encoding="utf-8-sig")))

    ok = ng = skip = 0
    bad = []
    for r in rows:
        gold = to_num(r["값"])
        code = ITEM.get(r["항목"].strip())
        if gold is None or not code:
            skip += 1
            continue
        t = tg.get(r["no"])
        x = rz.resolve(r["기업"])
        if x["status"] != "exact":
            skip += 1
            continue
        year = int(r["회계연도"]) if r["회계연도"].strip() else None
        # 같은 연도에 사업·반기·분기가 다 있으므로 그 문서의 기준월로 좁힌다.
        # 재무상태표는 전부 instant 라 월로 구분하지 않으면 네 건이 나온다.
        d = con.execute("SELECT base_month FROM document WHERE doc_id=?",
                        (t["doc_id"],)).fetchone() if t else None
        month = int(d["base_month"]) if d and d["base_month"] else None
        got = lookup(con, [x["corp_code"]], [code],
                     years=[year] if year else None,
                     months=[month] if month else None,
                     basis=r["기준"] or "연결")
        # 골든은 표기 단위 그대로다. value_raw 와 견준다.
        hit = [g for g in got if g["value_raw"] == gold]
        if hit:
            ok += 1
        else:
            ng += 1
            bad.append((r["no"], r["기업"], r["항목"], gold,
                        [g["value_raw"] for g in got][:4]))
    print(f"일치 {ok} · 불일치 {ng} · 건너뜀 {skip}")
    if bad:
        print("\n── 불일치")
        for no, corp, item, g, cand in bad[:12]:
            print(f"   no{no} {corp} {item}")
            print(f"      골든 {g:,}")
            print(f"      조회 {[f'{c:,}' for c in cand]}")
    return 0 if ng == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
