"""원문 낱말을 쓰지 않는 질의로 벡터와 BM25 를 견준다.

`probe_pipeline.py` 로 재니 BM25 가 벡터를 앞섰다.

    필터+벡터      절 10위 안  80%
    필터+BM25     절 10위 안 100%
    필터+RRF      절 10위 안  96%

그런데 그 측정에는 편향이 있다. 질의를 조각에서 만들어 조각의 낱말이 그대로
질의에 들어갔다. 시험 문제를 답안지에서 뽑은 셈이다.

    질의   "KB금융 2024년 사업보고서 연결 재무상태표 자산총계 부채총계"
    문서    같은 낱말이 그대로 있다  →  BM25 가 정확히 맞힌다

실제 질의는 이렇지 않다.

    사람이 묻는 것   "작년에 자금을 어떻게 조달했나"
    문서에 있는 것   "제20-1회 상각형 조건부자본증권 발행"

    공통 낱말이 없다. BM25 로는 못 찾는다. 벡터의 몫이다.

## 이 스크립트가 하는 것

절 제목을 원문 낱말이 아닌 말로 바꿔 묻는다. 사람이 실제로 쓸 법한 표현이다.

    원문 제목                    바꿔 쓴 질의
    2-1. 연결 재무상태표          회사가 가진 재산과 갚아야 할 빚의 규모
    3. 연결재무제표 주석          재무제표 숫자에 딸린 설명
    2. 감사제도에 관한 사항        회계 감사를 누가 어떻게 맡고 있는지
    1. 사업의 개요               이 회사가 무슨 일로 돈을 버는지

바꿔 쓰기는 사람(AI)이 만든다. 그래서 이 측정은 품질 평가가 아니라
"낱말이 겹치지 않을 때 벡터가 BM25 보다 나은가" 라는 사실 확인이다.
정답은 그 제목이 붙은 절이고, 바꿔 쓴 표현이 무엇이든 정답은 안 바뀐다.

## 한계

바꿔 쓴 표현을 제가 골랐으므로, 실제 사용자의 말투와 다를 수 있다.
최종 가중치는 사용자가 준비한 평가 질의로 정한다.
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

# 절 제목 → 원문 낱말을 안 쓰는 질의.
# 왼쪽이 제목에 들어 있으면 오른쪽으로 묻는다. 겹치는 낱말이 없어야 한다.
PARAPHRASE = [
    ("연결 재무상태표", "회사가 가진 재산과 갚아야 할 빚이 각각 얼마인지"),
    ("재무상태표", "보유한 자원과 갚을 의무의 규모"),
    ("연결 손익계산서", "한 해 동안 벌어들인 돈과 쓴 돈의 내역"),
    ("손익계산서", "벌어들인 돈에서 쓴 돈을 뺀 결과"),
    ("포괄손익계산서", "장부에 잡히는 이익과 그 밖의 손익까지 합친 것"),
    ("현금흐름표", "실제로 들어오고 나간 돈의 움직임"),
    ("자본변동표", "주주 몫이 한 해 동안 어떻게 바뀌었는지"),
    ("재무제표 주석", "숫자만으로는 알 수 없는 배경 설명"),
    ("사업의 개요", "이 회사가 무슨 일로 돈을 버는지"),
    ("주요 제품", "회사가 팔고 있는 것들"),
    ("원재료", "물건을 만들 때 들어가는 재료를 어디서 사 오는지"),
    ("생산 및 설비", "공장과 기계를 어디에 얼마나 두고 있는지"),
    ("매출 및 수주상황", "얼마나 팔았고 앞으로 얼마나 팔 예정인지"),
    ("위험관리", "장사하다 손해 볼 수 있는 요소를 어떻게 다루는지"),
    ("파생상품", "값이 오르내리는 계약으로 손실을 막는 방법"),
    ("감사제도", "회계 장부를 누가 어떻게 들여다보는지"),
    ("감사인의 감사의견", "장부를 검사한 전문가가 내린 결론"),
    ("내부통제", "회사 안에서 잘못을 막는 장치"),
    ("이사회", "경영을 결정하는 사람들의 모임"),
    ("임원 및 직원", "일하는 사람이 몇 명이고 얼마를 받는지"),
    ("임원의 보수", "경영진에게 준 돈"),
    ("주주에 관한 사항", "회사를 소유한 사람들의 구성"),
    ("배당", "주주에게 나눠 준 몫"),
    ("자금조달", "필요한 돈을 어디서 구했는지"),
    ("증권의 발행", "돈을 마련하려고 내놓은 것"),
    ("대주주 등과의 거래", "회사와 큰 주주 사이에 오간 거래"),
    ("종속기업", "지배하고 있는 다른 회사들"),
    ("관계기업", "일부 지분을 들고 있는 회사들"),
    ("우발부채", "지금은 아니지만 나중에 갚아야 할 수도 있는 것"),
    ("소송", "법정에서 다투고 있는 일"),
    ("연구개발", "새로운 것을 만들려고 쓴 돈과 성과"),
    ("지적재산권", "특허 같은 무형의 권리"),
    ("리스", "빌려 쓰는 자산과 그 대가"),
    ("퇴직급여", "직원이 그만둘 때 줄 돈"),
    ("법인세", "나라에 낸 세금"),
    ("유형자산", "건물이나 기계처럼 형태가 있는 재산"),
    ("무형자산", "형태는 없지만 값어치가 있는 것"),
    ("재고자산", "아직 팔지 않고 쌓아 둔 물건"),
    ("매출채권", "물건을 주고 아직 못 받은 돈"),
    ("차입금", "은행 등에서 빌린 돈"),
    ("사채", "회사가 발행해 빌린 돈"),
    ("정관", "회사 운영의 기본 규칙"),
    ("계열회사", "같은 그룹에 속한 회사들"),
    ("타법인출자", "다른 회사에 돈을 넣은 내역"),
    ("보험계약", "보험을 팔아 생긴 권리와 의무"),
    ("금융상품", "돈을 굴리는 수단들"),
]


def vec(b: bytes) -> np.ndarray:
    return np.frombuffer(b, dtype=np.float32)


def batch_corps(n: int) -> list[str]:
    import csv
    p = ROOT / "data" / "eval" / "batches.csv"
    with p.open(encoding="utf-8-sig") as f:
        return [r["corp_name"] for r in csv.DictReader(f) if int(r["set"]) == n]


def rrf_merge(lists, weights, k: int = 60):
    score: dict[int, float] = {}
    for w, lst in zip(weights, lists):
        for rank, i in enumerate(lst, 1):
            score[i] = score.get(i, 0.0) + w / (k + rank)
    return [i for i, _ in sorted(score.items(), key=lambda x: -x[1])]


def overlap(a: str, b: str) -> int:
    """두 문자열의 공통 낱말 수. 0 이어야 벡터를 제대로 재는 것이다."""
    return len(set(tokenize(a)) & set(tokenize(b)))


def main(batch: int = 1, topk: int = 10, seed: int = 3) -> int:
    from openai_emb import OpenAIEmbedder, normalize
    from rank_bm25 import BM25Okapi

    con = connect()
    corps = batch_corps(batch)
    rows = con.execute(f"""
        SELECT c.chunk_id, c.section_id, c.header, c.text, c.tokens,
               c.{COL} v, s.title, d.corp_name, d.base_year, d.doc_subtype
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
    by_doc: dict[tuple, list[int]] = {}
    for r in rows:
        by_doc.setdefault((r["corp_name"], r["base_year"],
                           r["doc_subtype"]), []).append(pos[r["chunk_id"]])

    # 제목에 바꿔 쓸 말이 있는 절을 찾는다
    cands = []
    for r in rows:
        t = r["title"] or ""
        for key, para in PARAPHRASE:
            if key in t:
                cands.append((r, key, para))
                break
    print(f"바꿔 쓸 수 있는 조각 {len(cands):,}")

    # 문서·절마다 하나씩만 뽑는다. 같은 절을 여러 번 묻지 않는다
    seen = set()
    picked = []
    random.seed(seed)
    random.shuffle(cands)
    for r, key, para in cands:
        k = (r["corp_name"], r["base_year"], r["doc_subtype"], r["section_id"])
        if k in seen:
            continue
        seen.add(k)
        picked.append((r, key, para))
        if len(picked) >= 60:
            break
    print(f"질의 {len(picked)}개\n")

    queries = []
    for r, key, para in picked:
        st = SUBTYPE_KO.get(r["doc_subtype"], "")
        queries.append(f"{r['corp_name']} {r['base_year']}년 {st}에서 "
                       f"{para}를 알려주세요")

    # 낱말이 얼마나 겹치는지 먼저 확인한다. 겹치면 벡터를 제대로 못 잰다
    ov = [overlap(q, byid[r["chunk_id"]]["text"][:400])
          for q, (r, _k, _p) in zip(queries, picked)]
    print(f"질의와 원문의 공통 낱말  평균 {sum(ov)/len(ov):.1f}개 "
          f"· 0개인 질의 {sum(1 for x in ov if x == 0)}개")
    print("   (앞선 측정은 질의를 원문에서 떠서 이 값이 훨씬 컸다)\n")

    emb = OpenAIEmbedder()
    qvecs, st_ = emb.embed_many(queries)
    if not qvecs:
        print(f"질의 임베딩 실패: {st_}")
        return 1

    combos = [("벡터만", [1, 0]), ("BM25만", [0, 1]),
              ("1:1", [1, 1]), ("1:2 BM25↑", [1, 2]),
              ("2:1 벡터↑", [2, 1]), ("1:3 BM25↑↑", [1, 3])]
    stat = {name: {"n": 0, "s1": 0, "sk": 0} for name, _ in combos}
    bm_cache: dict[tuple, object] = {}
    miss = []

    for (r, key, para), q, qv in zip(picked, queries, qvecs):
        qa = np.asarray(normalize(qv), dtype=np.float32)
        gs = r["section_id"]
        dkey = (r["corp_name"], r["base_year"], r["doc_subtype"])
        idx = np.array(by_doc[dkey])
        ov_ = idx[np.argsort(-(M[idx] @ qa))]
        if dkey not in bm_cache:
            bm_cache[dkey] = BM25Okapi(
                [byid[ids[i]]["tokens"].split() for i in idx])
        ob = idx[np.argsort(-bm_cache[dkey].get_scores(tokenize(q)))]

        for name, w in combos:
            if w == [1, 0]:
                order = ov_
            elif w == [0, 1]:
                order = ob
            else:
                order = np.array(rrf_merge([list(ov_), list(ob)], w))
            s = stat[name]
            s["n"] += 1
            if sec_of[order[0]] == gs:
                s["s1"] += 1
            if (sec_of[order[:topk]] == gs).any():
                s["sk"] += 1
            elif name == "BM25만":
                miss.append((key, para, r, int(order[0])))

    print(f"{'조합':<14}{'건수':>5}{'절 1위':>9}{'절 10위':>10}")
    for name, _ in combos:
        s = stat[name]
        print(f"{name:<14}{s['n']:>5}{s['s1']/s['n']:>9.0%}"
              f"{s['sk']/s['n']:>10.0%}")

    print(f"\n── BM25 가 놓친 것 {len(miss)}건  (벡터가 메우는지 본다)")
    for key, para, r, top_i in miss[:8]:
        w = byid[ids[top_i]]
        print(f"   [{r['corp_name']}] 제목 \"{r['title'][:24]}\"")
        print(f"      물은 것: {para}")
        print(f"      1위    : {(w['title'] or '(제목없음)')[:34]}")
    return 0


if __name__ == "__main__":
    b, k = 1, 10
    for a in sys.argv[1:]:
        if a.startswith("--set="):
            b = int(a.split("=")[1])
        elif a.startswith("--topk="):
            k = int(a.split("=")[1])
    sys.exit(main(batch=b, topk=k))
