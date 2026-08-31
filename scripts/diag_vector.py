"""벡터 검색이 왜 자기 조각을 못 찾는지 가른다.

`probe_vector.py` 로 재니 조각 앞부분 160자를 그대로 질의로 써도 65%만
10위 안에 들었다. 자기 텍스트로 자기를 못 찾는 셈이다.

세 가설이 있고 아직 어느 것인지 모른다.

    가설 1  같은 절의 조각들이 서로 구별되지 않는다
            재무제표는 조각마다 항목명이 같고 숫자만 다르다

    가설 2  표 조각은 숫자가 대부분이라 임베딩이 약하다
            "622,027,314,668" 에는 뜻이 없다

    가설 3  질의 생성 방식이 부적절하다
            앞부분 160자만 쓰면 표 머리글만 들어가고
            그 머리글은 여러 조각에 공통이다

가설 3 은 내 검증기의 문제다. 그것부터 배제해야 나머지를 판단할 수 있다.
검증기를 다섯 번 고쳤던 앞선 경험과 같은 상황이다.

## 어떻게 가르는가

질의를 뜨는 위치를 바꿔 가며 잰다.

    앞 160자      지금 방식
    가운데 160자   머리글을 피한다
    전체          조각 전부를 질의로 쓴다. 이론상 유사도 1.0
    숫자 제거      숫자를 빼고 남은 글자로 만든다

그리고 조각을 표와 문장으로 나눠 각각의 성적을 본다.

전체를 질의로 쓰면 자기 자신과 완전히 같으므로 반드시 1위여야 한다.
그것마저 1위가 아니면 벡터나 저장이 잘못된 것이다.
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
    """표 조각인가. 셀 구분자가 줄마다 있으면 표다."""
    lines = [x for x in text.split("\n") if x.strip()]
    if not lines:
        return False
    return sum(1 for x in lines if CELL in x) / len(lines) > 0.5


def digit_share(text: str) -> float:
    t = re.sub(r"\s+", "", text)
    if not t:
        return 0.0
    return sum(1 for c in t if c.isdigit() or c in ",.") / len(t)


def make_variants(row) -> list[tuple[str, str]]:
    """같은 조각에서 질의를 여러 방식으로 뜬다."""
    body = row["text"]
    flat = re.sub(r"\s+", " ", body).strip()
    mid = len(flat) // 2
    nodigit = re.sub(r"[\d,.\-()]+", " ", flat)
    nodigit = re.sub(r"\s+", " ", nodigit).strip()
    return [
        ("전체", f"{row['header']}\n{body}"),
        ("앞160", flat[:160]),
        ("가운데160", flat[mid:mid + 160]),
        ("숫자제거160", nodigit[:160]),
        ("헤더+앞160", f"{row['header']} {flat[:160]}"),
    ]


def main(batch: int = 1, n_probe: int = 30, topk: int = 10,
         seed: int = 11) -> int:
    from openai_emb import OpenAIEmbedder, normalize

    con = connect()
    corps = batch_corps(batch)
    rows = con.execute(f"""
        SELECT c.chunk_id, c.header, c.text, c.{COL} v, s.title, d.corp_name
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
    corp_idx: dict[str, np.ndarray] = {}
    for r in rows:
        corp_idx.setdefault(r["corp_name"], []).append(pos[r["chunk_id"]])
    corp_idx = {k: np.array(v) for k, v in corp_idx.items()}

    tab = [c for c in ids if is_table(byid[c]["text"])]
    txt = [c for c in ids if c not in set(tab)]
    print(f"   표 조각 {len(tab):,} · 문장 조각 {len(txt):,}")

    random.seed(seed)
    picks = (random.sample(tab, min(n_probe, len(tab)))
             + random.sample(txt, min(n_probe, len(txt))))
    kinds = ["표"] * min(n_probe, len(tab)) + ["문장"] * min(n_probe, len(txt))

    queries, meta = [], []
    for cid, grp in zip(picks, kinds):
        for way, qy in make_variants(byid[cid]):
            if not qy.strip():
                continue
            queries.append(qy[:6000])
            meta.append((cid, grp, way))
    print(f"   질의 {len(queries)}개")

    emb = OpenAIEmbedder()
    qvecs, st = emb.embed_many(queries)
    if not qvecs:
        print(f"질의 임베딩 실패: {st}")
        return 1

    stat: dict[tuple, dict] = {}
    self_sim: dict[str, list] = {}
    for (cid, grp, way), qv in zip(meta, qvecs):
        qa = np.asarray(normalize(qv), dtype=np.float32)
        gold = byid[cid]
        gp = pos[cid]
        idx = corp_idx[gold["corp_name"]]
        sims = M[idx] @ qa
        order = np.argsort(-sims)
        rank = int(np.where(idx[order] == gp)[0][0]) + 1
        s = stat.setdefault((grp, way), {"n": 0, "t1": 0, "tk": 0, "r": []})
        s["n"] += 1
        s["r"].append(rank)
        if rank == 1:
            s["t1"] += 1
        if rank <= topk:
            s["tk"] += 1
        if way == "전체":
            self_sim.setdefault(grp, []).append(float(M[gp] @ qa))

    print(f"\n{'조각':<6}{'질의 방식':<14}{'건수':>5}{'1위':>7}"
          f"{'10위내':>8}{'중앙순위':>9}")
    for grp in ("표", "문장"):
        for way in ("전체", "헤더+앞160", "앞160", "가운데160", "숫자제거160"):
            s = stat.get((grp, way))
            if not s:
                continue
            r = sorted(s["r"])
            print(f"{grp:<6}{way:<14}{s['n']:>5}{s['t1']/s['n']:>7.0%}"
                  f"{s['tk']/s['n']:>8.0%}{r[len(r)//2]:>9}")
        print()

    print("── 자기 자신과의 유사도  (전체를 질의로 썼을 때)")
    for grp, v in self_sim.items():
        v = sorted(v)
        print(f"   {grp}  평균 {sum(v)/len(v):.4f} · 최소 {v[0]:.4f} "
              f"· 중앙 {v[len(v)//2]:.4f}")
    print("      1.0 에 가까워야 한다. 낮으면 저장된 벡터가 그 조각의 것이 아니다")

    # 조각들이 서로 얼마나 비슷한가 — 가설 1 확인
    print("\n── 같은 절 안에서 조각들이 서로 얼마나 비슷한가")
    bysec = {}
    for r in rows:
        bysec.setdefault((r["corp_name"], r["title"]), []).append(pos[r["chunk_id"]])
    multi = [(k, v) for k, v in bysec.items() if len(v) >= 4]
    random.shuffle(multi)
    print(f"   조각이 4개 이상인 절 {len(multi):,}개 중 8개 표본")
    for k, v in multi[:8]:
        sub = M[np.array(v[:12])]
        sim = sub @ sub.T
        n = sim.shape[0]
        off = sim[~np.eye(n, dtype=bool)]
        print(f"      {k[0]:<10}{(k[1] or '(제목없음)')[:24]:<26}"
              f"조각 {len(v):>3}  서로 평균 {off.mean():.4f}")
    return 0


if __name__ == "__main__":
    b, n = 1, 30
    for a in sys.argv[1:]:
        if a.startswith("--set="):
            b = int(a.split("=")[1])
        elif a.startswith("--n="):
            n = int(a.split("=")[1])
    sys.exit(main(batch=b, n_probe=n))
