"""chunk 에 OpenAI 임베딩을 채운다.

CLOVA 와 두 벌을 만든다. 품질은 CLOVA(bge-m3)가 낫고 속도는 OpenAI 가
46배 빠르다. 그래서 OpenAI 로 먼저 검색 가능한 상태를 만들고 W7·W8 을
개발하면서 CLOVA 를 병행으로 돌린다. 마지막에 셋을 견줘 고른다.
자세한 것은 PLAN.md W7 절에 적었다.

묶음 호출이 되므로 한 번에 여러 개를 보낸다. CLOVA 가 조각마다 한 번씩
호출해야 했던 것과 다르고, 그것이 속도 차이의 이유다.
"""
from __future__ import annotations

import array
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8")

from db import connect
from openai_emb import OpenAIEmbedder, normalize

BATCH = 32          # 한 번에 보낼 개수


def load_batch(n: int) -> list[str]:
    """`data/eval/batches.csv` 에서 그 묶음의 기업 이름을 읽는다."""
    import csv
    p = ROOT / "data" / "eval" / "batches.csv"
    if not p.exists():
        raise SystemExit(f"{p} 가 없다. scripts/make_batches.py 를 먼저 돌린다.")
    with p.open(encoding="utf-8-sig") as f:
        return [r["corp_name"] for r in csv.DictReader(f) if int(r["set"]) == n]


def main(limit: int | None = None, batch: int | None = None) -> int:
    con = connect()
    sql = ["""SELECT c.chunk_id, c.header, c.text FROM chunk c
              JOIN document d ON c.doc_id = d.doc_id
              WHERE c.embedding_oa IS NULL"""]
    args: list = []
    if batch:
        # 묶음으로 나눠 하는 이유는 손실 제한이다. 되돌릴 수 없고 돈이 드는
        # 작업이라 전수로 하다 문제를 만나면 전부 버린다.
        # DECISIONS.md 2026-08-21 "임베딩을 10개 기업씩 7 묶음으로 나눈다"
        names = load_batch(batch)
        if not names:
            raise SystemExit(f"set {batch} 에 기업이 없다")
        sql.append(f"AND d.corp_name IN ({','.join('?' * len(names))})")
        args += names
        print(f"set {batch} · 기업 {len(names)}개  " + " · ".join(names))
    sql.append("ORDER BY c.chunk_id")
    if limit:
        sql.append(f"LIMIT {int(limit)}")
    rows = con.execute(" ".join(sql), args).fetchall()
    est = sum(len(r["header"] or "") + len(r["text"])
              for r in rows) * 0.625
    print(f"대상 {len(rows):,}건 · 추정 토큰 {est:,.0f} "
          f"· 예상 비용 {est*0.13/1e6*1380:,.0f}원")
    if not rows:
        return 0

    e = OpenAIEmbedder()
    ok = ng = 0
    t0 = time.time()
    for i in range(0, len(rows), BATCH):
        part = rows[i:i + BATCH]
        texts = [f"{r['header']}\n{r['text']}" for r in part]
        vecs, st = e.embed_many(texts)
        if not vecs:
            ng += len(part)
            print(f"   실패: {st[:100]}")
            continue
        for r, v in zip(part, vecs):
            con.execute("UPDATE chunk SET embedding_oa=? WHERE chunk_id=?",
                        (array.array("f", normalize(v)).tobytes(), r["chunk_id"]))
            ok += 1
        con.commit()
        dt = time.time() - t0
        if (i // BATCH) % 20 == 0:
            rate = ok / dt * 60 if dt else 0
            left = (len(rows) - ok) / rate / 60 if rate else 0
            print(f"   {ok:,}/{len(rows):,} · 분당 {rate:.0f}건"
                  f" · 남은 {left:.1f}시간", flush=True)
    dt = time.time() - t0
    print(f"\n완료 {ok:,} · 실패 {ng} · {dt/60:.1f}분 · 분당 {ok/dt*60:.0f}건")
    return 0


if __name__ == "__main__":
    n = b = None
    for a in sys.argv[1:]:
        if a.startswith("--limit="):
            n = int(a.split("=")[1])
        elif a.startswith("--set="):
            b = int(a.split("=")[1])
    sys.exit(main(limit=n, batch=b))
