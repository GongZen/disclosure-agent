"""fsvalue 로 뽑은 값을 골든 데이터셋과 대조한다.

사람이 원문을 읽어 적은 값이 정답지다. 파서 결과를 그것과 견준다.
어느 쪽이 틀렸는지는 이 스크립트가 정하지 않는다. 목록으로 낸다.
"""
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8")

from corpus import read_raw
from db import connect
from fsvalue import extract, pick, to_num

# 골든의 항목 이름 → item_code
ITEM = {
    "자산총계": "total_assets", "부채총계": "total_liabilities",
    "자본총계": "total_equity",
    "매출액": "revenue", "수익(매출액)": "revenue", "영업수익": "revenue",
    "매출액 또는 영업수익": "revenue",
    "영업이익": "operating_income",
    "당기순이익": "net_income", "분기순이익": "net_income",
    "반기순이익": "net_income", "연결당기순이익": "net_income",
    "당기순이익(손실)": "net_income",
    "순이자손익": "net_interest_income", "순이자이익": "net_interest_income",
    "보험서비스결과": "insurance_result", "보험손익": "insurance_result",
}
# 골든의 기간유형 → period_type
WANT = {"연간": "annual", "누적": "cumulative", "3개월": "quarter", "분기": "quarter"}


def main():
    con = connect()
    tg = {t["no"]: t for t in csv.DictReader(
        (ROOT / "data/golden/golden_targets.csv").open(encoding="utf-8-sig"))}
    rows = list(csv.DictReader(
        (ROOT / "data/golden/golden_values.csv").open(encoding="utf-8-sig")))

    ok = ng = skip = 0
    bad = []
    for no in sorted({r["no"] for r in rows}, key=lambda x: int(x)):
        vals = [r for r in rows if r["no"] == no and r["값"].strip()]
        if not vals:
            continue
        t = tg.get(no)
        d = con.execute("SELECT file_path,file_format FROM document WHERE doc_id=?",
                        (t["doc_id"],)).fetchone()
        if not d or d["file_format"] != "xml":
            skip += len(vals)
            print(f"── no {no} {t['기업']} {t['접수일']}  PDF. 건너뜀")
            continue
        got = extract(read_raw(d["file_path"]))
        src = got[0]["source"] if got else "-"
        print(f"── no {no} {t['기업']} {t['접수일']}  [{src}]")
        for r in vals:
            code = ITEM.get(r["항목"].strip())
            gold = to_num(r["값"])
            if not code or gold is None:
                print(f"   {r['항목']:<14} 항목 사전에 없음")
                skip += 1
                continue
            # 재무상태표는 시점의 값이라 기간을 묻지 않는다
            want = None if code.startswith("total_") else WANT.get(r["기간유형"].strip())
            g = pick(got, code, period=want)
            if g is None:
                print(f"   {r['항목']:<14} 못 찾음        골든 {gold:>18,}")
                ng += 1
                bad.append((no, t["기업"], r["항목"], None, gold))
                continue
            v = g["value"]
            if v == gold:
                ok += 1
                print(f"   {r['항목']:<14} OK   {v:>18,}")
            else:
                ng += 1
                bad.append((no, t["기업"], r["항목"], v, gold))
                print(f"   {r['항목']:<14} 다름 {v:>18,}  골든 {gold:>18,}")
    print(f"\n일치 {ok} · 불일치 {ng} · 건너뜀 {skip}")
    if bad:
        print("── 불일치 목록")
        for no, corp, item, v, gold in bad:
            print(f"   no{no} {corp} {item}  파서 {v}  골든 {gold}")
    return 0 if ng == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
