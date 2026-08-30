"""질의 세트로 검색이 올바른 절을 찾는지 잰다.

## 무엇을 재는가

질의를 던져 상위 조각을 받고, 그 안에 기대한 절이 있는지 본다.
답변 생성은 재지 않는다. 그것은 W8 의 몫이다.

    확인하는 것    답이 든 조각이 상위에 오는가
    확인 안 하는 것 그 조각으로 좋은 답변이 만들어지는가

## 질의를 어떻게 만들었는가

사용자가 준비한 평가 질의 28개에서 문체와 구조만 가져왔다.

    문체       "~를 알려줘" · "~를 정리해줘" · "~는 얼마인가"
    구체성      기업·연도·보고서 종류를 명시한다
    묻는 방식   Closed 단일 사실 · Open 정리·설명 요구

주제는 set 1 기업 10개의 절 구성 전체에서 고르게 뽑았다. 평가 질의에 나온
주제로 쏠리면 그쪽만 잘 되게 손보는 셈이 되기 때문이다.

평가 질의를 그대로 쓸 수 없었던 이유는 기업이 안 맞아서다. 평가 질의는
70개 기업 전체를 대상으로 만들어졌고 set 1 과 겹치는 것은 KB금융 하나다.

## 시스템에 무엇이 들어가는가

질의 문장만 들어간다. 기대 절 제목은 채점자만 본다. 실제 심사에서 위치를
알려주지 않는 것과 같다.

## 검색 조건

질의에서 기업·연도·보고서 종류를 뽑았다고 가정하고 필터를 건다. 실제
파이프라인의 S2·S3 이 하는 일이다. 그 안에서 벡터·BM25·RRF 를 견준다.
"""
import csv
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


def vec(b: bytes) -> np.ndarray:
    return np.frombuffer(b, dtype=np.float32)


def rrf_merge(lists, weights, k: int = 60):
    score: dict[int, float] = {}
    for w, lst in zip(weights, lists):
        for rank, i in enumerate(lst, 1):
            score[i] = score.get(i, 0.0) + w / (k + rank)
    return [i for i, _ in sorted(score.items(), key=lambda x: -x[1])]


def norm_title(s: str) -> str:
    return re.sub(r"[\s.]+", "", s or "")


def main(qfile: str = "queries_set1.csv", topk: int = 10,
         show: int = 0) -> int:
    from openai_emb import OpenAIEmbedder, normalize
    from rank_bm25 import BM25Okapi

    p = ROOT / "data" / "eval" / qfile
    qs = list(csv.DictReader(p.open(encoding="utf-8-sig")))
    print(f"질의 {len(qs)}개 · {p.name}")

    con = connect()
    corps = sorted({q["corp"] for q in qs})
    rows = con.execute(f"""
        SELECT c.chunk_id, c.section_id, c.header, c.text, c.tokens,
               c.{COL} v, s.title, d.corp_name, d.base_year, d.doc_subtype
        FROM chunk c
        JOIN section s ON c.section_id = s.section_id
        JOIN document d ON c.doc_id = d.doc_id
        WHERE c.{COL} IS NOT NULL AND c.tokens IS NOT NULL
          AND d.corp_name IN ({','.join('?' * len(corps))})""",
                       corps).fetchall()
    print(f"대상 조각 {len(rows):,}")

    ids = [r["chunk_id"] for r in rows]
    pos = {c: i for i, c in enumerate(ids)}
    byid = {r["chunk_id"]: r for r in rows}
    M = np.vstack([vec(r["v"]) for r in rows])
    title_of = [norm_title(r["title"]) for r in rows]
    by_doc: dict[tuple, list[int]] = {}
    for r in rows:
        by_doc.setdefault((r["corp_name"], r["base_year"],
                           r["doc_subtype"]), []).append(pos[r["chunk_id"]])

    # 시스템에는 질의 문장만 넘긴다
    emb = OpenAIEmbedder()
    qvecs, st = emb.embed_many([q["query"] for q in qs])
    if not qvecs:
        print(f"질의 임베딩 실패: {st}")
        return 1

    combos = [("벡터만", [1, 0]), ("BM25만", [0, 1]),
              ("RRF 1:1", [1, 1]), ("RRF 1:2", [1, 2]),
              ("RRF 1:3", [1, 3]), ("RRF 2:1", [2, 1])]
    stat = {c[0]: {"n": 0, "t1": 0, "tk": 0} for c in combos}
    bm_cache: dict[tuple, object] = {}
    detail = []

    for q, qv in zip(qs, qvecs):
        key = (q["corp"], int(q["year"]), q["subtype"])
        if key not in by_doc:
            print(f"   ! 문항 {q['no']}: {key} 문서가 없다")
            continue
        idx = np.array(by_doc[key])
        want = norm_title(q["expect_title"])
        qa = np.asarray(normalize(qv), dtype=np.float32)

        ov = idx[np.argsort(-(M[idx] @ qa))]
        if key not in bm_cache:
            bm_cache[key] = BM25Okapi(
                [byid[ids[i]]["tokens"].split() for i in idx])
        ob = idx[np.argsort(-bm_cache[key].get_scores(tokenize(q["query"])))]

        row = {"no": q["no"], "corp": q["corp"], "want": q["expect_title"]}
        for name, w in combos:
            order = (ov if w == [1, 0] else ob if w == [0, 1]
                     else np.array(rrf_merge([list(ov), list(ob)], w)))
            hit1 = title_of[order[0]] == want
            hitk = any(title_of[i] == want for i in order[:topk])
            s = stat[name]
            s["n"] += 1
            s["t1"] += hit1
            s["tk"] += hitk
            row[name] = (hit1, hitk, order[:5])
        detail.append(row)

    print(f"\n{'조합':<12}{'건수':>5}{'1위 적중':>10}{f'{topk}위 적중':>10}")
    for name, _ in combos:
        s = stat[name]
        if not s["n"]:
            continue
        print(f"{name:<12}{s['n']:>5}{s['t1']/s['n']:>10.0%}"
              f"{s['tk']/s['n']:>10.0%}")

    best = "RRF 1:2"
    print(f"\n── 문항별  ({best} 기준)")
    print(f"{'문항':<5}{'기업':<14}{'1위':>4}{'10위':>5}  기대한 절")
    for d in detail:
        h1, hk, _ = d[best]
        print(f"{d['no']:<5}{d['corp']:<14}{'O' if h1 else 'X':>4}"
              f"{'O' if hk else 'X':>5}  {d['want'][:34]}")

    miss = [d for d in detail if not d[best][1]]
    print(f"\n── {best} 로 {topk}위 안에 못 넣은 것 {len(miss)}건")
    for d in miss[:show or len(miss)]:
        print(f"\n   문항 {d['no']} · {d['corp']}")
        print(f"      기대: {d['want']}")
        for i in d[best][2][:4]:
            r = byid[ids[i]]
            body = re.sub(r"\s+", " ", r["text"])[:52]
            print(f"      찾음: {(r['title'] or '(제목없음)')[:26]:<28}{body}")
    return 0


if __name__ == "__main__":
    f, k, sh = "queries_set1.csv", 10, 0
    for a in sys.argv[1:]:
        if a.startswith("--file="):
            f = a.split("=", 1)[1]
        elif a.startswith("--topk="):
            k = int(a.split("=")[1])
        elif a.startswith("--show="):
            sh = int(a.split("=")[1])
    sys.exit(main(qfile=f, topk=k, show=sh))
