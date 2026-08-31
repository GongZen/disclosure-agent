"""본문 검색 성적을 잰다. 측정 코드를 하나로 통일한 뒤의 유일한 평가 진입점이다.

앞서 평가 스크립트를 셋 만들었다가 서로 다른 숫자를 냈다. 후보 구성과 채점
기준이 조금씩 달랐고 어느 것이 맞는지 알 수 없었다. 실행 부분을
`src/retrieval.py` 로 모으고 이 스크립트는 설정을 바꿔 가며 부르기만 한다.

## 사례와 정답

`data/eval/body_cases.csv` 에 있다. 정답 절은 사용자가 만든
`EVALSET_SOURCE.md` 에서 옮긴 것이라 AI 가 정답을 정하지 않았다.

평가 질의 28개 중 본문 검색으로 답하는 것만 담았다. 값 조회는
`fact_financial`, 정형 이벤트는 `event_*` 가 답한다.

## 무엇을 비교하나

    --mode=weights   RRF 가중치를 바꿔 가며 잰다
    --mode=stop      질의 해석기의 STOP 갈래를 켜고 끄며 잰다
    --mode=path      층 2 경로 필터를 켜고 끄며 잰다
    --mode=show      한 설정의 검색 결과를 그대로 낸다. 눈으로 본다

## 다른 질의 세트로 재기

    --cases=queries_set1.csv    set 1 기업 10곳의 질의 30개

평가 스크립트는 이 파일 하나다. 예전에 eval_evalset.py · tune_stop.py ·
eval_retrieval.py 셋이 더 있었는데 2026-08-30 에 지웠다. 셋 다 retrieval.py 를
안 쓰고 후보 구성을 따로 해서 같은 질의에 다른 답을 냈다. 꺼내 보려면
`git show f3af3b6:scripts/eval_evalset.py`. 사유는 docs/feedback/W7.md.
"""
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8")

# 지금 쓰는 RRF 가중치. 확정한 값이 아니다.
#
# 사례 9개로 잴 때는 2:1 이 가장 나았는데 37개로 늘리니 1:1 이 됐다.
# 한두 건에 순위가 뒤집히는 규모라 값을 고정하지 않는다. 사례가 늘거나
# 조건이 바뀌면 --mode=weights 로 다시 잰다.
#
#     37건 기준   1:0  1위 9  · 8위내 20
#                 0:1  1위 15 · 8위내 25
#                 1:1  1위 12 · 8위내 29   ← 넓게 담는 데 가장 낫다
#                 1:2  1위 16 · 8위내 28
#                 1:5  1위 18 · 8위내 25   ← 1위 적중은 여기가 낫다
#
# 생성 모델에 여러 조각을 넣으므로 8위내를 우선한다.
W_DEFAULT = (1.0, 1.0)

# STOP 갈래. query.STOP 을 이 조합으로 바꿔 가며 잰다.
GROUPS = {
    "질의동사": {"알리", "알", "정리", "설명", "확인", "비교", "알아보", "찾"},
    "연결어": {"기준", "근거", "대하", "관하", "위하", "통하", "따르"},
    "의문사": {"얼마", "무엇", "어디", "언제", "누구", "어떻", "어떤"},
    "문서종류": {"보고서", "공시"},
    "일반명사": {"내용", "사항", "경우", "정도", "수준"},
    "서술어": {"주세요", "바랍니다", "싶다", "하다", "되다", "있다", "없다"},
}


def load_cases(name: str = "body_cases.csv"):
    """질의 CSV 를 읽는다. 두 형식을 받는다.

        body_cases.csv     no · corp · query · gold
                           gold 는 정답 절이 여럿이면 | 로 잇는다
        queries_set1.csv   no · corp · year · subtype · type · level ·
                           expect_title · query

    정답 컬럼 이름만 다르므로 둘 다 본다. year 는 안 쓴다. 지금 사례가
    전부 최신 연도이고, 안 넘기면 Corpus 가 기업별 최신 연도를 고른다.
    """
    p = ROOT / "data" / "eval" / name
    out = []
    with p.open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            gold = r.get("gold") or r.get("expect_title") or ""
            out.append((r["no"], r["corp"], r["query"],
                        [g for g in gold.split("|") if g]))
    return out


def run(cp, cases, qvecs, weights, stop=None, topk=8, use_path=False):
    """한 설정으로 전부 재고 (1위, 3위내, topk내, 상세) 를 낸다."""
    import query as Q
    from retrieval import grade, search

    if stop is not None:
        Q.parse.__globals__["STOP"] = stop
    h1 = h3 = hk = 0
    detail = []
    for (no, corp, q, gold), qv in zip(cases, qvecs):
        p = Q.parse(q)
        hits = search(cp, corp, qv, p.terms, weights=weights, topk=topk,
                      use_path=use_path)
        ranks = grade(hits, gold)
        h1 += 1 in ranks
        h3 += any(r <= 3 for r in ranks)
        hk += bool(ranks)
        detail.append((no, p.terms, ranks, hits))
    return h1, h3, hk, detail


def main(mode: str = "weights", topk: int = 8,
         cases_file: str = "body_cases.csv") -> int:
    import query as Q
    from openai_emb import OpenAIEmbedder, normalize
    from retrieval import Corpus

    cases = load_cases(cases_file)
    corps = sorted({c[1] for c in cases})
    cp = Corpus(corps)
    n = len(cases)
    print(f"사례 {n}개 · 기업 {len(corps)}개 · 조각 {len(cp.rows):,}")
    for c in corps:
        print(f"   {c:<16}{cp.latest.get(c)}년 · 후보 "
              f"{len(cp.candidates(c)):,}조각")

    emb = OpenAIEmbedder()
    got, st = emb.embed_many([c[2] for c in cases])
    if not got:
        print(f"질의 임베딩 실패: {st}")
        return 1
    qvecs = [normalize(g) for g in got]
    base = set().union(*GROUPS.values())

    if mode == "weights":
        print(f"\n── RRF 가중치별  (STOP 기본)")
        print(f"{'벡터:BM25':<12}{'1위':>7}{'3위내':>7}{f'{topk}위내':>8}")
        best = None
        for w in [(1, 0), (0, 1), (1, 1), (1, 2), (1, 3), (1, 5),
                  (2, 1), (3, 1)]:
            h1, h3, hk, d = run(cp, cases, qvecs, w, base, topk)
            print(f"{f'{w[0]}:{w[1]}':<12}{h1:>3}/{n}{h3:>4}/{n}{hk:>5}/{n}")
            if best is None or (hk, h3, h1) > best[0]:
                best = ((hk, h3, h1), w, d)
        print(f"\n가장 나은 조합 {best[1][0]}:{best[1][1]}")
        for no, terms, ranks, _ in best[2]:
            print(f"   {no:<12}{str(ranks) if ranks else '없음':<10}{terms}")

    elif mode == "stop":
        print(f"\n── STOP 갈래별  (가중치 1:2)")
        print(f"{'설정':<16}{'검색어평균':>10}{'1위':>7}{'3위내':>7}{f'{topk}위내':>8}")
        trials = [("STOP 없음", set()), ("STOP 전부", base)]
        trials += [(f"− {g}", base - GROUPS[g]) for g in GROUPS]
        rows = []
        for name, stop in trials:
            h1, h3, hk, d = run(cp, cases, qvecs, W_DEFAULT, stop, topk)
            avg = sum(len(t) for _n, t, _r, _h in d) / len(d)
            rows.append((name, stop, hk, h3, h1, d))
            print(f"{name:<16}{avg:>10.1f}{h1:>3}/{n}{h3:>4}/{n}{hk:>5}/{n}")
        b = max(rows, key=lambda x: (x[2], x[3], x[4]))
        print(f"\n가장 나은 설정 {b[0]}")
        for no, terms, ranks, _ in b[5]:
            print(f"   {no:<12}{str(ranks) if ranks else '없음':<10}{terms}")

    elif mode == "path":
        from retrieval import guess_paths
        print(f"\n── 경로 필터  (가중치 {W_DEFAULT[0]}:{W_DEFAULT[1]})")
        print(f"{'설정':<14}{'1위':>7}{'3위내':>7}{f'{topk}위내':>8}")
        for name, up in [("경로 필터 없음", False), ("경로 필터 적용", True)]:
            h1, h3, hk, d = run(cp, cases, qvecs, W_DEFAULT, base, topk, up)
            print(f"{name:<14}{h1:>3}/{n}{h3:>4}/{n}{hk:>5}/{n}")
            if up:
                after = d
        print(f"\n{'문항':<12}{'적중':<12}짚은 경로")
        for (no, terms, ranks, _), (_, _, r0, _) in zip(
                after, run(cp, cases, qvecs, W_DEFAULT, base, topk, False)[3]):
            g = guess_paths(terms)
            mark = ""
            if ranks and not r0:
                mark = "  ← 새로 찾음"
            elif r0 and not ranks:
                mark = "  ← 잃음"
            elif ranks and r0 and min(ranks) < min(r0):
                mark = f"  ← {min(r0)}위→{min(ranks)}위"
            print(f"{no:<12}{str(ranks) if ranks else '없음':<12}{g}{mark}")

    else:  # show
        Q.parse.__globals__["STOP"] = base
        h1, h3, hk, d = run(cp, cases, qvecs, W_DEFAULT, base, topk)
        print(f"\n1위 {h1}/{n} · 3위내 {h3}/{n} · {topk}위내 {hk}/{n}")
        for no, terms, ranks, hits in d:
            print(f"\n{'='*72}\n{no}   검색어 {terms}   적중 {ranks or '없음'}")
            for h in hits:
                mark = "★" if h.rank in ranks else " "
                print(f"   {mark}{h.rank:<3}{h.path:<11}"
                      f"{h.title[:30]:<32}{h.text[:36]}")
    return 0


if __name__ == "__main__":
    m, k, cf = "weights", 8, "body_cases.csv"
    for a in sys.argv[1:]:
        if a.startswith("--mode="):
            m = a.split("=")[1]
        elif a.startswith("--topk="):
            k = int(a.split("=")[1])
        elif a.startswith("--cases="):
            cf = a.split("=")[1]
    sys.exit(main(mode=m, topk=k, cases_file=cf))
