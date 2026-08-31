"""사전의 조각별 문서빈도를 전수로 잰다. 무의미한 조각을 걷어내려고 만든다.

`review_top.csv` 를 보고 두 가지가 드러났다.

    당분기말  →  당분 · 기말      '당분' 은 설탕이라는 뜻이다
    당분기    →  당분             형태소 분석기가 잘못 자른 조각이다
    전분기    →  전분             '전분' 은 녹말이다
    기업명    →  기업             너무 포괄적이라 변별력이 없다

앞의 셋은 원어 밖에서 거의 안 쓰이는 조각이고, 뒤는 반대로 어디에나 있는
조각이다. 양 끝이 다 쓸모없다.

    거의 안 쓰인다   형태소 분석기가 잘못 자른 것이다. 검색어가 될 수 없다
    너무 흔하다      거의 모든 조각이 걸린다. 변별력이 없다

## 앞서 실패한 선별과 무엇이 다른가

    앞서   검색을 개선하려고 사전 후보를 골랐다
           BM25 점수를 단일 지표로 근사하려다 두 번 어긋났다

    지금   무의미한 토큰을 줄인다
           IDF 가 낮아 원래 기여가 없던 것들이라 검색 품질은 거의 안 변한다
           안전한 정리다

그래도 적용 전후로 BM25 를 돌려 나빠지는 게 없는지 확인해야 한다.

## 임계값을 미리 정하지 않는다

분포를 재고, 지적받은 사례가 어디에 놓이는지 본 뒤에 정한다.
"""
import csv
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8")

from db import connect

OUT = ROOT / "data" / "terms"
# 지적받은 사례. 규칙이 이것들을 제대로 가리는지 확인용이다.
WATCH = ["당분", "전분", "기말", "기업", "기타", "자산", "채권", "순이익",
         "잉여금", "총계", "상장", "이행", "현황", "장부", "금액"]


def main() -> int:
    import ahocorasick

    src = OUT / "dictionary.csv"
    terms: dict[str, list[str]] = {}
    with src.open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            terms[r["term"]] = r["parts"].split()
    parts = sorted({p for ps in terms.values() for p in ps})
    print(f"사전 {len(terms):,}종 · 서로 다른 조각 {len(parts):,}개")

    A = ahocorasick.Automaton()
    for w in set(parts) | set(terms):
        A.add_word(w, w)
    A.make_automaton()

    df = Counter()
    t0 = time.time()
    n = 0
    con = connect()
    cur = con.execute("SELECT text FROM chunk")
    while True:
        rows = cur.fetchmany(2000)
        if not rows:
            break
        for r in rows:
            n += 1
            for w in {v for _, v in A.iter(r["text"])}:
                df[w] += 1
        if n % 40000 == 0:
            print(f"   {n:,}조각 · {time.time()-t0:.0f}초", flush=True)
    print(f"   조각 {n:,} · {time.time()-t0:.0f}초")

    # 조각마다 그것을 쓰는 용어들의 문서빈도 합을 함께 본다.
    # 조각 DF 가 그 합보다 크게 높으면 원어 밖에서도 널리 쓰인다는 뜻이다.
    rows_out = []
    for p in parts:
        owners = [t for t, ps in terms.items() if p in ps]
        own_df = max((df.get(t, 0) for t in owners), default=0)
        d = df.get(p, 0)
        rows_out.append({
            "part": p, "part_df": d, "n_owner": len(owners),
            "owner_max_df": own_df,
            "share": round(d / n, 4),
            "vs_owner": round(d / own_df, 2) if own_df else 0.0,
        })
    rows_out.sort(key=lambda r: -r["part_df"])

    pth = OUT / "parts_df.csv"
    with pth.open("w", encoding="utf-8-sig", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=list(rows_out[0]))
        wr.writeheader()
        wr.writerows(rows_out)
    print(f"\n   {pth}  {len(rows_out):,}행")

    ds = sorted(r["part_df"] for r in rows_out)
    print(f"\n── 조각 문서빈도 분포  (전체 조각 {n:,})")
    for q in (0.1, 0.25, 0.5, 0.75, 0.9, 0.95, 0.99):
        v = ds[int(len(ds) * q)]
        print(f"   {int(q*100):>3}분위  {v:>9,}  ({v/n:>6.2%})")

    print("\n── 흔한 쪽 (변별력이 없다)")
    for r in rows_out[:15]:
        print(f"   {r['part']:<12}{r['part_df']:>9,} ({r['share']:>6.2%}) "
              f"· 쓰는 용어 {r['n_owner']:>4}개")

    print("\n── 드문 쪽 (잘못 잘린 조각일 수 있다)")
    for r in rows_out[-15:]:
        print(f"   {r['part']:<12}{r['part_df']:>9,} ({r['share']:>6.2%}) "
              f"· 쓰는 용어 {r['n_owner']:>4}개 · 원어 대비 {r['vs_owner']:>6.2f}")

    print("\n── 지적받은 사례가 어디에 놓이는가")
    idx = {r["part"]: r for r in rows_out}
    print(f"   {'조각':<10}{'DF':>9}{'비율':>8}{'원어대비':>9}   쓰는 용어")
    for w in WATCH:
        r = idx.get(w)
        if not r:
            print(f"   {w:<10}{'(조각 목록에 없음)':>26}")
            continue
        print(f"   {w:<10}{r['part_df']:>9,}{r['share']:>8.2%}"
              f"{r['vs_owner']:>9.2f}   {r['n_owner']}개")
    return 0


if __name__ == "__main__":
    sys.exit(main())
