"""골든 데이터셋의 빈 칸을 XBRL 태그로 채운다.

사람이 원문을 읽어 적은 값과 성격이 다르다. 사람이 채운 것은 파서와
완전히 다른 경로라 어떤 오류든 드러나지만, 이쪽은 태그를 읽은 것이라
태그 자체가 틀렸으면 같이 틀린다.

그래도 값이 있다. 표 파싱 경로를 검증하는 데는 쓸 수 있고, 태그 경로가
바뀌었을 때 회귀를 잡는다. 출처를 비고에 남겨 구분한다.

표 파싱을 쓰지 않는다. 그것을 쓰면 검사받을 것으로 정답지를 만드는 셈이다.
"""
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8")

from lxml import etree
from corpus import read_raw
from db import connect
from fsdoc import _P
from fsvalue import from_tags, pick

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
WANT = {"연간": "annual", "누적": "cumulative", "3개월": "quarter", "분기": "quarter"}
UNIT = {1: "원", 1000: "천원", 1_000_000: "백만원"}


def main(apply: bool = False):
    con = connect()
    tg = {t["no"]: t for t in csv.DictReader(
        (ROOT / "data/golden/golden_targets.csv").open(encoding="utf-8-sig"))}
    path = ROOT / "data/golden/golden_values.csv"
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))

    cache = {}
    filled = skipped = 0
    for r in rows:
        if r["값"].strip():
            continue
        t = tg.get(r["no"])
        if not t:
            continue
        did = t["doc_id"]
        if did not in cache:
            d = con.execute("SELECT file_path,file_format FROM document WHERE doc_id=?",
                            (did,)).fetchone()
            if not d or d["file_format"] != "xml":
                cache[did] = None
            else:
                try:
                    raw = read_raw(d["file_path"])
                    cache[did] = from_tags(
                        etree.fromstring(raw.encode("utf-8"), parser=_P))
                except Exception:
                    cache[did] = None
        got = cache[did]
        if not got:
            skipped += 1
            continue
        code = ITEM.get(r["항목"].strip())
        if not code:
            skipped += 1
            continue
        want = None if code.startswith("total_") else WANT.get(r["기간유형"].strip())
        g = pick(got, code, period=want)
        if g is None or g.get("mult") is None:
            skipped += 1
            continue
        r["값"] = f"{g['value']:,}"
        r["단위"] = UNIT.get(g["mult"], str(g["mult"]))
        r["비고"] = "xbrl 자동"
        filled += 1
        print(f"   no{r['no']:>3} {r['기업']:<14}{r['항목']:<14}{g['value']:>20,}  {r['단위']}")

    print(f"\n채움 {filled} · 건너뜀 {skipped}")
    if apply and filled:
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print("golden_values.csv 갱신")
    elif filled:
        print("--apply 를 주면 파일에 씁니다")
    return 0


if __name__ == "__main__":
    sys.exit(main(apply="--apply" in sys.argv))
