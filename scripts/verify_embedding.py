"""임베딩을 검증한다. 마지막 관문이다.

여기까지 오면 되돌리는 데 2시간과 42,474원이 든다. 그래서 앞의 세 관문을
먼저 통과해야 하고, 여기서는 벡터 자체와 벡터-조각 연결을 본다.

여섯 겹을 둔다.

    1 빠짐         벡터가 없는 조각
    2 차원         모델이 바뀌지 않았는가
    3 노름         정규화됐는가. 내적으로 유사도를 재므로 1 이어야 한다
    4 중복 벡터     서로 다른 조각인데 벡터가 같은 것
    5 이웃 일관성   같은 문서 조각끼리 더 가까운가
    6 왕복         텍스트를 다시 임베딩해 저장된 벡터와 맞춰 본다

4번이 특히 중요하다. API 가 오류를 내면서도 같은 값을 반환하는 경우가 있는데
개수만 세면 절대 못 잡는다.

6번은 API 를 호출하므로 돈이 든다. 표본 20개면 1원 미만이다. 기본으로 끄고
--roundtrip 을 줄 때만 한다. 앞의 다섯이 전부 통과해도 벡터와 조각의 연결이
어긋나 있으면 검색이 완전히 틀리는데, 그것을 잡는 유일한 검사다.

주의 — 이 스크립트는 아직 실제 데이터로 검증되지 않았다. 임베딩을 다시 만든
뒤에야 각 항목이 제대로 잡는지 확인된다.
"""
import array
import math
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8")

from db import connect

# 컬럼마다 모델이 다르다
MODELS = {
    "embedding_oa": ("OpenAI text-embedding-3-large", 3072),
    "embedding": ("CLOVA bge-m3", 1024),
}
NORM_TOL = 0.01
# 이웃 일관성. 같은 문서 조각 쌍의 평균 유사도가 다른 문서 쌍보다
# 이만큼은 높아야 한다. 낮으면 벡터가 내용을 안 담고 있다는 뜻이다.
NEIGHBOR_GAP = 0.05
# 왕복 검사의 하한. 1.0 이 아닌 이유가 있다.
#
# API 는 배정밀도(float64)로 주는데 우리는 단정밀도(float32)로 저장한다.
# 3,072개 숫자를 각각 반올림하므로 오차가 쌓여 내적이 1.0 에서 조금 벗어난다.
# 표본 40건을 재니 최소 0.9971 · 중앙 0.99995 였고 절반 가까이가 1.0 미만이었다.
# 같은 입력을 세 번 불러도 서로는 완전히 같았으므로 API 흔들림이 아니다.
#
# float32 는 의도한 선택이다. float64 로 저장하면 171,564조각에 4GB 가 든다.
#
# 벡터가 실제로 뒤바뀌면 0.5 아래로 떨어지므로 0.99 면 충분히 잡는다.
# 처음에 0.999 로 뒀다가 정상인 조각을 12.5% 실패로 잡았다.
ROUNDTRIP_MIN = 0.99


def vec(b: bytes) -> array.array:
    a = array.array("f")
    a.frombytes(b)
    return a


def dot(a, b) -> float:
    return sum(x * y for x, y in zip(a, b))


def batch_corps(n: int) -> list[str]:
    """묶음에 든 기업 이름. 없으면 빈 목록."""
    import csv
    p = ROOT / "data" / "eval" / "batches.csv"
    if not p.exists():
        return []
    with p.open(encoding="utf-8-sig") as f:
        return [r["corp_name"] for r in csv.DictReader(f) if int(r["set"]) == n]


def main(col: str = "embedding_oa", sample: int = 300,
         roundtrip: int = 0, batch: int | None = None) -> int:
    name, dim = MODELS[col]
    con = connect()
    q = lambda s, *a: con.execute(s, *a).fetchall()
    fail = 0

    # 묶음으로 나눠 임베딩하므로 검증도 그 범위로 좁힌다.
    # 전체를 보면 아직 임베딩하지 않은 묶음이 "벡터 없음" 으로 잡혀
    # 실제 문제와 섞인다.
    corps = batch_corps(batch) if batch else []
    if batch and not corps:
        raise SystemExit(f"set {batch} 를 batches.csv 에서 찾지 못했다")
    where = ""
    args: list = []
    if corps:
        where = (" AND c.doc_id IN (SELECT doc_id FROM document"
                 f" WHERE corp_name IN ({','.join('?' * len(corps))}))")
        args = corps

    print(f"── {name}  ({col})")
    if corps:
        print(f"   set {batch} · 기업 {len(corps)}개  " + " · ".join(corps))
    r = q(f"""SELECT COUNT(*) n,
                     SUM(CASE WHEN c.{col} IS NULL THEN 1 ELSE 0 END) nul
              FROM chunk c WHERE 1=1{where}""", args)[0]
    print(f"   조각 {r['n']:,} · 벡터 없음 {r['nul']:,}")

    # ── 1 빠짐
    print("\n1 빠짐      벡터가 없는 조각")
    print(f"   {r['nul']:,}건")
    fail += r["nul"]
    if r["n"] == r["nul"]:
        print("\n벡터가 하나도 없다. 임베딩을 먼저 만든다.")
        return 1

    # ── 2 차원
    print(f"\n2 차원      {dim}차원이 아닌 벡터")
    rows = q(f"""SELECT c.chunk_id, length(c.{col}) b FROM chunk c
                 WHERE c.{col} IS NOT NULL AND length(c.{col}) <> ?{where}""",
             [dim * 4] + args)
    print(f"   {len(rows):,}건")
    for x in rows[:5]:
        print(f"      chunk {x['chunk_id']}  {x['b'] // 4}차원")
    fail += len(rows)

    # ── 3 노름 · 4 중복
    print(f"\n3 노름      정규화되지 않은 벡터  (표본 {sample:,})")
    rows = q(f"""SELECT c.chunk_id, c.doc_id, c.{col} v FROM chunk c
                 WHERE c.{col} IS NOT NULL{where}
                 ORDER BY RANDOM() LIMIT ?""", args + [sample])
    badnorm = []
    seen = {}
    dup = []
    for x in rows:
        a = vec(x["v"])
        n = math.sqrt(sum(t * t for t in a))
        if abs(n - 1.0) > NORM_TOL:
            badnorm.append((x["chunk_id"], n))
        h = hash(x["v"])
        if h in seen:
            dup.append((seen[h], x["chunk_id"]))
        else:
            seen[h] = x["chunk_id"]
    print(f"   {len(badnorm):,}건")
    for cid, n in badnorm[:5]:
        print(f"      chunk {cid}  노름 {n:.4f}")
    fail += len(badnorm)

    print("\n4 중복 벡터  서로 다른 조각인데 벡터가 같은 것")
    print(f"   {len(dup):,}쌍")
    for a, b in dup[:5]:
        print(f"      chunk {a} == chunk {b}")
    fail += len(dup)

    # ── 5 이웃 일관성
    print("\n5 이웃 일관성  같은 문서 조각끼리 더 가까운가")
    bydoc = {}
    for x in rows:
        bydoc.setdefault(x["doc_id"], []).append(vec(x["v"]))
    same, diff = [], []
    docs = [d for d, v in bydoc.items() if len(v) >= 2]
    for d in docs[:40]:
        vs = bydoc[d][:6]
        for i in range(len(vs)):
            for j in range(i + 1, len(vs)):
                same.append(dot(vs[i], vs[j]))
    keys = list(bydoc)
    for i in range(min(len(keys), 40)):
        for j in range(i + 1, min(len(keys), 40)):
            diff.append(dot(bydoc[keys[i]][0], bydoc[keys[j]][0]))
    if same and diff:
        ms, md = sum(same) / len(same), sum(diff) / len(diff)
        gap = ms - md
        print(f"   같은 문서 쌍 {len(same):,}개 평균 {ms:.4f}")
        print(f"   다른 문서 쌍 {len(diff):,}개 평균 {md:.4f}")
        print(f"   차이 {gap:+.4f}  (기준 {NEIGHBOR_GAP:+.2f} 이상)")
        if gap < NEIGHBOR_GAP:
            print("      벡터가 내용을 담고 있지 않을 수 있다.")
            fail += 1
    else:
        print("   표본이 부족해 재지 못했다")

    # ── 6 왕복
    if roundtrip:
        print(f"\n6 왕복      텍스트를 다시 임베딩해 대조  (표본 {roundtrip})")
        print("      API 를 호출한다. 조각 20개면 1원 미만이다.")
        rows = q(f"""SELECT c.chunk_id, c.header, c.text, c.{col} v FROM chunk c
                     WHERE c.{col} IS NOT NULL
                       AND c.char_len BETWEEN 200 AND 3000{where}
                     ORDER BY RANDOM() LIMIT ?""", args + [roundtrip])
        if col == "embedding_oa":
            from openai_emb import OpenAIEmbedder, normalize
            emb = OpenAIEmbedder()
            # embed_many 는 (벡터 목록, 상태) 를 낸다. 튜플을 그대로 쓰면
            # normalize 가 리스트의 리스트를 받아 터진다.
            # 적재할 때와 같은 형식이어야 한다. 다르면 벡터가 안 맞아
            # 멀쩡한 것을 실패로 잡는다.
            texts = [f"{r['header']}\n{r['text']}" for r in rows]
            got, st = emb.embed_many(texts)
            if not got:
                print(f"   질의 임베딩 실패: {st[:100]}")
                got = []
        else:
            from clova import ClovaEmbedder, normalize
            emb = ClovaEmbedder()
            got = [emb.embed(f"{r['header']} {r['text']}") for r in rows]
        bad = []
        for r, g in zip(rows, got):
            if g is None:
                continue
            sim = dot(vec(r["v"]), normalize(g))
            if sim < ROUNDTRIP_MIN:
                bad.append((r["chunk_id"], sim))
        print(f"   저장된 벡터와 다른 조각 {len(bad):,}건")
        for cid, s in bad[:5]:
            print(f"      chunk {cid}  유사도 {s:.4f}")
        if bad:
            print("      벡터가 다른 조각에 붙어 있을 수 있다.")
            print("      앞의 다섯을 전부 통과해도 이것이 어긋나면 검색이 틀린다.")
        fail += len(bad)
    else:
        print("\n6 왕복      건너뜀. --roundtrip=20 을 주면 한다")

    print(f"\n{'통과' if fail == 0 else f'실패 {fail:,}건'}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    c, smp, rt, b = "embedding_oa", 300, 0, None
    for a in sys.argv[1:]:
        if a.startswith("--col="):
            c = a.split("=")[1]
        elif a.startswith("--sample="):
            smp = int(a.split("=")[1])
        elif a.startswith("--roundtrip"):
            rt = int(a.split("=")[1]) if "=" in a else 20
        elif a.startswith("--set="):
            b = int(a.split("=")[1])
    sys.exit(main(col=c, sample=smp, roundtrip=rt, batch=b))
