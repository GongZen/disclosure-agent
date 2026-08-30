"""절마다 그 절을 가리키는 낱말을 데이터에서 뽑는다. 층 2 경로 필터의 재료다.

## 왜 필요한가

질의가 무엇을 묻는지 알면 볼 절을 좁힐 수 있다.

    "주주환원 정책"   →  III/6  6. 배당에 관한 사항
    "임원 보수"       →  VIII/2 2. 임원의 보수 등
    "감사의견"        →  V/1    1. 외부감사에 관한 사항

후보가 300조각에서 5조각으로 줄면 검색이 거의 필요 없어진다.

## 목차가 고정이라 가능하다

사업보고서는 법정 서식이다. 70개 기업 전부가 같은 경로에 같은 제목을 쓴다.
`I/1 회사의 개요` 부터 `XII 상세표` 까지 60개 남짓이고 기업이 달라도 안 바뀐다.

## 질의가 아니라 목차에서 뽑는다

평가 질의를 보고 만들면 그 질의에 맞춰진다. 실제 심사가 다른 것을 물으면
안 잡힌다. 그래서 절의 본문에서 그 절에만 유난히 많이 나오는 낱말을 뽑는다.

    점수 = (그 절에 나오는 비율) ÷ (전체 절에 나오는 비율)

값이 크면 그 절에서만 쓰이는 낱말이다. 실측하면 이렇게 나온다.

    III/6  배당에 관한 사항   배당성향 · 배당수익률 · 결산배당 · 배당금총액
    VIII/2 임원의 보수 등     보수총액 · 퇴직소득 · 등기이사 · 상여
    II/5   위험관리 및 파생거래 시장위험 · 유동성위험 · 신용위험 · 환율

## 한계

`II/1 사업의 개요` 처럼 내용이 일반적인 절은 특징 낱말이 안 나온다.
실측에서 `부문 · 시장 · 사업 · 당사` 같은 흔한 말만 걸렸다. 그런 절은
경로 필터로 좁힐 수 없고 일반 검색에 맡겨야 한다.
"""
import csv
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8")

from db import connect
from search import tokenize

OUT = ROOT / "data" / "eval" / "pathmap.csv"
# 그 절의 이 비율 이상에 나와야 대표 낱말로 본다
MIN_COVER = 0.7
# 전체에서 이보다 드물면 표본이 적어 못 믿는다
MIN_DF = 5
# 이 점수 아래면 그 절만의 낱말이 아니다
MIN_SCORE = 3.0
TOP_N = 12


def main(year: int = 2025, subtype: str = "annual") -> int:
    con = connect()
    rows = con.execute("""
        SELECT s.path, s.title, s.text FROM section s
        JOIN document d ON s.doc_id = d.doc_id
        WHERE d.doc_subtype = ? AND d.base_year = ?
          AND s.path <> '' AND s.char_len BETWEEN 300 AND 30000
          AND s.level IN ('major', 'minor')""", (subtype, year)).fetchall()
    print(f"{year}년 {subtype} · 절 {len(rows):,}개")

    tf: dict[str, Counter] = defaultdict(Counter)
    df = Counter()
    cnt = Counter()
    title: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        ts = set(tokenize(r["text"][:4000]))
        tf[r["path"]].update(ts)
        df.update(ts)
        cnt[r["path"]] += 1
        if r["title"]:
            title[r["path"]][r["title"]] += 1
    n_all = len(rows)

    out = []
    for p, c in sorted(cnt.items()):
        if c < 20:                       # 기업 20곳 미만이면 공통 구조가 아니다
            continue
        scored = []
        for w, k in tf[p].items():
            if k < c * MIN_COVER or df[w] < MIN_DF:
                continue
            s = (k / c) / (df[w] / n_all)
            if s >= MIN_SCORE:
                scored.append((s, w))
        scored.sort(reverse=True)
        t = title[p].most_common(1)[0][0] if title[p] else ""
        # 낱말만이 아니라 점수도 남긴다. 낱말마다 변별력이 다르기 때문이다.
        # "배당성향" 은 III/6 을 특정하지만 "정책" 은 아무 절이나 가리킨다.
        # 개수만 세면 "주주환원 정책" 이 IV/6 을 짚는 일이 생긴다.
        out.append({"path": p, "title": t, "n_corp": c,
                    "terms": " ".join(w for _s, w in scored[:TOP_N]),
                    "scores": " ".join(f"{w}:{s:.1f}"
                                       for s, w in scored[:TOP_N]),
                    "top_score": round(scored[0][0], 1) if scored else 0.0})

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8-sig", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=list(out[0]))
        wr.writeheader()
        wr.writerows(out)
    print(f"{OUT}  {len(out)}행\n")

    strong = [r for r in out if r["terms"]]
    weak = [r for r in out if not r["terms"]]
    print(f"대표 낱말이 잡힌 절 {len(strong)} · 안 잡힌 절 {len(weak)}\n")
    print(f"{'경로':<10}{'절':<28}{'기업':>4}  대표 낱말")
    for r in strong:
        print(f"{r['path']:<10}{r['title'][:26]:<28}{r['n_corp']:>4}  "
              f"{r['terms'][:56]}")
    if weak:
        print(f"\n── 대표 낱말이 안 잡힌 절  (경로 필터로 못 좁힌다)")
        for r in weak:
            print(f"   {r['path']:<10}{r['title'][:40]}")
    return 0


if __name__ == "__main__":
    y, st = 2025, "annual"
    for a in sys.argv[1:]:
        if a.startswith("--year="):
            y = int(a.split("=")[1])
        elif a.startswith("--subtype="):
            st = a.split("=")[1]
    sys.exit(main(year=y, subtype=st))
