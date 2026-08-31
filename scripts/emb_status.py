"""임베딩 진행 상황을 본다. 언제든 돌려도 된다.

build_embedding.py 가 도는 중에도 조회만 하므로 방해하지 않는다.
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8")

from db import connect

MARK = ROOT / "data" / ".emb_mark"      # 직전 조회 시점과 건수


def main(watch: int = 0):
    con = connect()
    while True:
        rows = con.execute("""
            SELECT COUNT(*) tot,
                   SUM(embedding IS NOT NULL) done,
                   SUM(CASE WHEN embedding IS NOT NULL THEN token_est ELSE 0 END) tok,
                   SUM(embedding_oa IS NOT NULL) done_oa,
                   SUM(CASE WHEN embedding_oa IS NOT NULL THEN token_est ELSE 0 END) tok_oa
            FROM chunk""").fetchone()
        tot, done, tok = rows["tot"], rows["done"] or 0, rows["tok"] or 0
        done_oa, tok_oa = rows["done_oa"] or 0, rows["tok_oa"] or 0
        now = time.time()
        rate = rate_oa = None
        if MARK.exists():
            try:
                parts = MARK.read_text().split()
                t0, d0 = float(parts[0]), int(parts[1])
                d0_oa = int(parts[2]) if len(parts) > 2 else 0
                dt = now - t0
                if dt > 5:
                    rate = max(0, done - d0) / dt * 60
                    rate_oa = max(0, done_oa - d0_oa) / dt * 60
            except Exception:
                pass
        MARK.parent.mkdir(exist_ok=True)
        MARK.write_text(f"{now} {done} {done_oa}")

        print(f"CLOVA  {done:>8,} / {tot:,}   {done/tot*100:6.2f}%"
              f"   약 {tok/1000*0.2:,.0f}원")
        print(f"OpenAI {done_oa:>8,} / {tot:,}   {done_oa/tot*100:6.2f}%"
              f"   약 {tok_oa/1_000_000*0.13*1300:,.0f}원")
        if rate is not None:
            print(f"   CLOVA 직전 조회 이후 분당 {rate:.0f}건")
        if rate_oa is not None and rate_oa > 0:
            left = (tot - done_oa) / rate_oa / 60
            print(f"   OpenAI 직전 조회 이후 분당 {rate_oa:.0f}건 · 남은 {left:.1f}시간")
        # 단계별
        print("\n   단계별 진행")
        for k, cond in [("1 II·IV", "s.path='II' OR s.path LIKE 'II/%' OR s.path='IV' OR s.path LIKE 'IV/%'"),
                        ("2 I·V~XI", "s.path IN ('I','V','VI','VII','VIII','IX','X','XI') OR s.path LIKE 'I/%' OR s.path LIKE 'V/%' OR s.path LIKE 'VI/%' OR s.path LIKE 'VII/%' OR s.path LIKE 'VIII/%' OR s.path LIKE 'IX/%' OR s.path LIKE 'X/%' OR s.path LIKE 'XI/%'"),
                        ("3 XII", "s.path='XII' OR s.path LIKE 'XII/%'"),
                        ("4 III", "s.path='III' OR s.path LIKE 'III/%'"),
                        ("5 그 밖", "s.path='' OR s.path IS NULL")]:
            r = con.execute(f"""SELECT COUNT(*) n, SUM(c.embedding IS NOT NULL) d
                                FROM chunk c JOIN section s ON c.section_id=s.section_id
                                WHERE ({cond})""").fetchone()
            n, d = r["n"], r["d"] or 0
            print(f"      {k:<10}{d:>7,} / {n:>7,}   {d/n*100:5.1f}%")
        if not watch:
            break
        time.sleep(watch)
        print()
    return 0


if __name__ == "__main__":
    w = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 0
    sys.exit(main(w))
