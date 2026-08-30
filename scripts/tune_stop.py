"""STOP 목록의 어느 갈래가 도움이 되고 어느 것이 해로운지 하나씩 잰다.

## 왜 필요한가

질의 해석기를 넣으니 성적이 떨어졌다.

              적용 전   적용 후
    3위 안      38%     25%
    8위 안      50%     38%

원인은 검색어를 너무 많이 뺀 것이었다.

    질의     "SK하이닉스가 어떤 사업을 하는 회사인지 사업보고서 기준으로 알려줘"
    검색어   ['사업', '회사']      두 낱말만 남았다

`어떤` · `보고서` · `기준` 을 다 빼니 변별력이 사라졌다. 잡음을 줄이려다
단서까지 없앤 것이다.

## 어떻게 재는가

STOP 목록을 갈래로 나누고, 갈래를 하나씩 켜고 끄며 성적을 본다. 한 번에
전부 바꾸면 어느 것이 영향을 줬는지 모른다.

판정은 절 단위다. 정답 위치는 사용자가 만든 `EVALSET_SOURCE.md` 에서 왔다.
"""
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8")

from db import connect

COL = "embedding_oa"

# STOP 을 갈래로 나눈다. 성격이 달라 따로 재야 한다.
GROUPS = {
    "질의동사": {"알리", "알", "정리", "설명", "확인", "비교", "알아보", "찾"},
    "연결어": {"기준", "근거", "대하", "관하", "위하", "통하", "따르"},
    "의문사": {"얼마", "무엇", "어디", "언제", "누구", "어떻", "어떤"},
    "문서종류": {"보고서", "공시"},
    "일반명사": {"내용", "사항", "경우", "정도", "수준"},
    "서술어": {"주세요", "바랍니다", "싶다", "하다", "되다", "있다", "없다"},
}

# 본문 검색으로 답하는 질의와 그 정답 절. 정답은 EVALSET_SOURCE.md 에서 왔다.
CASES = [
    ("문항 16", "삼성전자", "삼성전자의 주주환원 정책이 어떻게 되는지 알려줘.",
     ["6. 배당에 관한 사항"]),
    ("문항 17", "SK하이닉스",
     "SK하이닉스가 어떤 사업을 하는 회사인지 사업보고서 기준으로 알려줘.",
     ["1. 사업의 개요"]),
    ("문항 20", "삼성전자", "삼성전자의 사업부문 구성과 주요 매출처를 알려줘.",
     ["1. 사업의 개요"]),
    ("문항 21a", "삼성전자",
     "삼성전자와 SK하이닉스의 사업 구조가 어떻게 다른지 사업보고서를 근거로 비교해줘.",
     ["1. 사업의 개요", "2. 연결재무제표", "연결 손익계산서", "2-2. 연결 손익계산서"]),
    ("문항 21b", "SK하이닉스",
     "삼성전자와 SK하이닉스의 사업 구조가 어떻게 다른지 사업보고서를 근거로 비교해줘.",
     ["1. 사업의 개요", "2. 연결재무제표", "연결 손익계산서", "2-2. 연결 손익계산서"]),
    ("문항 22", "LG에너지솔루션",
     "LG에너지솔루션의 실적 변화를 설명해주고 공시 안에서 그 이유를 찾아줘.",
     ["2. 연결재무제표", "연결 손익계산서", "2-2. 연결 손익계산서",
      "1. 사업의 개요", "IV. 이사의 경영진단 및 분석의견"]),
    ("자체제작 4", "JYP Ent",
     "jyp에서 전속 연예인들에 대해서 어떠한 회계계정을 적용하여 회계처리 하는지 알아봐줘.",
     ["무형자산", "3. 연결재무제표 주석"]),
    ("자체제작 5", "한미약품",
     "한미약품의 약품 개발 현황에 대해서 공시된 사항을 모두 알려줘.",
     ["1. 사업의 개요", "II. 사업의 내용", "2. 주요 제품 및 서비스",
      "6. 주요계약 및 연구개발활동"]),
]


def vec(b: bytes) -> np.ndarray:
    return np.frombuffer(b, dtype=np.float32)


def rrf_merge(lists, weights, k: int = 60):
    score: dict[int, float] = {}
    for w, lst in zip(weights, lists):
        for rank, i in enumerate(lst, 1):
            score[i] = score.get(i, 0.0) + w / (k + rank)
    return [i for i, _ in sorted(score.items(), key=lambda x: -x[1])]


def main(weights: str = "1,2", topk: int = 8) -> int:
    import query as Q
    from openai_emb import OpenAIEmbedder, normalize
    from rank_bm25 import BM25Okapi

    w = [float(x) for x in weights.split(",")]
    con = connect()
    corps = sorted({c[1] for c in CASES})
    rows = con.execute(f"""
        SELECT c.chunk_id, c.section_id, c.tokens, c.{COL} v,
               s.title, d.corp_name, d.base_year
        FROM chunk c
        JOIN section s ON c.section_id = s.section_id
        JOIN document d ON c.doc_id = d.doc_id
        WHERE c.{COL} IS NOT NULL AND c.tokens IS NOT NULL
          AND c.char_len >= 200 AND d.doc_subtype = 'annual'
          AND d.corp_name IN ({','.join('?' * len(corps))})""",
                       corps).fetchall()
    ids = [r["chunk_id"] for r in rows]
    pos = {c: i for i, c in enumerate(ids)}
    byid = {r["chunk_id"]: r for r in rows}
    M = np.vstack([vec(r["v"]) for r in rows])
    latest: dict[str, int] = {}
    for r in rows:
        latest[r["corp_name"]] = max(latest.get(r["corp_name"], 0),
                                     r["base_year"])
    tmp: dict[str, list] = {}
    for r in rows:
        if r["base_year"] == latest[r["corp_name"]]:
            tmp.setdefault(r["corp_name"], []).append(pos[r["chunk_id"]])
    pool = {k: np.array(v) for k, v in tmp.items()}
    print(f"대상 기업 {len(corps)} · 조각 {len(rows):,}")

    emb = OpenAIEmbedder()
    qvecs, st = emb.embed_many([c[2] for c in CASES])
    if not qvecs:
        print(f"질의 임베딩 실패: {st}")
        return 1
    bm_cache = {c: BM25Okapi([byid[ids[i]]["tokens"].split() for i in pool[c]])
                for c in corps}

    def run(stop: set):
        Q.STOP = stop
        Q.parse.__globals__["STOP"] = stop
        h1 = h3 = hk = 0
        out = []
        for (no, corp, q, gold), qv in zip(CASES, qvecs):
            p = Q.parse(q)
            idx = pool[corp]
            qa = np.asarray(normalize(qv), dtype=np.float32)
            ov = idx[np.argsort(-(M[idx] @ qa))]
            ob = idx[np.argsort(-bm_cache[corp].get_scores(p.terms or ["없음"]))]
            order = rrf_merge([list(ov), list(ob)], w)
            seen, ranks, n = set(), [], 0
            for i in order:
                sid = byid[ids[i]]["section_id"]
                if sid in seen:
                    continue
                seen.add(sid)
                n += 1
                t = byid[ids[i]]["title"] or ""
                if any(g in t for g in gold):
                    ranks.append(n)
                if n >= topk:
                    break
            h1 += 1 in ranks
            h3 += any(r <= 3 for r in ranks)
            hk += bool(ranks)
            out.append((no, len(p.terms), ranks))
        return h1, h3, hk, out

    base = set().union(*GROUPS.values())
    n = len(CASES)
    print(f"\n{'설정':<18}{'검색어 평균':>11}{'1위':>7}{'3위내':>7}{'8위내':>7}")

    trials = [("STOP 없음", set()), ("STOP 전부", base)]
    trials += [(f"− {g}", base - GROUPS[g]) for g in GROUPS]
    trials += [(f"{g} 만", GROUPS[g]) for g in GROUPS]

    results = []
    for name, stop in trials:
        h1, h3, hk, out = run(stop)
        avg = sum(t for _n, t, _r in out) / len(out)
        results.append((name, stop, h1, h3, hk, avg, out))
        print(f"{name:<18}{avg:>11.1f}{h1:>4}/{n}{h3:>4}/{n}{hk:>4}/{n}")

    best = max(results, key=lambda x: (x[4], x[3], x[2]))
    print(f"\n── 가장 나은 설정: {best[0]}   8위 안 {best[4]}/{n}")
    print(f"{'문항':<12}{'검색어 수':>9}  적중 순위")
    for no, nt, ranks in best[6]:
        print(f"{no:<12}{nt:>9}  {ranks or '없음'}")
    return 0


if __name__ == "__main__":
    w, k = "1,2", 8
    for a in sys.argv[1:]:
        if a.startswith("--weights="):
            w = a.split("=")[1]
        elif a.startswith("--topk="):
            k = int(a.split("=")[1])
    sys.exit(main(weights=w, topk=k))
