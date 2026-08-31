"""검색을 조립된 상태로 잰다. 부품 하나만 떼어 재면 잘못된 결론이 나온다.

`probe_section.py` 로 벡터 단독 성적을 재니 절 10위 안이 42~68%였다.
못 찾은 사례를 보니 원인이 분명했다.

    정답 : KB금융 · 2-1. 연결 재무상태표 · 제 19 기 1분기말 2026.03.31
    1위  : KB금융 · 2-1. 연결 재무상태표 · 제 17 기 반기말 2024.06.30

같은 절이 문서마다 반복된다. KB금융은 문서가 24건이고 각각에 연결
재무상태표가 있다. 항목명도 표 구조도 같고 다른 것은 기(期)와 숫자뿐이다.
벡터는 뜻을 담으므로 이 24개가 거의 같은 좌표에 놓인다.

그런데 실제 파이프라인은 벡터 단독이 아니다.

    지금 잰 것       기업 필터 + 벡터
    실제 파이프라인   기업·연도·종류 필터 + 벡터 + BM25 + RRF

## 무엇을 재는가

부품을 하나씩 더해 가며 잰다. 어느 것이 얼마나 기여하는지 갈린다.

    1  기업만 + 벡터           지금까지 잰 것
    2  기업+연도+종류 + 벡터    필터를 실제처럼 건다
    3  기업+연도+종류 + BM25    낱말 검색만
    4  기업+연도+종류 + RRF     둘을 순위로 합친다

질의에 연도와 보고서 종류를 넣는다. 실제 질의가 그렇기 때문이다.
"삼성전자 2024년 사업보고서의 감사의견" 처럼 묻지 "감사의견" 만 묻지 않는다.

판정은 절 단위다. chunk 는 찾기 위한 단위이고 답을 주는 단위는 section 이다.
"""
import random
import re
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8")

from db import connect
from search import tokenize

COL = "embedding_oa"
SUBTYPE_KO = {"annual": "사업보고서", "half": "반기보고서",
              "quarter": "분기보고서"}


def vec(b: bytes) -> np.ndarray:
    return np.frombuffer(b, dtype=np.float32)


def batch_corps(n: int) -> list[str]:
    import csv
    p = ROOT / "data" / "eval" / "batches.csv"
    with p.open(encoding="utf-8-sig") as f:
        return [r["corp_name"] for r in csv.DictReader(f) if int(r["set"]) == n]


def rrf_merge(*ranked, k: int = 60):
    """여러 순위 목록을 합친다. 점수를 안 쓰고 순위만 쓴다."""
    score: dict[int, float] = {}
    for lst in ranked:
        for rank, i in enumerate(lst, 1):
            score[i] = score.get(i, 0.0) + 1.0 / (k + rank)
    return [i for i, _s in sorted(score.items(), key=lambda x: -x[1])]


def main(batch: int = 1, n_probe: int = 50, topk: int = 10,
         seed: int = 7) -> int:
    from openai_emb import OpenAIEmbedder, normalize
    from rank_bm25 import BM25Okapi

    con = connect()
    corps = batch_corps(batch)
    rows = con.execute(f"""
        SELECT c.chunk_id, c.section_id, c.doc_id, c.header, c.text,
               c.tokens, c.{COL} v, s.title, d.corp_name, d.base_year,
               d.doc_subtype
        FROM chunk c
        JOIN section s ON c.section_id = s.section_id
        JOIN document d ON c.doc_id = d.doc_id
        WHERE c.{COL} IS NOT NULL AND c.tokens IS NOT NULL
          AND c.char_len >= 300
          AND d.corp_name IN ({','.join('?' * len(corps))})""",
                       corps).fetchall()
    print(f"set {batch} · 후보 조각 {len(rows):,}")

    ids = [r["chunk_id"] for r in rows]
    pos = {c: i for i, c in enumerate(ids)}
    byid = {r["chunk_id"]: r for r in rows}
    M = np.vstack([vec(r["v"]) for r in rows])
    sec_of = np.array([r["section_id"] for r in rows])

    # 필터 조합별 후보 목록을 미리 만든다
    by_corp: dict[str, list[int]] = {}
    by_doc: dict[tuple, list[int]] = {}
    for r in rows:
        i = pos[r["chunk_id"]]
        by_corp.setdefault(r["corp_name"], []).append(i)
        by_doc.setdefault((r["corp_name"], r["base_year"],
                           r["doc_subtype"]), []).append(i)

    random.seed(seed)
    picks = random.sample(ids, min(n_probe, len(ids)))

    # 질의를 만든다. 연도와 보고서 종류를 넣는다.
    queries, meta = [], []
    for cid in picks:
        g = byid[cid]
        flat = re.sub(r"\s+", " ", g["text"]).strip()
        corp = g["corp_name"]
        yr = g["base_year"]
        st = SUBTYPE_KO.get(g["doc_subtype"], "")
        title = g["title"] or ""
        words = [w for w in re.findall(r"[가-힣]{3,}", flat)[:60]][:4]
        for kind, qy in [
            ("본문 인용", f"{corp} {yr}년 {st} {flat[:140]}"),
            ("맥락+낱말", f"{corp} {yr}년 {st} {title} {' '.join(words)}"),
            ("사람 말투", f"{corp} {yr}년 {st}의 {title or ' '.join(words[:2])} "
                        f"내용을 알려주세요"),
        ]:
            queries.append(qy.strip())
            meta.append((cid, kind, qy.strip()))
    print(f"질의 {len(queries)}개")

    emb = OpenAIEmbedder()
    qvecs, st_ = emb.embed_many(queries)
    if not qvecs:
        print(f"질의 임베딩 실패: {st_}")
        return 1
    print("질의 임베딩 완료\n")

    ways = ["1 기업+벡터", "2 필터+벡터", "3 필터+BM25", "4 필터+RRF"]
    stat: dict[tuple, dict] = {}
    bm_cache: dict[tuple, object] = {}
    miss4 = []

    for (cid, kind, qtext), qv in zip(meta, qvecs):
        g = byid[cid]
        qa = np.asarray(normalize(qv), dtype=np.float32)
        gp = pos[cid]
        gs = g["section_id"]
        key = (g["corp_name"], g["base_year"], g["doc_subtype"])
        wide = np.array(by_corp[g["corp_name"]])
        narrow = np.array(by_doc[key])
        # 1  기업만 + 벡터
        o1 = wide[np.argsort(-(M[wide] @ qa))]
        # 2  기업+연도+종류 + 벡터
        o2 = narrow[np.argsort(-(M[narrow] @ qa))]
        # 3  같은 필터 + BM25
        if key not in bm_cache:
            corpus = [byid[ids[i]]["tokens"].split() for i in narrow]
            bm_cache[key] = BM25Okapi(corpus)
        bm = bm_cache[key]
        sc = bm.get_scores(tokenize(qtext))
        o3 = narrow[np.argsort(-sc)]
        # 4  RRF
        o4 = np.array(rrf_merge(list(o2), list(o3)))

        for way, order in zip(ways, (o1, o2, o3, o4)):
            s = stat.setdefault((kind, way),
                                {"n": 0, "s1": 0, "sk": 0, "pool": []})
            s["n"] += 1
            s["pool"].append(len(order))
            top = order[:topk]
            if sec_of[order[0]] == gs:
                s["s1"] += 1
            if (sec_of[top] == gs).any():
                s["sk"] += 1
            elif way == ways[3]:
                miss4.append((kind, g, int(order[0])))

    print(f"{'질의 종류':<12}{'방식':<14}{'후보':>7}{'절 1위':>8}{'절 10위':>9}")
    for kind in ("본문 인용", "맥락+낱말", "사람 말투"):
        for way in ways:
            s = stat.get((kind, way))
            if not s:
                continue
            n = s["n"]
            print(f"{kind:<12}{way:<14}{sum(s['pool'])//n:>7,}"
                  f"{s['s1']/n:>8.0%}{s['sk']/n:>9.0%}")
        print()

    print(f"── RRF 로도 절을 못 찾은 것 {len(miss4)}건")
    for kind, g, top_i in miss4[:6]:
        w = byid[ids[top_i]]
        print(f"   [{kind}] {g['corp_name']} {g['base_year']} "
              f"{g['doc_subtype']} · {(g['title'] or '(제목없음)')[:20]}")
        print(f"      정답: {re.sub(r'\\s+', ' ', g['text'])[:56]}")
        print(f"      1위 : {(w['title'] or '(제목없음)')[:20]} · "
              f"{re.sub(r'\\s+', ' ', w['text'])[:44]}")
    return 0


if __name__ == "__main__":
    b, n, k = 1, 50, 10
    for a in sys.argv[1:]:
        if a.startswith("--set="):
            b = int(a.split("=")[1])
        elif a.startswith("--n="):
            n = int(a.split("=")[1])
        elif a.startswith("--topk="):
            k = int(a.split("=")[1])
    sys.exit(main(batch=b, n_probe=n, topk=k))
