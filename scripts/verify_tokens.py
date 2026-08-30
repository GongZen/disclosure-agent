"""BM25 용 형태소 토큰을 검증한다. 임베딩으로 넘어가기 전 관문 중 하나다.

토큰은 검색의 절반이다. 벡터 검색이 뜻으로 찾고 BM25 가 낱말로 찾는다.
낱말이 잘못 잘리면 "전환사채" 를 물어도 못 찾는다.

네 겹을 둔다.

    1 빠짐       내용이 있는데 토큰이 없는 조각
    2 빈 토큰     분석 결과가 0개인 조각
    3 어휘 분포   종류 수와 빈도. 이상하면 신호다
    4 표본 확인   원문과 토큰을 나란히 낸다. 사람이 본다

4번은 자동 판정이 안 된다. "전환사채" 가 "전환" + "사채" 로 갈렸는지는
사람이 봐야 안다. 실패/통과가 아니라 확인 절차다.

주의 — 이 스크립트는 아직 실제 데이터로 검증되지 않았다. 토큰을 다시 만든
뒤에야 각 항목이 제대로 잡는지 확인된다.
"""
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8")

from db import connect

# 이 글자 수 이상인데 토큰이 없으면 이상하다. 숫자만 든 표 조각은
# 명사가 안 나올 수 있어 여유를 둔다.
MIN_CHARS = 200
# 검색에 쓰일 낱말. 하나라도 안 잡히면 분석기 설정이 잘못된 것이다.
MUST_HAVE = ["전환사채", "영업이익", "자산총계", "감사의견", "핵심감사사항",
             "종속기업", "유형자산", "매출채권", "이사회", "배당"]


def main(sample: int = 5) -> int:
    con = connect()
    q = lambda s, *a: con.execute(s, *a).fetchall()
    fail = 0

    print("── 규모")
    r = q("""SELECT COUNT(*) n,
                    SUM(CASE WHEN tokens IS NULL THEN 1 ELSE 0 END) nul,
                    SUM(CASE WHEN tokens='' THEN 1 ELSE 0 END) emp
             FROM chunk""")[0]
    print(f"   조각 {r['n']:,} · 토큰 없음 {r['nul']:,} · 빈 토큰 {r['emp']:,}")

    # ── 1 빠짐
    print("\n1 빠짐      토큰이 만들어지지 않은 조각")
    n = q("SELECT COUNT(*) n FROM chunk WHERE tokens IS NULL")[0]["n"]
    print(f"   {n:,}건")
    fail += n

    # ── 2 빈 토큰
    print(f"\n2 빈 토큰    {MIN_CHARS}자 이상인데 토큰이 0개인 조각")
    rows = q("""SELECT chunk_id, char_len, substr(text,1,80) t FROM chunk
                WHERE tokens = '' AND char_len >= ?""", (MIN_CHARS,))
    print(f"   {len(rows):,}건")
    for x in rows[:5]:
        print(f"      chunk {x['chunk_id']}  {x['char_len']:,}자  {x['t']!r}")
    fail += len(rows)

    # ── 3 어휘 분포
    print("\n3 어휘 분포")
    rows = q("SELECT tokens FROM chunk WHERE tokens IS NOT NULL AND tokens<>''")
    vocab = Counter()
    ntok = 0
    for x in rows:
        ts = x["tokens"].split()
        ntok += len(ts)
        vocab.update(ts)
    once = sum(1 for c in vocab.values() if c == 1)
    print(f"   토큰 {ntok:,} · 종류 {len(vocab):,} · 한 번만 나오는 것 {once:,} "
          f"({once/max(len(vocab),1):.1%})")
    print(f"   조각당 평균 {ntok/max(len(rows),1):.1f}개")
    print("\n   가장 많이 나오는 것")
    for w, c in vocab.most_common(12):
        print(f"      {w:<18}{c:>9,}")

    print("\n   검색에 쓰일 낱말이 어휘에 있는가")
    #
    # "어휘에 하나의 낱말로 있는가" 만 보면 안 된다. 쪼개져도 질의가 같은
    # 방식으로 쪼개지므로 검색은 된다. 실제로 봐야 할 것은 그 낱말이 든
    # 조각을 찾아낼 수 있는가다.
    #
    # 그래서 두 가지를 함께 낸다.
    #   원형 토큰   사전이 적용돼 하나로 잡혔는가
    #   조각 토큰   쪼개진 형태로도 남아 있는가
    #
    # 둘 다 없으면 그 낱말로는 아무것도 못 찾는다. 그때만 실패다.
    from search import tokenize
    miss = []
    print(f"      {'낱말':<18}{'원형':>10}{'조각':>26}")
    for w in MUST_HAVE:
        whole = vocab.get(w, 0)
        pieces = [(p, vocab.get(p, 0)) for p in tokenize(w) if p != w]
        if not whole and not any(c for _, c in pieces):
            miss.append(w)
        head = f"{whole:>10,}" if whole else "        없음"
        tail = " · ".join(f"{p} {c:,}" for p, c in pieces) or "-"
        print(f"      {w:<18}{head}   {tail[:24]}")
    if miss:
        print(f"      → {len(miss)}개는 원형도 조각도 없다. 검색이 불가능하다")
    fail += len(miss)

    # ── 4 표본 확인
    print(f"\n4 표본 확인  원문과 토큰을 나란히 낸다  [사람이 본다]")
    rows = q("""SELECT chunk_id, header, substr(text,1,160) t, tokens
                FROM chunk WHERE tokens IS NOT NULL AND tokens<>''
                  AND char_len BETWEEN 300 AND 3000
                ORDER BY RANDOM() LIMIT ?""", (sample,))
    for x in rows:
        print(f"\n   chunk {x['chunk_id']}  {x['header'][:60]}")
        print(f"      원문  {x['t']!r}")
        print(f"      토큰  {' '.join(x['tokens'].split()[:30])}")
    print("\n   낱말이 엉뚱하게 갈렸는지 눈으로 확인한다.")
    print("   '전환사채' 가 '전환' + '사채' 로 갈리면 검색이 안 된다.")

    print(f"\n{'통과' if fail == 0 else f'실패 {fail:,}건'}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    smp = 5
    for a in sys.argv[1:]:
        if a.startswith("--sample="):
            smp = int(a.split("=")[1])
    sys.exit(main(sample=smp))
