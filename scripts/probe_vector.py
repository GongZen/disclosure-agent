"""임베딩이 검색에 쓸 수 있는 상태인지 실제 질의로 확인한다.

`verify_embedding.py` 는 벡터 자체를 본다. 차원·노름·중복·이웃 일관성이다.
그것을 전부 통과해도 검색이 안 될 수 있다. 벡터가 조각의 내용을 담고 있는지는
실제로 찾아 봐야 안다.

## 무엇을 재는가

조각에서 질의를 만들고 그 조각이 돌아오는지 본다.

    조각 하나를 고른다
    그 조각의 내용으로 질의를 만든다
    벡터 검색을 돌린다
    그 조각이 상위에 오는가

정답을 사람이 고르지 않는다. 질의를 만든 조각이 곧 정답이라 사실 확인이 된다.
검색 품질 평가와는 성격이 다르다. 품질 평가는 "이 질의에 제대로 답하는가" 를
묻고, 이것은 "벡터와 조각이 맞물려 있는가" 를 묻는다.

## 질의를 어떻게 만드는가

세 가지를 섞는다. 난이도가 다르다.

    그대로       조각 앞부분을 그대로 쓴다
                 가장 쉽다. 이것도 안 되면 벡터가 완전히 잘못됐다

    요약         헤더(기업·보고서·절)와 조각의 특징 낱말을 조합한다
                 실제 질의에 가깝다

    바꿔 쓰기     조각 내용을 사람 말투 질문으로 바꾼다
                 가장 어렵다. 표현이 달라도 뜻으로 찾아야 한다

## 왜 필터를 거는가

실제 파이프라인은 기업으로 먼저 좁힌다. 필터 없이 전체에서 찾으면 실제와
다른 상황을 재게 된다. 다만 필터 없는 경우도 함께 내어, 좁히지 않았을 때
얼마나 나빠지는지 본다.
"""
import array
import random
import re
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8")

from db import connect

COL = "embedding_oa"


def vec(b: bytes) -> np.ndarray:
    return np.frombuffer(b, dtype=np.float32)


def batch_corps(n: int) -> list[str]:
    import csv
    p = ROOT / "data" / "eval" / "batches.csv"
    with p.open(encoding="utf-8-sig") as f:
        return [r["corp_name"] for r in csv.DictReader(f) if int(r["set"]) == n]


def make_queries(row) -> list[tuple[str, str]]:
    """조각 하나에서 질의 셋을 만든다. (종류, 질의)"""
    body = re.sub(r"\s+", " ", row["text"]).strip()
    head = row["header"] or ""
    corp = head.split(" · ")[0] if " · " in head else ""
    title = row["title"] or ""

    out = [("그대로", body[:160])]

    # 요약 — 기업·절 제목 + 조각의 특징 낱말
    words = [w for w in re.findall(r"[가-힣A-Za-z]{2,}", body)[:40]
             if len(w) >= 3][:6]
    out.append(("요약", f"{corp} {title} {' '.join(words[:4])}".strip()))

    # 바꿔 쓰기 — 사람 말투 질문
    subj = title or (words[0] if words else corp)
    out.append(("바꿔쓰기", f"{corp}의 {subj}에 대해 알려주세요"))
    return out


def main(batch: int = 1, n_probe: int = 40, topk: int = 10,
         seed: int = 7) -> int:
    from openai_emb import OpenAIEmbedder, normalize

    con = connect()
    corps = batch_corps(batch)
    print(f"set {batch} · 기업 {len(corps)}개")

    rows = con.execute(f"""
        SELECT c.chunk_id, c.header, c.text, c.{COL} v, s.title, d.corp_name
        FROM chunk c
        JOIN section s ON c.section_id = s.section_id
        JOIN document d ON c.doc_id = d.doc_id
        WHERE c.{COL} IS NOT NULL AND c.char_len >= 300
          AND d.corp_name IN ({','.join('?' * len(corps))})""",
                       corps).fetchall()
    print(f"후보 조각 {len(rows):,}개")
    if len(rows) < 100:
        print("조각이 너무 적다. 임베딩을 먼저 마친다.")
        return 1

    byid = {r["chunk_id"]: r for r in rows}
    ids = [r["chunk_id"] for r in rows]
    pos = {c: i for i, c in enumerate(ids)}
    # 벡터를 한 행렬로 쌓는다. 조각 3만 개와 질의 120개를 하나씩 비교하면
    # 110억 번 곱셈이라 순수 파이썬으로는 끝나지 않는다.
    M = np.vstack([vec(r["v"]) for r in rows])
    bycorp: dict[str, list[int]] = {}
    for r in rows:
        bycorp.setdefault(r["corp_name"], []).append(r["chunk_id"])
    corp_idx = {c: np.array([pos[x] for x in v])
                for c, v in bycorp.items()}

    random.seed(seed)
    picks = random.sample(list(byid), min(n_probe, len(byid)))

    queries, meta = [], []
    for cid in picks:
        for kind, qy in make_queries(byid[cid]):
            queries.append(qy)
            meta.append((cid, kind))
    print(f"질의 {len(queries)}개 ({len(picks)}조각 × 3종)")

    emb = OpenAIEmbedder()
    qvecs, st = emb.embed_many(queries)
    if not qvecs:
        print(f"질의 임베딩 실패: {st}")
        return 1
    print(f"질의 임베딩 완료\n")

    stat: dict[str, dict] = {}
    misses = []
    for (cid, kind), qv in zip(meta, qvecs):
        qv = normalize(qv)
        gold = byid[cid]
        s = stat.setdefault(kind, {"n": 0, "top1": 0, "topk": 0,
                                   "rank": [], "g_top1": 0, "g_topk": 0})
        s["n"] += 1

        qa = np.asarray(qv, dtype=np.float32)

        # 기업으로 좁힌 경우
        idx = corp_idx[gold["corp_name"]]
        sims = M[idx] @ qa
        order = np.argsort(-sims)
        gold_pos = pos[cid]
        rank = int(np.where(idx[order] == gold_pos)[0][0]) + 1
        s["rank"].append(rank)
        if rank == 1:
            s["top1"] += 1
        if rank <= topk:
            s["topk"] += 1
        else:
            misses.append((kind, rank, gold, ids[int(idx[order[0]])]))

        # 좁히지 않은 경우
        gsims = M @ qa
        grank = int((gsims > gsims[gold_pos]).sum()) + 1
        if grank == 1:
            s["g_top1"] += 1
        if grank <= topk:
            s["g_topk"] += 1

    print(f"{'질의 종류':<10}{'건수':>5}{'1위':>7}{'10위내':>8}"
          f"{'중앙순위':>8}   |  {'1위':>7}{'10위내':>8}")
    print(f"{'':10}{'':>5}{'  기업으로 좁힘':>20}{'':>8}   |  {'전체에서':>15}")
    total_fail = 0
    for kind in ("그대로", "요약", "바꿔쓰기"):
        s = stat.get(kind)
        if not s:
            continue
        rs = sorted(s["rank"])
        med = rs[len(rs) // 2]
        print(f"{kind:<10}{s['n']:>5}{s['top1']/s['n']:>7.0%}"
              f"{s['topk']/s['n']:>8.0%}{med:>8}   |  "
              f"{s['g_top1']/s['n']:>7.0%}{s['g_topk']/s['n']:>8.0%}")
        if kind == "그대로":
            total_fail += s["n"] - s["topk"]

    print(f"\n── 못 찾은 것  (기업으로 좁혔는데도 {topk}위 밖)")
    print(f"   {len(misses)}건")
    for kind, rank, gold, top_cid in misses[:6]:
        t = re.sub(r"\s+", " ", gold["text"])[:60]
        print(f"   [{kind}] {rank}위  {gold['corp_name']} · "
              f"{gold['title'][:20]}")
        print(f"      정답 조각: {t}")
        print(f"      1위 조각:  {byid[top_cid]['title'][:40]}")

    print(f"\n{'통과' if total_fail == 0 else f'실패 {total_fail}건'}"
          "   (판정은 '그대로' 질의 기준. 그것도 못 찾으면 벡터가 잘못된 것이다)")
    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    b, n, k = 1, 40, 10
    for a in sys.argv[1:]:
        if a.startswith("--set="):
            b = int(a.split("=")[1])
        elif a.startswith("--n="):
            n = int(a.split("=")[1])
        elif a.startswith("--topk="):
            k = int(a.split("=")[1])
    sys.exit(main(batch=b, n_probe=n, topk=k))
