"""BM25 단독으로 무엇이 걸리는지 본다. 임베딩 없이 돌아간다.

토큰 사전을 만들지 말지 정하기 전에, 지금 BM25 가 실제로 어떻게 동작하는지
재는 것이 먼저다. 사전은 BM25 를 개선하려는 수단인데 개선 대상의 현재 상태를
모르고 있었다.

## 무엇을 재는가

품질을 채점하지 않는다. 사실만 낸다.

    상위 N 개에 질의 낱말이 원문 그대로 든 조각이 몇 개인가
    엉뚱한 조각이 걸린다면 그것이 무엇인가

"매출채권" 을 물었을 때 "매출액" 만 든 조각이 상위에 오는지 같은 것은
채점이 아니라 사실 확인이다.

## 왜 필터를 거는가

실제 파이프라인은 기업·연도로 먼저 좁힌다. 필터 없이 171,564조각 전부에서
찾으면 실제와 다른 상황을 재게 된다. 기본은 기업 하나로 좁힌다.
"""
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8")

from db import connect
from search import tokenize

# 사실 확인용 질의. 품질 채점용이 아니다.
# 쪼개짐이 문제가 되는지 보려고 고른 것이고, 답이 무엇인지는 원문이 정한다.
PROBES = [
    ("매출채권", "매출액과 다른 계정인데 '매출' 조각을 공유한다"),
    ("순이익", "문서에는 '당기순이익' 으로 있다. 축약형으로 찾아지는가"),
    ("미지급비용", "'미' 가 버려져 '지급비용' 이 된다. 뜻이 뒤집힌다"),
    ("영업이익", "'영업활동' · '이익잉여금' 과 조각을 공유한다"),
    ("자산총계", "'자산' 이 매우 흔하다"),
    ("전환사채", "쪼개지지 않는 낱말. 대조군이다"),
]


def load(con, corp: str | None, limit: int | None) -> list:
    sql = ["""SELECT c.chunk_id, c.header, c.text, c.tokens, s.title,
                     d.corp_name, d.report_nm
              FROM chunk c
              JOIN section s ON c.section_id = s.section_id
              JOIN document d ON c.doc_id = d.doc_id
              WHERE c.tokens IS NOT NULL AND c.tokens <> '' AND c.char_len >= 100"""]
    args: list = []
    if corp:
        sql.append("AND d.corp_name = ?")
        args.append(corp)
    if limit:
        sql.append(f"LIMIT {int(limit)}")
    return con.execute(" ".join(sql), args).fetchall()


def run(rows, query: str, topk: int = 10):
    from rank_bm25 import BM25Okapi
    corpus = [r["tokens"].split() for r in rows]
    bm = BM25Okapi(corpus)
    qt = tokenize(query)
    scores = bm.get_scores(qt)
    order = sorted(zip(scores, range(len(rows))), key=lambda x: -x[0])[:topk]
    return qt, [(s, rows[i]) for s, i in order]


def main(corp: str = "삼성전자", topk: int = 10) -> int:
    con = connect()
    t0 = time.time()
    rows = load(con, corp, None)
    print(f"대상 {corp} · 조각 {len(rows):,}개 · 적재 {time.time()-t0:.1f}초\n")

    for q, why in PROBES:
        qt, hits = run(rows, q, topk)
        # 원문에 그 낱말이 그대로 있는 조각이 전체에 몇 개인가
        total = sum(1 for r in rows if q in r["text"])
        good = sum(1 for _s, r in hits if q in r["text"])
        print(f"── 질의 \"{q}\"   {why}")
        print(f"   질의 토큰 {qt}")
        print(f"   원문에 '{q}' 가 그대로 든 조각 {total:,}개 / {len(rows):,}")
        print(f"   상위 {topk} 중 그것을 담은 조각 {good}개")
        for i, (s, r) in enumerate(hits[:5], 1):
            mark = "O" if q in r["text"] else "X"
            body = re.sub(r"\s+", " ", r["text"])[:70]
            print(f"      {i}. [{mark}] {s:6.2f}  {r['title'][:22]:<24}{body}")
        print()
    return 0


if __name__ == "__main__":
    c, k = "삼성전자", 10
    for a in sys.argv[1:]:
        if a.startswith("--corp="):
            c = a.split("=", 1)[1]
        elif a.startswith("--topk="):
            k = int(a.split("=")[1])
    sys.exit(main(corp=c, topk=k))
