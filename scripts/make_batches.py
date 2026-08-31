"""임베딩을 10개 기업씩 7 묶음으로 나눈다.

## 왜 나누는가

임베딩은 되돌릴 수 없고 53,785원이 든다. 전수로 하다 문제를 발견하면
전부 버린다. 나누면 손실이 한 묶음 분량으로 제한된다.

    전수로 하다 틀림    53,785원 전부
    1 묶음에서 발견     약 9,000원

## 어떻게 나누는가

두 가지를 섞는다.

### 1 묶음 — 문제가 몰렸던 기업으로 채운다

손실 제한이 목적이므로, 문제가 있다면 첫 묶음에서 만나야 한다. 지금까지
실측으로 드러난 어려운 기업을 모은다. 여기를 통과하면 나머지는 상대적으로
안전하다고 볼 근거가 생긴다.

### 2~7 묶음 — 조각 수를 고르게 맞추고 업종을 섞는다

기업 규모가 9.6배까지 벌어진다(KB금융 7,032 · 시프트업 731). 기업 수로만
나누면 묶음마다 비용과 시간이 크게 달라져 예측이 안 된다.

조각 수가 큰 기업부터 가장 작은 묶음에 넣는 방식으로 채운다. 같은 조건이면
그 묶음에 없는 업종을 우선한다. 각 묶음이 전체의 축소판이 되어야 한 묶음의
결과로 나머지를 가늠할 수 있다.
"""
import csv
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8")

from corpus import load_universe
from db import connect

OUT = ROOT / "data" / "eval"
NSET = 7

# 1 묶음에 넣을 기업과 그 근거. 전부 실측으로 나온 것이다.
HARD = {
    "KB금융": "속성 따옴표 오류 29,553개(전체의 96%) · 조각 최다 7,032 "
              "· 손실 53.4% · 금융지주 계정 체계 · 본문이 PDF 인 대체 수집분",
    "삼성생명": "보험. IFRS17 로 계정 체계가 은행지주와도 다르다",
    "신한지주": "금융지주 · 한 절이 문서의 98.1% 를 차지했다",
    "현대자동차": "속성 오류로 밀림이 DOCUMENT 까지 올라갔다 · 손실 31.9%",
    "CJ제일제당": "손실 67.9% 로 최대 · 표 하나가 899,194자였다",
    "NC": "중첩 표 57개. 표 안의 표가 두 번 담기던 사례",
    "크래프톤": "한 절이 문서의 99.6% 를 차지했다",
    "HD현대일렉트릭": "손실 55.8% · 속성 오류 130개",
    "NAVER": "손실 31.5% · 속성 오류 50개",
    "시프트업": "조각 731개로 가장 작다. 작은 문서도 확인한다",
}


def main() -> int:
    con = connect()
    rows = con.execute("""SELECT d.corp_name, COUNT(DISTINCT d.doc_id) ndoc,
                                 COUNT(*) nchunk, SUM(c.token_est) tok
                          FROM chunk c JOIN document d ON c.doc_id=d.doc_id
                          GROUP BY d.corp_name""").fetchall()
    uni = load_universe().set_index("corp_name")
    corp = {}
    for r in rows:
        nm = r["corp_name"]
        corp[nm] = {
            "name": nm, "ndoc": r["ndoc"], "nchunk": r["nchunk"],
            "tok": r["tok"],
            "sector": uni.loc[nm]["sector"] if nm in uni.index else "?",
        }
    total_tok = sum(c["tok"] for c in corp.values())
    total_chunk = sum(c["nchunk"] for c in corp.values())
    print(f"기업 {len(corp)} · 조각 {total_chunk:,} · 토큰 {total_tok:,}")

    missing = [n for n in HARD if n not in corp]
    if missing:
        print(f"경고 — 1묶음 목록에 없는 기업: {missing}")

    sets = [[] for _ in range(NSET)]
    sets[0] = [corp[n] for n in HARD if n in corp]
    used = set(HARD)

    # 나머지를 조각 수 내림차순으로, 가장 작은 묶음부터 채운다.
    # 같은 조건이면 그 묶음에 없는 업종을 먼저 넣는다.
    rest = sorted((c for n, c in corp.items() if n not in used),
                  key=lambda c: -c["nchunk"])
    for c in rest:
        cand = [i for i in range(1, NSET) if len(sets[i]) < 10]
        cand.sort(key=lambda i: (sum(x["nchunk"] for x in sets[i]),
                                 any(x["sector"] == c["sector"] for x in sets[i])))
        sets[cand[0]].append(c)

    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / "batches.csv"
    with p.open("w", encoding="utf-8-sig", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["set", "corp_name", "sector", "docs", "chunks",
                     "tokens", "why"])
        for i, s in enumerate(sets, 1):
            for c in sorted(s, key=lambda x: -x["nchunk"]):
                wr.writerow([i, c["name"], c["sector"], c["ndoc"],
                             c["nchunk"], c["tok"],
                             HARD.get(c["name"], "")])
    print(f"{p}\n")

    USD, KRW = 0.13 / 1e6, 1380
    print(f"{'묶음':<6}{'기업':>4}{'문서':>6}{'조각':>9}{'토큰':>13}"
          f"{'비용':>9}{'시간':>7}   업종")
    for i, s in enumerate(sets, 1):
        nc = sum(c["nchunk"] for c in s)
        nt = sum(c["tok"] for c in s)
        nd = sum(c["ndoc"] for c in s)
        won = nt * USD * KRW
        mins = nc / total_chunk * 120
        secs = sorted({c["sector"] for c in s})
        tag = "  ← 문제가 몰렸던 기업" if i == 1 else ""
        print(f"set {i:<2}{len(s):>4}{nd:>6}{nc:>9,}{nt:>13,}"
              f"{won:>8,.0f}원{mins:>6.0f}분   {len(secs)}업종{tag}")
    print(f"{'합계':<6}{len(corp):>4}{sum(c['ndoc'] for c in corp.values()):>6}"
          f"{total_chunk:>9,}{total_tok:>13,}{total_tok*USD*KRW:>8,.0f}원{120:>6}분")

    for i, s in enumerate(sets, 1):
        print(f"\n── set {i}   조각 {sum(c['nchunk'] for c in s):,}")
        for c in sorted(s, key=lambda x: -x["nchunk"]):
            why = HARD.get(c["name"], "")
            note = f"   {why}" if why else ""
            print(f"   {c['name']:<20}{c['nchunk']:>7,}  {c['sector']:<18}{note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
