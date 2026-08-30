"""S7 본문 검색. chunk 에서 질의에 맞는 조각을 찾는다.

세 가지를 쓴다.

    벡터 검색    뜻이 비슷한 것을 찾는다. "자금조달" 로 물어도 "유상증자" 가 걸린다
    BM25        단어가 정확히 맞는 것을 찾는다. 고유명사·종목코드에 강하다
    RRF         둘의 순위를 합친다. 점수를 안 쓰고 순위만 쓴다

필터를 먼저 거는 것이 중요하다. W6 임베딩 비교에서 확인했다. 필터 없이
전체에서 찾으면 다른 기업 조각이 섞여 검색 방식의 문제가 모델 차이로 보인다.

임베딩이 두 벌 있다. `embedding`(CLOVA) 과 `embedding_oa`(OpenAI) 다.
어느 쪽을 쓸지는 호출할 때 정한다. 마지막에 실측으로 고른다.
"""
from __future__ import annotations

import array
import math

from db import connect


def unpack(b: bytes) -> list[float]:
    a = array.array("f")
    a.frombytes(b)
    return list(a)


def dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def normalize(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v] if n else v


def candidates(con, corp: str | list[str] | None = None,
               year: int | list[int] | None = None,
               subtype: str | None = None,
               path_prefix: str | None = None,
               min_chars: int = 100,
               col: str = "embedding_oa") -> list:
    """검색 후보를 좁힌다. 이 단계가 검색 품질을 좌우한다.

    min_chars 로 내용 없는 조각을 뺀다. "해당사항 없음" 1,423건,
    "본문 위치로 이동" 473건 같은 것이 검색 결과를 채우면 답이 안 나온다.
    다만 그 조각들은 section 에 그대로 있어 빈 절 조회로 찾을 수 있다.
    """
    sql = [f"""SELECT c.chunk_id, c.section_id, c.header, c.text, c.{col} AS vec,
                      c.tokens, s.path, s.title, d.corp_name, d.report_nm,
                      d.base_year, d.doc_subtype, c.doc_id
               FROM chunk c
               JOIN section s ON c.section_id = s.section_id
               JOIN document d ON c.doc_id = d.doc_id
               WHERE c.{col} IS NOT NULL AND c.char_len >= ?"""]
    args: list = [min_chars]
    if corp:
        names = [corp] if isinstance(corp, str) else list(corp)
        sql.append(f"AND d.corp_name IN ({','.join('?' * len(names))})")
        args += names
    if year:
        ys = [year] if isinstance(year, int) else list(year)
        sql.append(f"AND d.base_year IN ({','.join('?' * len(ys))})")
        args += [str(y) for y in ys]
    if subtype:
        sql.append("AND d.doc_subtype = ?")
        args.append(subtype)
    if path_prefix:
        sql.append("AND (s.path = ? OR s.path LIKE ?)")
        args += [path_prefix, path_prefix + "/%"]
    return con.execute(" ".join(sql), args).fetchall()


def vector_search(qvec: list[float], rows: list, topk: int = 10) -> list:
    """질의 벡터와 조각 벡터의 내적으로 정렬한다.

    둘 다 정규화돼 있으므로 내적이 곧 코사인 유사도다.
    """
    q = normalize(qvec)
    scored = [(dot(q, unpack(r["vec"])), r) for r in rows]
    scored.sort(key=lambda x: -x[0])
    return scored[:topk]


# ── BM25 ─────────────────────────────────────────────────────────────
# 단어가 정확히 맞는 것을 찾는다. 벡터 검색이 못 하는 일을 한다.
#
#     "전환사채" 로 물으면 그 단어가 든 조각을 찾는다
#     벡터 검색은 뜻으로 넓히므로 "신주인수권부사채" 도 걸린다
#     고유명사·종목코드·법률 용어는 정확히 맞아야 한다
#
# 한국어는 형태소 분석이 필요하다. 공백으로만 자르면 "전환사채의" 와
# "전환사채가" 가 다른 낱말이 되어 BM25 가 무력해진다.

_kiwi = None
_KEEP = ("XR", "SL", "SN", "SH")


def _analyzer():
    global _kiwi
    if _kiwi is None:
        from kiwipiepy import Kiwi
        from terms import terms
        _kiwi = Kiwi()
        for w in terms():
            _kiwi.add_user_word(w, "NNP")
    return _kiwi


def tokenize(text: str) -> list[str]:
    """명사·동사·어근·외국어만 남긴다. 조사와 어미는 검색에 쓸모가 없다.

    세 가지를 한다. 셋 다 실측으로 정했다.

    ## 1  회계 용어 사전을 적용한다

    kiwipiepy 는 회계 용어를 하나의 낱말로 알지 못한다. "자산총계" 가
    자산·총계로 잘리면, 그 둘이 여러 번 나오는 부문별 보고 조각이 정작
    자산총계가 적힌 재무상태표보다 점수가 높아진다. 삼성전자에서 상위 10개
    중 3개만 맞았다. 사전을 넣으니 10개가 됐다. `terms.py` 참조.

    ## 2  접두사를 앞 명사에 붙인다

    "미지급비용" 은 미(XPN) + 지급 + 비용 으로 잘린다. XPN 을 버리면
    "지급비용" 이 되어 뜻이 뒤집히고, 임원 보수 조각에 지급·비용이 흔해
    함께 걸린다. 상위 10개 중 5개만 맞았다. 붙이니 10개가 됐다.

    사전이 아니라 규칙이라 새 낱말에도 자동으로 적용된다.

    ## 3  사전 낱말은 조각도 함께 낸다

    원형만 남기면 부분 질의를 잃는다.

        문서 "당기순이익" → 당기순이익
        질의 "순이익"     → 순이익            안 걸린다

    원형과 조각을 둘 다 내면 정확한 질의와 부분 질의가 모두 걸린다.
    흔한 조각은 IDF 가 낮아 점수 기여가 작으므로 BM25 가 알아서 가중한다.
    토큰이 8.9% 는다.
    """
    from terms import parts_of

    out: list[str] = []
    pend = ""
    for t in _analyzer().tokenize(text[:20000]):
        tag, form = t.tag, t.form
        if tag == "XPN":                       # 미· 비· 재· 무· 등
            pend = form
            continue
        if tag[0] not in "NV" and tag not in _KEEP:
            pend = ""
            continue
        w = pend + form
        pend = ""
        if len(w) > 1 or tag in ("SL", "SN"):
            out.append(w)
            if w != form and len(form) > 1:
                out.append(form)               # 접두사를 뗀 형태도 남긴다
            out.extend(parts_of(w))            # 사전 낱말이면 그 조각도
    return out


def bm25_search(query: str, rows: list, topk: int = 10) -> list:
    """후보 안에서만 BM25 를 돌린다.

    전체 141,524조각으로 인덱스를 미리 만들지 않는다. 필터로 좁힌 뒤
    그 자리에서 만드는 편이 낫다. 문서 필터가 바뀔 때마다 인덱스를
    다시 만들 필요가 없다.

    토큰은 build_tokens.py 가 미리 만들어 chunk.tokens 에 넣어 둔다.
    검색할 때마다 형태소 분석을 하면 후보 2,000개에 65초가 걸린다.
    미리 만들어 두면 공백으로 나누기만 하면 된다.
    """
    from rank_bm25 import BM25Okapi

    if not rows:
        return []
    corpus = []
    for r in rows:
        tk = r["tokens"] if "tokens" in r.keys() else None
        corpus.append(tk.split() if tk else tokenize(f"{r['header']} {r['text']}"))
    bm = BM25Okapi(corpus)
    scores = bm.get_scores(tokenize(query))
    scored = sorted(zip(scores, rows), key=lambda x: -x[0])
    return scored[:topk]


# ── RRF ──────────────────────────────────────────────────────────────

def rrf(*ranked_lists, weights: list[float] | None = None,
        k: int = 60, topk: int = 10) -> list:
    """여러 검색 결과를 순위로 합친다.

    점수를 쓰지 않고 순위만 쓴다. 벡터 유사도 0.71 과 BM25 점수 8.3 은
    척도가 달라 직접 더할 수 없다. 순위로 바꾸면 그 문제가 사라진다.

        점수 = Σ 가중치 × 1 / (k + 순위)

    k=60 은 관례적인 값이다. 낮으면 1위에 쏠리고 높으면 평평해진다.

    ## 가중치

    기본은 전부 1 이다. 목록마다 같은 무게를 준다.

    set 1 실측에서 조합에 따라 성적이 갈렸다.

        필터+벡터      절 10위 안  80%
        필터+BM25     절 10위 안 100%
        필터+RRF(1:1) 절 10위 안  96%

    RRF 가 BM25 단독보다 낮다. 1대1로 섞으니 벡터가 순위를 흐린다.

    다만 이 측정은 조각의 낱말을 그대로 쓴 질의로 한 것이라 BM25 에 유리한
    조건이었다. 실제 질의는 원문 표현을 그대로 쓰지 않는다. "자금조달" 로
    물어도 "유상증자" 를 찾아야 하는 경우가 벡터의 몫이다.

    그래서 값을 지금 정하지 않는다. 인자만 두고 사용자가 준비한 평가 질의로
    잰 뒤 확정한다. 근거 없는 숫자를 코드에 박으면 나중에 그 숫자가 판단을
    흐린다.
    """
    if weights is None:
        weights = [1.0] * len(ranked_lists)
    if len(weights) != len(ranked_lists):
        raise ValueError(f"목록 {len(ranked_lists)}개에 가중치 {len(weights)}개")
    score: dict = {}
    keep: dict = {}
    for w, lst in zip(weights, ranked_lists):
        for rank, (_s, r) in enumerate(lst, 1):
            cid = r["chunk_id"]
            score[cid] = score.get(cid, 0.0) + w / (k + rank)
            keep[cid] = r
    out = sorted(score.items(), key=lambda x: -x[1])[:topk]
    return [(s, keep[cid]) for cid, s in out]
