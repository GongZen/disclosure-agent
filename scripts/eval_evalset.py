"""사용자가 준비한 평가 질의로 검색 성적을 잰다.

## 왜 정답 절을 미리 정하지 않는가

정답 위치를 AI 가 정하면 AI 가 만든 정답으로 AI 를 채점하는 형태가 된다.
앞서 set 1 질의에서 그 문제를 겪었다. `"KB금융이 어떤 사업을 하는 회사인지"`
의 정답을 `1. 사업의 개요` 하나로 정했는데, 실제로는 `2. 주요 제품 및 서비스`
와 `IV. 이사의 경영진단` 에도 답이 있었다. 정답이 아닌 절을 찾았다고 틀렸다고
하기 어렵다.

그래서 다르게 잰다.

## 무엇을 재는가

    1  기업·연도 추출     질의에서 필터 조건을 뽑아낼 수 있는가
    2  후보 축소          필터가 후보를 얼마나 줄이는가
    3  검색 결과          상위 10개가 무엇인가. 사람이 본다
    4  방식 비교          벡터 · BM25 · RRF 가중치별로 무엇이 다른가

3번은 자동 판정하지 않는다. 결과를 그대로 내어 사람이 본다. 사용자가
`EVALSET_SOURCE.md` 에 적어 둔 정답 위치와 대조하면 채점이 된다.
AI 는 그 파일을 읽지 않는다.

## 본문 검색으로 답할 질의만 대상이다

평가 질의 28개는 세 갈래로 갈린다.

    값 조회       fact_financial 이 답한다      W5
    정형 이벤트   event_* 가 답한다             W4
    본문 검색     chunk 가 답한다               W6·W7   ← 여기만 잰다

값과 이벤트는 표에서 뽑은 것이라 검색이 필요 없다. 이 스크립트는 본문
검색이 필요한 질의만 다룬다.
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
# 질의 해석기를 쓸지. 끄면 질의 문장을 그대로 검색어로 쓴다
USE_PARSER = True

# 본문 검색으로 답해야 하는 질의. 번호는 EVALSET_QUESTION.md 를 따른다.
# 값 조회(1·6·7·10·14·18·19)와 정형 이벤트(2·3·4·5·8·9·11·12·13·15)는 뺐다.
BODY_Q = {
    "문항 16": ("삼성전자", None, "삼성전자의 주주환원 정책이 어떻게 되는지 알려줘."),
    "문항 17": ("SK하이닉스", None,
              "SK하이닉스가 어떤 사업을 하는 회사인지 사업보고서 기준으로 알려줘."),
    "문항 20": ("삼성전자", None, "삼성전자의 사업부문 구성과 주요 매출처를 알려줘."),
    "문항 21a": ("삼성전자", None,
               "삼성전자와 SK하이닉스의 사업 구조가 어떻게 다른지 "
               "사업보고서를 근거로 비교해줘."),
    "문항 21b": ("SK하이닉스", None,
               "삼성전자와 SK하이닉스의 사업 구조가 어떻게 다른지 "
               "사업보고서를 근거로 비교해줘."),
    "문항 22": ("LG에너지솔루션", None,
              "LG에너지솔루션의 실적 변화를 설명해주고 공시 안에서 "
              "그 이유를 찾아줘."),
    "자체제작 4": ("JYP Ent", None,
                "jyp에서 전속 연예인들에 대해서 어떠한 회계계정을 적용하여 "
                "회계처리 하는지 알아봐줘."),
    "자체제작 5": ("한미약품", None,
                "한미약품의 약품 개발 현황에 대해서 공시된 사항을 모두 알려줘."),
}


def vec(b: bytes) -> np.ndarray:
    return np.frombuffer(b, dtype=np.float32)


def rrf_merge(lists, weights, k: int = 60):
    score: dict[int, float] = {}
    for w, lst in zip(weights, lists):
        for rank, i in enumerate(lst, 1):
            score[i] = score.get(i, 0.0) + w / (k + rank)
    return [i for i, _ in sorted(score.items(), key=lambda x: -x[1])]


def main(topk: int = 10, weights: str = "1,2", subtype: str = "annual") -> int:
    from openai_emb import OpenAIEmbedder, normalize
    from rank_bm25 import BM25Okapi

    w = [float(x) for x in weights.split(",")]
    con = connect()
    corps = sorted({v[0] for v in BODY_Q.values()})
    rows = con.execute(f"""
        SELECT c.chunk_id, c.section_id, c.header, c.text, c.tokens,
               c.{COL} v, s.title, s.path, d.corp_name, d.base_year,
               d.doc_subtype, d.report_nm
        FROM chunk c
        JOIN section s ON c.section_id = s.section_id
        JOIN document d ON c.doc_id = d.doc_id
        WHERE c.{COL} IS NOT NULL AND c.tokens IS NOT NULL
          AND c.char_len >= 200
          AND d.corp_name IN ({','.join('?' * len(corps))})""",
                       corps).fetchall()
    print(f"대상 기업 {len(corps)}개 · 조각 {len(rows):,}\n")

    ids = [r["chunk_id"] for r in rows]
    pos = {c: i for i, c in enumerate(ids)}
    byid = {r["chunk_id"]: r for r in rows}
    M = np.vstack([vec(r["v"]) for r in rows])

    # 최신 사업보고서로 좁힌다. 질의에 연도가 없으면 가장 최근 것을 본다
    latest: dict[str, int] = {}
    for r in rows:
        if r["doc_subtype"] == subtype:
            latest[r["corp_name"]] = max(latest.get(r["corp_name"], 0),
                                         r["base_year"])
    pool: dict[str, list[int]] = {}
    for r in rows:
        if r["doc_subtype"] == subtype and r["base_year"] == latest.get(
                r["corp_name"]):
            pool.setdefault(r["corp_name"], []).append(pos[r["chunk_id"]])

    from query import parse

    emb = OpenAIEmbedder()
    keys = list(BODY_Q)
    # 벡터에는 원문을 넣는다. 뜻을 담는 쪽이라 문장이 통째로 있는 편이 낫다.
    # BM25 에는 해석된 검색어를 넣는다. 낱말이 정확히 맞아야 하는 쪽이라
    # "사업보고서" 같은 필터 조건이 섞이면 엉뚱한 절이 걸린다.
    qvecs, st = emb.embed_many([BODY_Q[k][2] for k in keys])
    if not qvecs:
        print(f"질의 임베딩 실패: {st}")
        return 1
    parsed = {k: parse(BODY_Q[k][2]) for k in keys}

    out_rows = []
    for k, qv in zip(keys, qvecs):
        corp, _yr, q = BODY_Q[k]
        idx = np.array(pool.get(corp, []))
        if not len(idx):
            print(f"{k}: {corp} 문서를 못 찾았다")
            continue
        qa = np.asarray(normalize(qv), dtype=np.float32)
        ov = idx[np.argsort(-(M[idx] @ qa))]
        bm = BM25Okapi([byid[ids[i]]["tokens"].split() for i in idx])
        qt = parsed[k].terms if USE_PARSER else tokenize(q)
        ob = idx[np.argsort(-bm.get_scores(qt))]
        order = np.array(rrf_merge([list(ov), list(ob)], w))

        print(f"\n{'='*74}")
        print(f"{k} · {corp} {latest[corp]}년 {subtype} · 후보 {len(idx):,}조각")
        print(f"   질의: {q}")
        if USE_PARSER:
            print(f"   해석: {parsed[k].summary()}")
            print(f"   검색어: {parsed[k].terms}")
        print(f"   가중치 벡터 {w[0]} : BM25 {w[1]}\n")
        print(f"   {'순위':<4}{'경로':<12}{'절 제목':<34}본문 앞부분")
        seen = set()
        shown = 0
        for i in order:
            r = byid[ids[i]]
            if r["section_id"] in seen:
                continue
            seen.add(r["section_id"])
            shown += 1
            body = re.sub(r"\s+", " ", r["text"])[:40]
            print(f"   {shown:<4}{(r['path'] or '-'):<12}"
                  f"{(r['title'] or '(제목없음)')[:32]:<34}{body}")
            out_rows.append({"문항": k, "기업": corp, "순위": shown,
                             "경로": r["path"], "절": r["title"],
                             "본문": re.sub(r"\s+", " ", r["text"])[:200]})
            if shown >= topk:
                break

    p = ROOT / "data" / "eval" / "evalset_result.csv"
    with p.open("w", encoding="utf-8-sig", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=list(out_rows[0]))
        wr.writeheader()
        wr.writerows(out_rows)
    print(f"\n\n{p}")
    print("   EVALSET_SOURCE.md 의 근거 위치와 대조해 채점하시면 된다.")
    print("   AI 는 그 파일을 읽지 않았다.")
    return 0


if __name__ == "__main__":
    k, w, st = 10, "1,2", "annual"
    for a in sys.argv[1:]:
        if a.startswith("--topk="):
            k = int(a.split("=")[1])
        elif a.startswith("--weights="):
            w = a.split("=")[1]
        elif a.startswith("--subtype="):
            st = a.split("=")[1]
        elif a == "--no-parser":
            globals()["USE_PARSER"] = False
    sys.exit(main(topk=k, weights=w, subtype=st))
