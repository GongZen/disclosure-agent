"""CLOVA 임베딩을 단계 1~5 순서로 끝까지 돌린다.

`src/build_embedding.py` 는 한 번 부르면 한 통과만 한다. 그런데 429 로
16~22% 가 실패하므로 같은 단계를 여러 번 돌려야 다 채워진다. 실제 이력이다.

    1단계  1차  처리 5,982  성공 4,903  실패 1,079   242.5분
           2차  처리 1,079  성공   841  실패   238    45.2분
           3차  처리   238  …

이 스크립트가 그 반복을 대신한다. 한 단계에서 더 이상 줄지 않을 때까지
돌리고 다음 단계로 넘어간다.

## 왜 멈추는가

    다 채웠다          남은 조각이 0
    더 안 줄어든다      한 통과에서 성공이 0.  429 가 아니라 다른 문제다
    통과 상한          한 단계에 MAX_PASS 번까지만.  무한 반복 방지

## 중단해도 안전하다

`build_embedding.py` 가 `embedding IS NULL` 인 것만 처리한다. 중간에 죽여도
다시 돌리면 이어서 한다. 같은 조각을 두 번 호출하지 않으므로 중복 과금이 없다.

## 쓰는 법

    python scripts/run_embedding.py              1단계부터 끝까지
    python scripts/run_embedding.py --from=4     4단계부터
    python scripts/run_embedding.py --only=2     2단계만

세션이 끊겨도 살아 있게 하려면 터미널에서 직접 띄우는 편이 안전하다.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8")

from db import connect

MAX_PASS = 8        # 한 단계에서 이 횟수까지만 돌린다

# build_embedding.py 의 STAGE 와 같은 조건이다. 남은 개수를 세는 데만 쓴다.
COND = {
    "1": ("II·IV 사업내용·경영진단",
          "s.path='II' OR s.path LIKE 'II/%' OR s.path='IV' OR s.path LIKE 'IV/%'"),
    "2": ("I·V~XI 개요·주주·임원",
          "s.path IN ('I','V','VI','VII','VIII','IX','X','XI')"
          " OR s.path LIKE 'I/%' OR s.path LIKE 'V/%' OR s.path LIKE 'VI/%'"
          " OR s.path LIKE 'VII/%' OR s.path LIKE 'VIII/%' OR s.path LIKE 'IX/%'"
          " OR s.path LIKE 'X/%' OR s.path LIKE 'XI/%'"),
    "3": ("XII 상세표", "s.path='XII' OR s.path LIKE 'XII/%'"),
    "4": ("III 재무", "s.path='III' OR s.path LIKE 'III/%'"),
    "5": ("그 밖 (표지·목차)", "s.path='' OR s.path IS NULL"),
}


def left(stage: str) -> int:
    con = connect()
    return con.execute(f"""
        SELECT COUNT(*) FROM chunk k JOIN section s ON k.section_id = s.section_id
        WHERE k.embedding IS NULL AND ({COND[stage][1]})""").fetchone()[0]


def run_stage(stage: str) -> None:
    label = COND[stage][0]
    n0 = left(stage)
    print(f"\n{'=' * 62}\n단계 {stage} — {label}\n남은 조각 {n0:,}\n{'=' * 62}",
          flush=True)
    if n0 == 0:
        print("   이미 다 채워져 있다. 넘어간다", flush=True)
        return

    for p in range(1, MAX_PASS + 1):
        before = left(stage)
        if before == 0:
            print(f"   {p - 1}차에서 다 채웠다", flush=True)
            return
        print(f"\n-- {p}차 통과 시작 · 남은 {before:,} --", flush=True)
        t0 = time.time()
        subprocess.run([sys.executable, str(ROOT / "src" / "build_embedding.py"),
                        f"--stage={stage}"], check=False)
        after = left(stage)
        got = before - after
        dt = (time.time() - t0) / 60
        print(f"-- {p}차 끝 · {got:,}개 채움 · {dt:.1f}분 · 남은 {after:,} --",
              flush=True)
        if after == 0:
            print(f"   단계 {stage} 완료", flush=True)
            return
        if got == 0:
            print(f"   더 안 줄어든다. 429 가 아닌 문제로 보인다. 단계 {stage} 중단",
                  flush=True)
            return
    print(f"   통과 상한 {MAX_PASS}회에 닿았다. 남은 {left(stage):,}개", flush=True)


def main(stages: list[str]) -> int:
    con = connect()
    tot = con.execute("SELECT COUNT(*) FROM chunk").fetchone()[0]
    t0 = time.time()
    for s in stages:
        run_stage(s)
    done = con.execute(
        "SELECT COUNT(*) FROM chunk WHERE embedding IS NOT NULL").fetchone()[0]
    print(f"\n{'=' * 62}")
    print(f"끝. {done:,} / {tot:,} ({done / tot * 100:.1f}%) · "
          f"{(time.time() - t0) / 3600:.1f}시간")
    for s in COND:
        print(f"   단계 {s}  남은 {left(s):,}")
    return 0


if __name__ == "__main__":
    order = list(COND)
    sel = order
    for a in sys.argv[1:]:
        if a.startswith("--from="):
            sel = order[order.index(a.split("=")[1]):]
        elif a.startswith("--only="):
            sel = [a.split("=")[1]]
    sys.exit(main(sel))
