"""chunk 에 임베딩 벡터를 채운다.

한 번에 다 하지 않는다. `--limit` 으로 개수를 정해 부분 실행하고 결과를
확인한 뒤 늘린다. `embedding IS NULL` 인 것만 처리하므로 중단해도 이어서
돌릴 수 있고 같은 조각을 두 번 호출하지 않는다.

벡터는 정규화해서 float32 로 저장한다. 정규화해 두면 코사인 유사도가
내적만으로 끝나고, float32 는 float64 의 절반 크기다. 1,024차원이면
조각당 4KB 이고 9만 9천 개면 약 400MB 다.
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
from clova import Embedder, normalize


def to_blob(vec: list[float]) -> bytes:
    return array.array("f", vec).tobytes()


def from_blob(b: bytes) -> list[float]:
    a = array.array("f")
    a.frombytes(b)
    return list(a)


# 영역을 나눠 단계별로 돌린다. 검색 가치가 높은 순서다. 실측한 서술 비율이
# 근거다. II·IV 가 24~28%로 가장 높고 III 은 5.4%인데 조각의 58%를 차지한다.
STAGE = {
    "1": ("II·IV 사업내용·경영진단",
          "s.path='II' OR s.path LIKE 'II/%' OR s.path='IV' OR s.path LIKE 'IV/%'"),
    "2": ("I·V~XI 개요·주주·임원 등",
          "s.path IN ('I','V','VI','VII','VIII','IX','X','XI')"
          " OR s.path LIKE 'I/%' OR s.path LIKE 'V/%' OR s.path LIKE 'VI/%'"
          " OR s.path LIKE 'VII/%' OR s.path LIKE 'VIII/%' OR s.path LIKE 'IX/%'"
          " OR s.path LIKE 'X/%' OR s.path LIKE 'XI/%'"),
    "3": ("XII 상세표", "s.path='XII' OR s.path LIKE 'XII/%'"),
    "4": ("III 재무에 관한 사항", "s.path='III' OR s.path LIKE 'III/%'"),
    "5": ("그 밖 (표지·목차)", "s.path='' OR s.path IS NULL"),
}


def main(limit: int | None = None, report: int = 100,
         stage: str | None = None) -> int:
    con = connect()
    where = "c.embedding IS NULL"
    if stage:
        label, cond = STAGE[stage]
        where += f" AND ({cond})"
        print(f"단계 {stage} — {label}")
    todo = con.execute(
        f"""SELECT c.chunk_id, c.header, c.text FROM chunk c
            JOIN section s ON c.section_id = s.section_id
            WHERE {where}
            ORDER BY c.chunk_id""" + (f" LIMIT {int(limit)}" if limit else "")
    ).fetchall()
    total = con.execute("SELECT COUNT(*) n FROM chunk").fetchone()["n"]
    done = con.execute(
        "SELECT COUNT(*) n FROM chunk WHERE embedding IS NOT NULL").fetchone()["n"]
    print(f"전체 {total:,} · 이미 완료 {done:,} · 이번에 처리 {len(todo):,}")
    if not todo:
        return 0

    emb = Embedder()
    ok = ng = 0
    errs: dict[str, int] = {}
    t0 = time.time()
    for i, r in enumerate(todo, 1):
        vec, st = emb.embed(f"{r['header']}\n{r['text']}")
        if vec:
            con.execute("UPDATE chunk SET embedding=? WHERE chunk_id=?",
                        (to_blob(normalize(vec)), r["chunk_id"]))
            ok += 1
        else:
            ng += 1
            errs[st[:70]] = errs.get(st[:70], 0) + 1
        if i % report == 0:
            con.commit()
            dt = time.time() - t0
            rate = ok / dt * 60 if dt else 0
            left = (len(todo) - i) / rate / 60 if rate else 0
            print(f"   {i:>7,}/{len(todo):,}  성공 {ok:,} · 실패 {ng}"
                  f" · 분당 {rate:.0f} · 간격 {emb.gap:.2f}s"
                  f" · 429 {emb.n_429} · 남은 {left:.1f}시간")
    con.commit()
    dt = time.time() - t0
    print(f"\n처리 {len(todo):,} · 성공 {ok:,} · 실패 {ng} · {dt/60:.1f}분")
    print(f"   실효 분당 {ok/dt*60:.0f}건 · 429 누적 {emb.n_429} · 최종 간격 {emb.gap:.2f}s")
    if errs:
        print("   실패 사유")
        for k, v in sorted(errs.items(), key=lambda x: -x[1])[:5]:
            print(f"      {k}  {v}건")
    left = con.execute(
        "SELECT COUNT(*) n FROM chunk WHERE embedding IS NULL").fetchone()["n"]
    print(f"   남은 조각 {left:,}")
    return 0


if __name__ == "__main__":
    n = st = None
    for a in sys.argv[1:]:
        if a.startswith("--limit="):
            n = int(a.split("=")[1])
        elif a.startswith("--stage="):
            st = a.split("=")[1]
    sys.exit(main(limit=n, stage=st))
