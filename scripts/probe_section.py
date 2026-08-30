"""벡터 검색을 절 단위로 잰다. 조각 단위 판정이 목적과 어긋나 있었다.

`diag_vector.py` 로 원인을 규명했다. 한 절이 여러 조각으로 나뉠 때 그
조각들이 서로 0.86~0.97로 매우 비슷하다. 같은 표의 다른 부분이라 머리글이
같고 항목 구조가 같기 때문이다.

그래서 "그 조각" 을 맞히는 성적이 나빴다. 그런데 그것이 목적이 아니다.

    chunk    찾기 위한 단위
    section  답을 주는 단위

찾은 뒤에는 `section_id` 로 원본 절 전체를 꺼낸다. 같은 절의 다른 조각이
1위면 그 절을 찾은 것이므로 성공이다.

앞서 검증 3의 어휘 검사에서 같은 착각을 했다. 수단을 목적으로 재고 있었다.

## 무엇을 재는가

    조각 단위   정답 조각 자신이 상위에 오는가
    절 단위     정답 조각이 속한 절의 조각 아무거나 상위에 오는가
    문서 단위   같은 문서의 조각이 상위에 오는가

셋을 함께 내어 어디까지 맞히는지 본다. 실제로 필요한 것은 절 단위다.
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

COL = "embedding_oa"
CELL = "│"


def vec(b: bytes) -> np.ndarray:
    return np.frombuffer(b, dtype=np.float32)


def batch_corps(n: int) -> list[str]:
    import csv
    p = ROOT / "data" / "eval" / "batches.csv"
    with p.open(encoding="utf-8-sig") as f:
        return [r["corp_name"] for r in csv.DictReader(f) if int(r["set"]) == n]


def is_table(text: str) -> bool:
    lines = [x for x in text.split("\n") if x.strip()]
    return bool(lines) and sum(1 for x in lines if CELL in x) / len(lines) > 0.5


def make_queries(row) -> list[tuple[str, str]]:
    """사람이 쓸 법한 질의 셋. 난이도가 다르다."""
    flat = re.sub(r"\s+", " ", row["text"]).strip()
    head = row["header"] or ""
    corp = head.split(" · ")[0] if " · " in head else ""
    title = row["title"] or ""
    words = [w for w in re.findall(r"[가-힣]{3,}", flat)[:60]][:6]
    return [
        ("본문 인용", flat[:160]),
        ("맥락+낱말", f"{corp} {title} {' '.join(words[:4])}".strip()),
        ("사람 말투", f"{corp}의 {title or (words[0] if words else '')} "
                    f"내용을 알려주세요".strip()),
    ]


def main(batch: int = 1, n_probe: int = 40, topk: int = 10,
         seed: int = 7) -> int:
    from openai_emb import OpenAIEmbedder, normalize

    con = connect()
    corps = batch_corps(batch)
    rows = con.execute(f"""
        SELECT c.chunk_id, c.section_id, c.doc_id, c.header, c.text,
               c.{COL} v, s.title, d.corp_name
        FROM chunk c
        JOIN section s ON c.section_id = s.section_id
        JOIN document d ON c.doc_id = d.doc_id
        WHERE c.{COL} IS NOT NULL AND c.char_len >= 300
          AND d.corp_name IN ({','.join('?' * len(corps))})""",
                       corps).fetchall()
    print(f"set {batch} · 후보 조각 {len(rows):,}")

    ids = [r["chunk_id"] for r in rows]
    pos = {c: i for i, c in enumerate(ids)}
    byid = {r["chunk_id"]: r for r in rows}
    M = np.vstack([vec(r["v"]) for r in rows])
    sec_of = np.array([r["section_id"] for r in rows])
    doc_of = np.array([r["doc_id"] for r in rows])
    corp_idx: dict[str, list] = {}
    for r in rows:
        corp_idx.setdefault(r["corp_name"], []).append(pos[r["chunk_id"]])
    corp_idx = {k: np.array(v) for k, v in corp_idx.items()}

    random.seed(seed)
    picks = random.sample(ids, min(n_probe, len(ids)))
    queries, meta = [], []
    for cid in picks:
        grp = "표" if is_table(byid[cid]["text"]) else "문장"
        for kind, qy in make_queries(byid[cid]):
            if qy.strip():
                queries.append(qy)
                meta.append((cid, kind, grp))
    print(f"질의 {len(queries)}개")

    emb = OpenAIEmbedder()
    qvecs, st = emb.embed_many(queries)
    if not qvecs:
        print(f"질의 임베딩 실패: {st}")
        return 1

    stat: dict[str, dict] = {}
    miss = []
    for (cid, kind, grp), qv in zip(meta, qvecs):
        qa = np.asarray(normalize(qv), dtype=np.float32)
        gold = byid[cid]
        gp = pos[cid]
        idx = corp_idx[gold["corp_name"]]
        order = idx[np.argsort(-(M[idx] @ qa))]

        s = stat.setdefault(kind, {"n": 0, "c1": 0, "ck": 0,
                                   "s1": 0, "sk": 0, "d1": 0, "dk": 0})
        s["n"] += 1
        top = order[:topk]
        # 조각 단위
        if order[0] == gp:
            s["c1"] += 1
        if gp in top:
            s["ck"] += 1
        # 절 단위
        gs = gold["section_id"]
        if sec_of[order[0]] == gs:
            s["s1"] += 1
        if (sec_of[top] == gs).any():
            s["sk"] += 1
        else:
            miss.append((kind, grp, gold, int(order[0])))
        # 문서 단위
        gd = gold["doc_id"]
        if doc_of[order[0]] == gd:
            s["d1"] += 1
        if (doc_of[top] == gd).any():
            s["dk"] += 1

    print(f"\n{'질의 종류':<12}{'건수':>5} | {'조각 1위':>8}{'조각 10위':>9}"
          f" | {'절 1위':>8}{'절 10위':>9} | {'문서 1위':>8}{'문서 10위':>9}")
    for kind in ("본문 인용", "맥락+낱말", "사람 말투"):
        s = stat.get(kind)
        if not s:
            continue
        n = s["n"]
        print(f"{kind:<12}{n:>5} | {s['c1']/n:>8.0%}{s['ck']/n:>9.0%}"
              f" | {s['s1']/n:>8.0%}{s['sk']/n:>9.0%}"
              f" | {s['d1']/n:>8.0%}{s['dk']/n:>9.0%}")

    print(f"\n── 절도 못 찾은 것 {len(miss)}건")
    for kind, grp, gold, top_i in miss[:8]:
        t = re.sub(r"\s+", " ", gold["text"])[:56]
        w = byid[ids[top_i]]
        print(f"   [{kind}·{grp}] {gold['corp_name']} · "
              f"{(gold['title'] or '(제목없음)')[:22]}")
        print(f"      정답: {t}")
        print(f"      1위 : {(w['title'] or '(제목없음)')[:22]} · "
              f"{re.sub(r'\\s+', ' ', w['text'])[:40]}")
    return 0


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
