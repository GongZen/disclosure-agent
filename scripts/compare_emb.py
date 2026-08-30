"""같은 질의로 두 임베딩 모델의 검색 결과를 견준다.

테스트 설계에서 지킨 것 셋이다.

    실전 형태 질의   평가 질의가 "OO기업의 2025년 …" 처럼 기업·기간을 담는다
                     키워드만 던지면 모호해서 무엇을 가져와도 틀렸다고 하기 어렵다
    정답을 아는 질의  조각을 먼저 읽고 그 안에 답이 있는 질의를 만들었다
                     답이 없는 질의로는 검색 품질을 잴 수 없다
    필터를 먼저      실전은 기업·문서로 후보를 좁힌 뒤 본문을 찾는다
                     필터 없이 전체에서 찾으면 검색 방식의 문제가 모델 차이로 보인다

어느 모델이 나은지는 이 스크립트가 판정하지 않는다. 정답 조각이 몇 위에
오는지만 표시하고 판단은 사람이 한다.
"""
import array
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8")

from db import connect
from clova import Embedder, normalize as cl_norm
from openai_emb import OpenAIEmbedder, normalize as oa_norm

# (질의, 기업 필터, 정답이 있어야 할 절)
# 조각을 먼저 읽고 그 안에 답이 있는 것으로 만들었다.
CASES = [
    ("JYP Ent의 사업부문은 어떻게 나뉘어 있나요?", "JYP Ent", "II/1"),
    ("HMM의 컨테이너 부문 매출 비중은 얼마인가요?", "HMM", "II/4"),
    ("CJ제일제당이 사용하는 주요 원재료와 원산지를 알려주세요", "CJ제일제당", "II/3"),
    ("KB금융의 해외 진출 현황을 설명해주세요", "KB금융", None),
    ("HD현대중공업의 생산설비 투자 계획을 정리해주세요", "HD현대중공업", "II/3"),
    ("CJ제일제당의 주요 계약 상대방은 누구인가요?", "CJ제일제당", "II/6"),
    ("HMM이 직면한 주요 위험 요인은 무엇인가요?", "HMM", "II/5"),
    ("HD현대일렉트릭의 연구개발 활동을 알려주세요", "HD현대일렉트릭", "II/6"),
]


def unpack(b):
    a = array.array("f")
    a.frombytes(b)
    return list(a)


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def main(topk: int = 5, only: int | None = None, no_filter: bool = False):
    con = connect()
    ce, oe = Embedder(), OpenAIEmbedder()
    cases = CASES if only is None else [CASES[only]]
    tally = {"CLOVA": 0, "OpenAI": 0, "n": 0}

    for q, corp, want in cases:
        # 실전처럼 기업으로 먼저 좁힌다. 내용 없는 조각은 뺀다.
        sql = """SELECT c.chunk_id, c.header, c.text, c.embedding, c.embedding_oa,
                        s.path, d.report_nm
                 FROM chunk c
                 JOIN section s ON c.section_id = s.section_id
                 JOIN document d ON c.doc_id = d.doc_id
                 WHERE c.embedding IS NOT NULL AND c.embedding_oa IS NOT NULL
                   AND c.char_len >= 100"""
        args = []
        if corp and not no_filter:
            sql += " AND d.corp_name = ?"
            args.append(corp)
        rows = con.execute(sql, args).fetchall()

        print("=" * 78)
        print(f"질의   {q}")
        print(f"필터   {corp or '없음'} · 후보 {len(rows):,}개"
              + (f" · 정답 절 {want}" if want else ""))
        print("=" * 78)
        cv, st1 = ce.embed(q)
        ov, st2 = oe.embed(q)
        if not cv or not ov:
            print(f"   질의 임베딩 실패  {st1[:40]} / {st2[:40]}\n")
            continue
        cv, ov = cl_norm(cv), oa_norm(ov)

        hits = {}
        for label, col, qvec in (("CLOVA", "embedding", cv),
                                 ("OpenAI", "embedding_oa", ov)):
            scored = sorted(
                ((dot(qvec, unpack(r[col])), r) for r in rows),
                key=lambda x: -x[0])[:topk]
            print(f"\n  [{label}]")
            hit = None
            for rank, (s, r) in enumerate(scored, 1):
                mark = ""
                if want and r["path"] == want:
                    mark = "  ← 정답 절"
                    if hit is None:
                        hit = rank
                body = " ".join(r["text"].split())[:76]
                print(f"   {rank}. {s:.3f}  {r['path']:<9}{r['report_nm'][:18]:<20}{mark}")
                print(f"      {body}")
            hits[label] = hit
        if want:
            tally["n"] += 1
            for k in ("CLOVA", "OpenAI"):
                if hits.get(k) == 1:
                    tally[k] += 1
            print(f"\n   정답 절 최고 순위   CLOVA {hits.get('CLOVA') or '없음'}"
                  f" · OpenAI {hits.get('OpenAI') or '없음'}")
        print()
    if tally["n"]:
        print("=" * 78)
        print(f"정답 절을 1위로 가져온 횟수   "
              f"CLOVA {tally['CLOVA']}/{tally['n']} · OpenAI {tally['OpenAI']}/{tally['n']}")
    return 0


if __name__ == "__main__":
    only = None
    nf = "--no-filter" in sys.argv
    for a in sys.argv[1:]:
        if a.startswith("--q="):
            only = int(a.split("=")[1])
    sys.exit(main(only=only, no_filter=nf))
