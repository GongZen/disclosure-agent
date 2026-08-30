"""후보 용어와 그 조각의 등장 빈도를 재서 독립비율을 낸다.

`build_terms.py` 가 뽑은 후보를 받아, 각 조각을 부분 토큰으로 낼지 정할
근거를 만든다.

## 왜 재는가

복합 용어를 사전에 넣으면 하나로 잡히지만 부분 검색을 잃는다.

    문서 "당기순이익" → 당기순이익
    질의 "순이익"     → 순이익            안 걸린다

그래서 원래 형태와 조각을 둘 다 토큰으로 낸다. 다만 아무 조각이나 내면
엉뚱한 것이 걸린다.

    "매출채권" 을 쪼개 "매출" 을 내면
    "매출액이 얼마야" 질의에 매출채권 조각이 걸린다. 다른 계정인데도

## 무엇을 재는가

    ①  용어가 든 조각 수
    ②  조각은 있고 용어는 없는 조각 수
    독립비율 = ② ÷ ①

낮으면 그 조각이 거의 이 용어 안에서만 쓰인다는 뜻이라 쪼개도 안전하다.
높으면 용어 밖에서 자주 쓰여 쪼개면 잡음이 크다.

## 어떻게 재는가

Aho-Corasick 으로 한 번만 순회한다. 후보가 15,000개고 조각이 146,637개라
후보마다 훑으면 조 단위 비교가 된다. 다중 패턴 매칭은 텍스트를 한 번
지나가며 모든 패턴을 동시에 찾는다.

## 한계

한 조각에 "매출채권" 과 "매출액" 이 함께 있으면 ②에서 빠진다. 재무제표
조각에는 둘 다 있는 경우가 흔하므로 실제 잡음보다 낮게 나온다. 이 값은
잡음의 하한이고, 기준값을 그만큼 낮게 잡아야 한다.
"""
import csv
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8")

from db import connect

OUT = ROOT / "data" / "terms"


def main(limit: int | None = None) -> int:
    import ahocorasick

    src = OUT / "candidates_raw.csv"
    if not src.exists():
        print(f"{src} 가 없다. build_terms.py 를 먼저 돌린다.")
        return 1

    terms: dict[str, list[str]] = {}
    with src.open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            parts = [p for p in r["parts"].split() if p]
            if parts:
                terms[r["term"]] = parts
    if limit:
        terms = dict(list(terms.items())[:limit])
    print(f"후보 {len(terms):,}종")

    # 찾을 문자열을 모은다. 용어와 그 조각 전부다.
    words = set(terms)
    for ps in terms.values():
        words.update(ps)
    print(f"찾을 문자열 {len(words):,}개  (용어 + 조각)")

    A = ahocorasick.Automaton()
    for w in words:
        A.add_word(w, w)
    A.make_automaton()
    print("자동자 준비 완료")

    # 조각마다 어떤 문자열이 들어 있는지 모아 센다
    df = Counter()                      # 문자열 → 그것이 든 조각 수
    pair = defaultdict(Counter)         # 용어 → 함께 든 조각의 조각 수
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
            found = {v for _, v in A.iter(r["text"])}
            for w in found:
                df[w] += 1
            # 이 조각에 든 용어마다, 그 조각도 함께 있는지 기록한다
            for t in found & terms.keys():
                for p in terms[t]:
                    if p in found:
                        pair[t][p] += 1
        if n % 40000 == 0:
            print(f"   {n:,}조각 · {time.time()-t0:.0f}초", flush=True)
    print(f"   조각 {n:,} · {time.time()-t0:.0f}초")

    print("\n── 독립비율을 낸다")
    rows_out = []
    for t, parts in terms.items():
        nt = df.get(t, 0)
        if not nt:
            continue
        for p in parts:
            npart = df.get(p, 0)
            together = pair[t].get(p, 0)
            alone = npart - together        # 조각은 있고 용어는 없는 조각 수
            rows_out.append({
                "term": t, "part": p,
                "term_df": nt, "part_df": npart,
                "alone": alone,
                "ratio": round(alone / nt, 3),
            })
    rows_out.sort(key=lambda r: (-r["term_df"], r["term"]))

    p = OUT / "term_parts.csv"
    with p.open("w", encoding="utf-8-sig", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=["term", "part", "term_df",
                                           "part_df", "alone", "ratio"])
        wr.writeheader()
        wr.writerows(rows_out)
    print(f"   {p}  {len(rows_out):,}행")

    print("\n── 독립비율 분포")
    rs = sorted(r["ratio"] for r in rows_out)
    for q in (0.1, 0.25, 0.5, 0.75, 0.9, 0.95):
        print(f"   {int(q*100):>3}분위  {rs[int(len(rs)*q)]:>8.2f}")
    for th in (0.1, 0.3, 0.5, 0.7, 1.0, 2.0, 5.0):
        k = sum(1 for r in rs if r < th)
        print(f"   {th:>5.1f} 미만  {k:>7,} ({k/len(rs):>5.1%})")

    print("\n── 확인용 표본")
    want = ["매출채권", "당기순이익", "유형자산", "영업이익", "이익잉여금",
            "자본총계", "미지급비용", "매입채무", "자산총계", "현금및현금성자산"]
    for w in want:
        rs2 = [r for r in rows_out if r["term"] == w]
        if not rs2:
            print(f"   {w:<16} (후보에 없음)")
            continue
        s = " · ".join(f"{r['part']} {r['ratio']:.2f}" for r in rs2)
        print(f"   {w:<16} df {rs2[0]['term_df']:>7,}   {s}")
    return 0


if __name__ == "__main__":
    lim = None
    for a in sys.argv[1:]:
        if a.startswith("--limit="):
            lim = int(a.split("=")[1])
    sys.exit(main(limit=lim))
