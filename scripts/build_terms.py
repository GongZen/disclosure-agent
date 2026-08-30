"""회계·공시 용어 사전 후보를 코퍼스에서 뽑는다.

BM25 검색에서 복합 회계 용어가 쪼개지는 문제를 고치려고 만든다.

    영업이익    →  영업 · 이익
    자산총계    →  자산 · 총계
    미지급비용  →  지급 · 비용        '미' 가 접두사라 버려져 뜻이 뒤집힌다

kiwipiepy 는 일반 한국어를 대상으로 만들어져 회계 용어를 하나의 낱말로
알지 못한다. 사용자 사전에 등록하면 붙어 있게 된다.

## 어디서 뽑는가

표의 행 머리다. "매출채권 │ 622,027,314,668" 의 첫 칸이 계정명이고,
회계 용어가 여기 모여 있다. 표본 8,000조각에서 7,665개가 나왔다.

## 무엇을 함께 재는가

사전에 넣는 것만으로는 부족하다. 원래 형태만 남기면 부분 검색을 잃는다.

    문서 "당기순이익" → 당기순이익
    질의 "순이익"     → 순이익            안 걸린다

그래서 원래 형태와 쪼갠 조각을 둘 다 토큰으로 낸다(합집합). 다만 아무 조각이나
내면 안 된다.

    "매출채권" 을 쪼개 "매출" 을 내면
    "매출액이 얼마야" 질의에 매출채권 조각이 걸린다. 다른 계정인데도

그래서 조각마다 독립비율을 잰다.

    독립비율 = (조각은 있고 복합어는 없는 조각 수) ÷ (복합어가 있는 조각 수)

    낮다   그 조각이 거의 이 복합어 안에서만 쓰인다. 쪼개도 안전하다
    높다   복합어 밖에서 자주 쓰인다. 쪼개면 잡음이 크다

실측값이다.

    이익잉여금의 잉여금   0.2   안전
    당기순이익의 순이익   0.6   비교적 안전
    자본총계의 총계      0.9   경계
    매출채권의 매출·채권  1.0   쪼개면 안 된다
    유형자산의 자산      3.0   위험
    미지급비용의 지급    9.6   매우 위험

기준값은 미리 정하지 않는다. 전수 분포를 보고 정한다.

## 이 측정법의 한계

한 조각에 "매출채권" 과 "매출액" 이 함께 있으면 분모에서 빠진다. 재무제표
조각에는 둘 다 있는 경우가 흔하므로 실제 잡음보다 낮게 나온다. 이 값은
잡음의 하한이고, 기준값을 그만큼 낮게 잡아야 한다.
"""
import csv
import re
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8")

from db import connect

OUT = ROOT / "data" / "terms"
CELL = "│"

# 후보 조건
MIN_LEN, MAX_LEN = 3, 14
MIN_FREQ = 20          # 전수에서 이만큼은 나와야 검색에 쓰인다

# 괄호·기호를 떼고 본다. "씨제이제일제당(주)" → "씨제이제일제당"
_TRIM = re.compile(r"[\(\)\[\]{}<>「」『』:：,，.。·ㆍ/\\|*※\-–—_'\"`]+")
_NUMONLY = re.compile(r"^[\d,.\-()△▲%\s]+$")
# 계정명에 들어가는 접속어. 이것만 있으면 살린다.
_CONJ = re.compile(r"^[가-힣A-Za-z0-9]+(및|와|과|또는)[가-힣A-Za-z0-9]+$")


def clean(s: str) -> str:
    """행 머리를 정제한다. 공백과 기호를 떼고 본다."""
    s = re.sub(r"\s+", "", s)
    s = _TRIM.sub("", s)
    return s


def collect_heads(con) -> Counter:
    """표 행 머리를 전수로 모은다."""
    heads = Counter()
    t0 = time.time()
    n = 0
    cur = con.execute("SELECT text FROM chunk WHERE text LIKE '%' || ? || '%'",
                      (CELL,))
    while True:
        rows = cur.fetchmany(2000)
        if not rows:
            break
        for r in rows:
            n += 1
            for line in r["text"].split("\n"):
                if CELL not in line:
                    continue
                h = clean(line.split(CELL)[0])
                if MIN_LEN <= len(h) <= MAX_LEN and not _NUMONLY.match(h):
                    heads[h] += 1
        if n % 40000 == 0:
            print(f"   {n:,}조각 · 행 머리 {len(heads):,}종 · "
                  f"{time.time()-t0:.0f}초", flush=True)
    print(f"   조각 {n:,} · 서로 다른 행 머리 {len(heads):,}종 · "
          f"{time.time()-t0:.0f}초")
    return heads


def is_noun_phrase(kiwi, w: str) -> tuple[bool, list[str]]:
    """명사로만 이루어졌는가. 그리고 쪼개지면 그 조각들.

    접속어가 든 계정명은 살린다. "현금및현금성자산" · "판매비와관리비" 다.
    """
    toks = kiwi.tokenize(w)
    ok = all(t.tag.startswith("NN") or t.tag in ("XR", "SL", "SN", "SH",
                                                 "XPN", "XSN")
             for t in toks)
    if not ok and _CONJ.match(w):
        ok = all(t.tag.startswith("NN")
                 or t.tag in ("XR", "SL", "SN", "SH", "XPN", "XSN",
                              "MAG", "MAJ", "JC")
                 for t in toks)
    parts = [t.form for t in toks
             if (t.tag.startswith("NN") or t.tag in ("XR", "SL", "SH"))
             and len(t.form) > 1]
    return ok, parts


def main(min_freq: int = MIN_FREQ) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    con = connect()

    print("── 1 표 행 머리를 전수로 모은다")
    heads = collect_heads(con)

    print(f"\n── 2 후보를 거른다  (길이 {MIN_LEN}~{MAX_LEN} · {min_freq}회 이상)")
    cand = {w: n for w, n in heads.items() if n >= min_freq}
    print(f"   빈도 조건 통과 {len(cand):,}종")

    from kiwipiepy import Kiwi
    kiwi = Kiwi()
    keep, split, mixed = [], [], []
    for w, n in cand.items():
        ok, parts = is_noun_phrase(kiwi, w)
        if not ok:
            mixed.append((w, n))
        elif len(parts) <= 1 and len(kiwi.tokenize(w)) == 1:
            keep.append((w, n))
        else:
            split.append((w, n, parts))
    print(f"   하나로 유지  {len(keep):,}   사전에 넣을 필요 없음")
    print(f"   쪼개짐      {len(split):,}   사전 후보")
    print(f"   품사 섞임    {len(mixed):,}   제외")

    # lookup.py 가 이미 정한 말을 무조건 넣는다
    from lookup import LABEL, KEYWORD
    forced = set()
    for v in list(LABEL.values()) + list(KEYWORD):
        v = re.sub(r"\s*\(.*?\)\s*", "", v).strip()
        if MIN_LEN <= len(v) <= MAX_LEN:
            forced.add(v)
    have = {w for w, *_ in split}
    for v in sorted(forced - have):
        ok, parts = is_noun_phrase(kiwi, v)
        if len(parts) > 1 or len(kiwi.tokenize(v)) > 1:
            split.append((v, heads.get(v, 0), parts))
    print(f"   lookup.py 에서 추가 {len(forced - have):,}종")

    print(f"\n── 3 후보를 파일로 낸다")
    split.sort(key=lambda x: -x[1])
    p = OUT / "candidates_raw.csv"
    with p.open("w", encoding="utf-8-sig", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["term", "freq", "parts"])
        for w, n, parts in split:
            wr.writerow([w, n, " ".join(parts)])
    print(f"   {p}  {len(split):,}행")

    p2 = OUT / "excluded.csv"
    with p2.open("w", encoding="utf-8-sig", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["term", "freq", "why"])
        for w, n in sorted(mixed, key=lambda x: -x[1]):
            wr.writerow([w, n, "품사 섞임"])
        for w, n in sorted(keep, key=lambda x: -x[1]):
            wr.writerow([w, n, "하나로 유지"])
    print(f"   {p2}  {len(mixed)+len(keep):,}행")

    print("\n── 상위 30")
    for w, n, parts in split[:30]:
        print(f"   {n:>7,}  {w:<18} → {' · '.join(parts)}")
    return 0


if __name__ == "__main__":
    mf = MIN_FREQ
    for a in sys.argv[1:]:
        if a.startswith("--min-freq="):
            mf = int(a.split("=")[1])
    sys.exit(main(min_freq=mf))
