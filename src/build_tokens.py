"""chunk 마다 형태소 토큰을 미리 만들어 둔다.

BM25 는 검색할 때마다 후보 전부를 형태소 분석해야 한다. 후보가 2,000개면
1,000만 자를 매번 분석하게 되어 질의 하나에 65초가 걸렸다. 미리 만들어
두면 문자열을 공백으로 나누기만 하면 된다.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8")

from db import connect
from search import tokenize


def main(limit: int | None = None) -> int:
    con = connect()
    rows = con.execute(
        """SELECT chunk_id, header, text FROM chunk
           WHERE tokens IS NULL ORDER BY chunk_id"""
        + (f" LIMIT {int(limit)}" if limit else "")).fetchall()
    print(f"대상 {len(rows):,}건", flush=True)
    if not rows:
        return 0
    t0 = time.time()
    buf = []
    for i, r in enumerate(rows, 1):
        toks = tokenize(f"{r['header']} {r['text']}")
        buf.append((" ".join(toks), r["chunk_id"]))
        if len(buf) >= 2000:
            con.executemany("UPDATE chunk SET tokens=? WHERE chunk_id=?", buf)
            con.commit()
            buf = []
            dt = time.time() - t0
            print(f"   {i:,}/{len(rows):,} · 분당 {i/dt*60:,.0f}건"
                  f" · 남은 {(len(rows)-i)/(i/dt)/60:.1f}분", flush=True)
    if buf:
        con.executemany("UPDATE chunk SET tokens=? WHERE chunk_id=?", buf)
        con.commit()
    dt = time.time() - t0
    print(f"완료 {len(rows):,}건 · {dt/60:.1f}분")
    return 0


if __name__ == "__main__":
    n = None
    for a in sys.argv[1:]:
        if a.startswith("--limit="):
            n = int(a.split("=")[1])
    sys.exit(main(limit=n))
