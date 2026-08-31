"""복합어를 쪼갰을 때 실제로 검색이 나빠지는지 가르는 지표를 만든다.

`compare_tokenize.py` 로 재니 같은 "쪼개짐" 인데 결과가 갈렸다.

    자산총계   쪼개면 3/10   부문별 보고 조각이 상위를 차지한다
    매출채권   쪼개도 10/10  문제가 없다

앞서 쓰던 독립비율은 조각을 하나씩 봤다. 그것으로는 이 차이를 못 가른다.

    자산총계   자산 12.25 · 총계 0.74
    매출채권   매출  1.04 · 채권 1.04

실제 원인은 조각들이 함께 나타나는 데 있다. BM25 는 질의의 낱말이 전부 든
조각에 높은 점수를 준다. "자산" 과 "총계" 가 둘 다 여러 번 나오는 부문별
보고 조각은, 정작 "자산총계" 가 한 번 적힌 재무상태표보다 점수가 높다.

## 그래서 재는 것

    혼동 조각 수  = 모든 조각이 들어 있으면서 원어는 없는 조각 수
    혼동도       = 혼동 조각 수 ÷ 원어가 든 조각 수

    높다   쪼개면 그만큼 엉뚱한 조각이 경쟁한다. 사전에 넣어야 한다
    낮다   쪼개도 원어 조각이 우세하다. 사전이 필요 없다

빈도도 함께 낸다. BM25 는 낱말이 반복될수록 점수를 올리므로, 혼동 조각에서
조각들이 몇 번씩 나오는지가 실제 경쟁력을 좌우한다.
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
    terms: dict[str, list[str]] = {}
    with src.open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            parts = [p for p in r["parts"].split() if p]
            if len(parts) >= 2:                 # 조각이 둘 이상인 것만
                terms[r["term"]] = parts
    if limit:
        terms = dict(list(terms.items())[:limit])
    print(f"후보 {len(terms):,}종  (조각 2개 이상)")

    words = set(terms)
    for ps in terms.values():
        words.update(ps)
    A = ahocorasick.Automaton()
    for w in words:
        A.add_word(w, w)
    A.make_automaton()
    print(f"찾을 문자열 {len(words):,}개 · 자동자 준비 완료")

    # 조각 → 그 조각을 쓰는 용어. 조각마다 후보 전부를 훑으면 24억 번이 된다.
    # 실제로 등장한 조각에서 역으로 용어를 찾으면 몇 십 배 빠르다.
    part2terms = defaultdict(set)
    for t, parts in terms.items():
        for p in parts:
            part2terms[p].add(t)

    term_df = Counter()          # 원어가 든 조각 수
    conf_df = Counter()          # 조각은 다 있고 원어는 없는 조각 수
    conf_tf = Counter()          # 그 조각들에서 조각이 나온 총 횟수
    t0 = time.time()
    n = 0
    con = connect()
    cur = con.execute("SELECT text FROM chunk WHERE char_len >= 100")
    while True:
        rows = cur.fetchmany(2000)
        if not rows:
            break
        for r in rows:
            n += 1
            cnt = Counter(v for _, v in A.iter(r["text"]))
            found = set(cnt)
            for t in found & terms.keys():
                term_df[t] += 1
            cand = set()
            for w in found:
                cand |= part2terms.get(w) or set()
            for t in cand - found:
                parts = terms[t]
                if all(p in found for p in parts):
                    conf_df[t] += 1
                    conf_tf[t] += sum(cnt[p] for p in parts)
        if n % 40000 == 0:
            print(f"   {n:,}조각 · {time.time()-t0:.0f}초", flush=True)
    print(f"   조각 {n:,} · {time.time()-t0:.0f}초")

    rows_out = []
    for t, parts in terms.items():
        td = term_df.get(t, 0)
        if td < 20:
            continue
        cd = conf_df.get(t, 0)
        rows_out.append({
            "term": t, "parts": " ".join(parts),
            "term_df": td, "confuse_df": cd,
            "confuse_ratio": round(cd / td, 3),
            "confuse_tf_avg": round(conf_tf.get(t, 0) / cd, 1) if cd else 0.0,
        })
    rows_out.sort(key=lambda r: -r["confuse_ratio"])

    p = OUT / "confusion.csv"
    with p.open("w", encoding="utf-8-sig", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=list(rows_out[0]))
        wr.writeheader()
        wr.writerows(rows_out)
    print(f"\n   {p}  {len(rows_out):,}행")

    rs = sorted(r["confuse_ratio"] for r in rows_out)
    print("\n── 혼동도 분포")
    for q in (0.1, 0.25, 0.5, 0.75, 0.9, 0.95, 0.99):
        print(f"   {int(q*100):>3}분위  {rs[int(len(rs)*q)]:>9.2f}")
    for th in (0.5, 1.0, 2.0, 5.0, 10.0, 30.0):
        k = sum(1 for r in rs if r >= th)
        print(f"   {th:>5.1f} 이상  {k:>7,} ({k/len(rs):>5.1%})")

    print("\n── 실측으로 결과를 아는 것들")
    known = {"자산총계": "쪼개면 3/10", "부채총계": "쪼개면 9/10",
             "매출채권": "쪼개도 10/10", "영업이익": "쪼개도 10/10",
             "미지급비용": "접두사 문제. 규칙으로 해결",
             "당기순이익": "쪼개도 10/10", "매출원가": "쪼개도 10/10",
             "이익잉여금": "", "유형자산": "", "무형자산": ""}
    idx = {r["term"]: r for r in rows_out}
    print(f"   {'용어':<14}{'원어df':>8}{'혼동df':>8}{'혼동도':>8}{'혼동TF':>8}   실측")
    for t, note in known.items():
        r = idx.get(t)
        if not r:
            print(f"   {t:<14}{'(후보에 없음)':>32}   {note}")
            continue
        print(f"   {t:<14}{r['term_df']:>8,}{r['confuse_df']:>8,}"
              f"{r['confuse_ratio']:>8.2f}{r['confuse_tf_avg']:>8.1f}   {note}")

    print("\n── 혼동도가 가장 높은 것 20")
    for r in rows_out[:20]:
        print(f"   {r['confuse_ratio']:>8.1f}  {r['term']:<18} "
              f"원어 {r['term_df']:>6,} · 혼동 {r['confuse_df']:>7,}  "
              f"→ {r['parts']}")
    return 0


if __name__ == "__main__":
    lim = None
    for a in sys.argv[1:]:
        if a.startswith("--limit="):
            lim = int(a.split("=")[1])
    sys.exit(main(limit=lim))
