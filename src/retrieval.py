# -*- coding: utf-8 -*-
"""검색 실행과 채점을 한 곳에 모은다. 측정 코드가 갈라지면 결과를 못 믿는다.

## 왜 만들었나

검색 성적을 재는 스크립트를 셋 만들었더니 서로 다른 숫자를 냈다.

    eval_evalset   문항 16·20 적중 · 자체제작 5 는 4위
    tune_stop      문항 16·20·자체제작 5 전부 1위
    직접 확인       또 다른 결과

후보 구성과 채점 기준이 조금씩 달랐고, 어느 것이 맞는지 알 수 없었다.
설정을 바꿔도 성적이 안 변하는 일까지 겪었다. 그때 `query.py` 가 "보고서" 를
정규식과 STOP 두 곳에서 지우고 있어, STOP 을 바꿔도 효과가 없었다.

측정 코드를 못 믿으면 무엇을 고쳐도 확인이 안 된다. 그래서 한 곳에 모은다.

## 무엇을 하나

    후보 구성      기업 · 연도 · 보고서 종류로 좁힌다
    검색          벡터 · BM25 · RRF
    채점          절 단위. chunk 가 아니라 section 이 답을 주는 단위다

## 후보에서 빼는 것

    목 차          문서의 절 제목을 나열한 조각이다. 어떤 질의와도 낱말이
                  겹쳐 벡터 유사도가 높게 나온다. SK하이닉스 질의에서
                  벡터 1위(0.7274)를 차지했다. 검색 대상이 아니다

    표지 · 확인서   "【 대표이사 등의 확인 】" 처럼 내용이 없는 것

## 채점

정답은 `EVALSET_SOURCE.md` 의 절 이름이다. 사람이 만든 것이라 AI 가 정답을
정하지 않는다. 같은 절의 다른 조각이 걸려도 맞은 것으로 센다. 조각은 찾기
위한 단위이고 답을 주는 단위는 절이기 때문이다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

__all__ = ["Corpus", "search", "grade", "SKIP_TITLE"]

COL = "embedding_oa"

# 후보에서 뺄 절. 검색 대상이 아니다.
SKIP_TITLE = re.compile(r"^\s*(목\s*차|【.*확인.*】|전문가의 확인|"
                        r"전문가와의 이해관계)\s*$")


# ── 층 2  경로 필터 ──────────────────────────────────────────────────
#
# 질의가 무엇을 묻는지 알면 볼 절을 좁힐 수 있다. 사업보고서는 법정 서식이라
# 70개 기업이 같은 경로에 같은 제목을 쓴다.
#
#     "주주환원 정책"  →  III/6  6. 배당에 관한 사항
#     "임원 보수"      →  VIII/2 2. 임원의 보수 등
#
# 매핑은 질의가 아니라 절 본문에서 뽑았다. 평가 질의를 보고 만들면 그 질의에
# 맞춰지기 때문이다. `scripts/build_pathmap.py` 가 만들고 결과는
# `data/eval/pathmap.csv` 에 있다.
#
# 좁히지 않고 점수만 올린다. 잘못 짚으면 정답을 후보에서 빼 버리기 때문이다.
# II/1 사업의 개요처럼 대표 낱말이 안 잡히는 절도 있어, 그런 질의는 매핑에
# 안 걸리고 일반 검색으로 간다.

_PATHMAP: dict[str, dict[str, float]] | None = None
# 경로를 맞힌 조각에 줄 순위 보정. 순위를 이만큼 앞으로 당긴다.
PATH_BOOST = 20
# 이 점수에 못 미치면 경로를 안 짚는다.
#
# 낱말 개수만 세면 변별력이 낮은 말 하나에 끌려간다. "주주환원 정책" 이
# IV/6 을 짚은 것이 그랬다. 그 절의 대표 낱말에 "정책" 이 있어서다.
# 정답은 III/6 배당에 관한 사항이었다.
#
# 점수는 (그 절에 나오는 비율) ÷ (전체 절에 나오는 비율)이다. 실측값을 보면
# 차이가 크다.
#
#     III/6  배당성향 40.9 · 배당수익률 41.2 · 결산배당 35.4
#     II/1   부문 3.8                          이것뿐이다
#
# 문턱을 두면 확신이 있을 때만 짚는다. 못 짚으면 일반 검색으로 간다.
PATH_MIN = 15.0


def pathmap() -> dict[str, dict[str, float]]:
    """경로 → {낱말: 점수}. 점수가 높을수록 그 절만의 낱말이다."""
    global _PATHMAP
    if _PATHMAP is None:
        import csv
        from pathlib import Path
        p = Path(__file__).resolve().parents[1] / "data" / "eval" / "pathmap.csv"
        _PATHMAP = {}
        if p.exists():
            with p.open(encoding="utf-8-sig") as f:
                for r in csv.DictReader(f):
                    d = {}
                    for it in (r.get("scores") or "").split():
                        w, _, s = it.rpartition(":")
                        if w:
                            d[w] = float(s)
                    if d:
                        _PATHMAP[r["path"]] = d
    return _PATHMAP


def guess_paths(terms: list[str], top: int = 2) -> list[str]:
    """검색어로 볼 만한 절 경로를 고른다. 점수 합이 문턱을 넘어야 한다."""
    pm = pathmap()
    if not pm or not terms:
        return []
    ts = set(terms)
    hits = []
    for path, d in pm.items():
        s = sum(v for w, v in d.items() if w in ts)
        if s >= PATH_MIN:
            hits.append((s, path))
    if not hits:
        return []
    hits.sort(reverse=True)
    return [p for _s, p in hits[:top]]


# ── 층 2-B. 사람이 만든 매칭표로 절을 짚는다 ──────────────────────────
#
# 위의 경로 지도(`pathmap`)와 하는 일은 같고 열쇠가 다르다.
#
#     경로 지도    열쇠가 목차 경로.  III/6 같은 것
#     매칭표       열쇠가 절의 정체.  제목 또는 XBRL 태그
#
# 경로를 안 쓰는 이유는 경로가 회사마다 다른 절을 가리키기 때문이다.
# 실측에서 `III/6` 에 18가지 절이 들어앉아 있었다. 배당에 관한 사항이
# 70곳, 범주별 금융상품이 33곳, 매출채권이 21곳이다. 여기에 "결산배당" 을
# 걸면 어떤 회사에서는 재고자산이 튀어오른다.
#
#     경로당 절 수 (평균)
#        I IV V VI VIII IX X XI   1.0    안정적이다
#        III                      5.0    최대 25.  쓸 수 없다
#
# III 가 절의 대부분인데 하필 거기가 못 믿을 자리다. 그래서 절의 정체를
# 열쇠로 쓴다. `scripts/build_section_schema.py` 의 schema_key 와 같은
# 규칙이고, 사전은 `scripts/build_keymap.py` 가 만든다.
_KEYMAP: dict[str, list[tuple]] | None = None
# 매칭표가 짚은 절을 이만큼 앞으로 당긴다. 경로 지도보다 크게 준 이유는
# 사람이 확정한 대응이라 자동 생성물보다 믿을 만하기 때문이다. 다만
# 빼지는 않는다. 잘못 짚어도 정답을 잃지 않게 한다.
KEY_BOOST = 30
NUM_HEAD = re.compile(r"^\s*[\(\[]?\s*[0-9IVXivx]+[\-\.0-9]*\s*[\)\].]?\s*")
TOP_ROMAN = re.compile(r"^(XII|XI|VIII|VII|VI|IV|IX|V|X|III|II|I)(?:/|$)")


def keymap() -> dict[str, list[tuple]]:
    """낱말 → 그 낱말이 가리키는 절들. (대분류, 열쇠종류, 열쇠) 로 담는다."""
    global _KEYMAP
    if _KEYMAP is None:
        import csv
        from pathlib import Path
        p = Path(__file__).resolve().parents[1] / "data" / "eval" / "keymap.csv"
        _KEYMAP = {}
        if p.exists():
            with p.open(encoding="utf-8-sig") as f:
                for r in csv.DictReader(f):
                    _KEYMAP.setdefault(r["word"], []).append(
                        (r["major"], r["key_type"], r["key"]))
    return _KEYMAP


def chunk_key(row) -> tuple[str, str, str] | None:
    """조각이 속한 절의 정체. 매칭표의 열쇠와 같은 규칙으로 만든다."""
    m = TOP_ROMAN.match(row["path"] or "")
    if not m:
        return None
    top = m.group(1)
    ac = row["aclass"]
    if top == "III" and ac and "_U" not in ac:
        return top, "tag", ac
    t = NUM_HEAD.sub("", row["title"] or "")
    t = re.sub(r"\((제조서비스업|금융업)\)", "", t)
    t = re.sub(r"\(연결\)", "", t)
    return top, "title", re.sub(r"\s+", "", t).strip()


def guess_keys(terms: list[str]) -> set[tuple[str, str, str]]:
    """검색어가 가리키는 절들. 매칭표에 없는 말은 그냥 지나간다."""
    km = keymap()
    if not km or not terms:
        return set()
    out: set[tuple[str, str, str]] = set()
    for t in terms:
        for u in km.get(t, ()):
            out.add(u)
    return out


@dataclass
class Hit:
    """검색이 찾아낸 절 하나.

    출처 넷(`corp`·`report`·`rcept`·`year`)을 함께 들고 다닌다. 절 제목만
    있으면 생성 모델이 "자료의 첫 번째 부분에 있습니다" 같은 말밖에 못 한다.
    과제 자료가 "모든 답변에는 근거 공시를 표시할 것" 을 요구하므로 어느
    보고서인지까지 전달되어야 한다.
    """

    rank: int
    chunk_id: int
    section_id: int
    path: str
    title: str
    text: str
    corp: str = ""       # 기업 이름
    report: str = ""     # 보고서명. 예 "사업보고서 (2025.12)"
    rcept: str = ""      # 접수일 YYYYMMDD
    year: int = 0        # 회계연도

    def source(self) -> str:
        """사람이 읽는 출처 한 줄. 답변과 근거 머리글에 그대로 쓴다."""
        d = f"{self.rcept[:4]}-{self.rcept[4:6]}-{self.rcept[6:8]}" \
            if len(self.rcept or "") == 8 else ""
        head = " ".join(x for x in (self.corp, self.report) if x)
        tail = f"접수 {d}" if d else ""
        sec = self.title
        if self.path:
            sec = f"{self.path} {self.title}"
        return " · ".join(x for x in (head, sec, tail) if x)


class Corpus:
    """검색 대상을 한 번만 읽어 두고 여러 설정으로 재쓴다.

    조회 조건의 `c.corp_code IN (...)` 를 지우지 말 것. 없어도 결과는 같지만
    101배 느려진다. 기업을 가리는 조건이 `d.corp_name` 뿐이면 SQLite 는 어느
    조각이 그 기업 것인지 미리 알 수 없어 chunk 17만행을 전부 꺼내 document 와
    이어 붙여 본다. 조각마다 12KB 벡터가 붙어 있어 2GB 를 읽는다.

    chunk 에는 `corp_code` 칸과 그 색인(`ix_chunk_corp`)이 있다. 조건에 이걸
    넣으면 색인을 타고 그 기업 조각만 집는다. 실측(삼성전자 830조각)이다.

        d.corp_name 만        SCAN c                            101.3초
        c.corp_code 추가      SEARCH c USING INDEX ix_chunk_corp   1.1초
                              조각 집합은 완전히 동일
    """

    def __init__(self, corps: list[str], subtype: str = "annual",
                 min_chars: int = 200, latest_only: bool = True,
                 doc_groups: tuple[str, ...] = ("periodic",)):
        """`doc_groups` 로 어떤 종류의 문서를 후보에 넣을지 정한다.

        기본은 정기공시만이다. 감사보고서를 넣으면 검색이 나빠진다.
        2026-09-04 실측에서 질의 30개 중 20개(67%)의 상위 8위 안에 감사보고서
        조각이 끼어들었고, 빼니 1위 적중이 14/37 에서 17/37 로 올랐다.

        왜 나빠지나. 감사보고서의 78%가 첨부 재무제표와 주석이라 사업보고서
        III 과 겹친다. 나머지도 "우리는 감사기준에 따라 감사를 수행하였으며"
        같은 정형 문구라 70개사가 똑같고, 어떤 질의에나 어정쩡하게 걸린다.

        그렇다고 영영 빼는 것은 아니다. 감사보고서에만 있는 내용이 있다.
        주요 감사실시내용 32조각, 감사인의 중요성 금액 4조각, 감사보고서
        원문 463조각이다. 그것을 물으면 `query.parse` 가 알아보고 이 인자에
        'audit' 을 더한다.

        반대로 감사의견·핵심감사사항·감사보수는 사업보고서 `V/1 외부감사에
        관한 사항` 에 더 많이 있다. 그런 질의는 감사보고서를 안 열어도 된다.
        실측에서 "외부감사인과 감사보수" 질의가 감사보고서를 빼니 3위에서
        1위가 됐다.
        """
        from db import connect
        con = connect()
        q = ",".join("?" * len(corps))
        g = ",".join("?" * len(doc_groups))
        rows = con.execute(f"""
            SELECT c.chunk_id, c.section_id, c.header, c.text, c.tokens,
                   c.{COL} v, s.title, s.path, s.aclass, d.corp_name,
                   d.base_year, d.doc_subtype, d.report_nm, d.rcept_dt
            FROM chunk c
            JOIN section s ON c.section_id = s.section_id
            JOIN document d ON c.doc_id = d.doc_id
            WHERE c.corp_code IN (SELECT corp_code FROM document
                                  WHERE corp_name IN ({q}))
              AND c.{COL} IS NOT NULL AND c.tokens IS NOT NULL
              AND c.char_len >= ? AND d.doc_subtype = ?
              AND d.doc_group IN ({g})
              AND d.corp_name IN ({q})""",
                           corps + [min_chars, subtype]
                           + list(doc_groups) + corps).fetchall()
        self.doc_groups = tuple(doc_groups)
        # 목차·확인서를 뺀다
        rows = [r for r in rows if not SKIP_TITLE.match(r["title"] or "")]

        self.rows = rows
        self.ids = [r["chunk_id"] for r in rows]
        self.byid = {r["chunk_id"]: r for r in rows}
        self.pos = {c: i for i, c in enumerate(self.ids)}
        self.M = np.vstack([np.frombuffer(r["v"], dtype=np.float32)
                            for r in rows]) if rows else np.zeros((0, 3072))
        self.sec = np.array([r["section_id"] for r in rows])

        latest: dict[str, int] = {}
        for r in rows:
            latest[r["corp_name"]] = max(latest.get(r["corp_name"], 0),
                                         r["base_year"])
        self.latest = latest
        tmp: dict[tuple, list[int]] = {}
        for r in rows:
            if latest_only and r["base_year"] != latest[r["corp_name"]]:
                continue
            tmp.setdefault((r["corp_name"], r["base_year"]), []).append(
                self.pos[r["chunk_id"]])
        self.pool = {k: np.array(v) for k, v in tmp.items()}
        self._bm: dict[tuple, object] = {}

    def candidates(self, corp: str, year: int | None = None) -> np.ndarray:
        y = year or self.latest.get(corp)
        return self.pool.get((corp, y), np.array([], dtype=int))

    def bm25(self, corp: str, year: int | None = None):
        from rank_bm25 import BM25Okapi
        y = year or self.latest.get(corp)
        key = (corp, y)
        if key not in self._bm:
            idx = self.pool.get(key, np.array([], dtype=int))
            self._bm[key] = BM25Okapi(
                [self.byid[self.ids[i]]["tokens"].split() for i in idx])
        return self._bm[key]


def rrf(lists, weights, k: int = 60) -> list[int]:
    score: dict[int, float] = {}
    for w, lst in zip(weights, lists):
        for rank, i in enumerate(lst, 1):
            score[i] = score.get(i, 0.0) + w / (k + rank)
    return [i for i, _ in sorted(score.items(), key=lambda x: -x[1])]


def search(cp: Corpus, corp: str, qvec, terms: list[str],
           year: int | None = None, weights=(1.0, 1.0),
           topk: int = 10, use_path: bool = False,
           use_key: bool = False) -> list[Hit]:
    """한 기업 안에서 검색한다. 같은 절은 한 번만 낸다."""
    idx = cp.candidates(corp, year)
    if not len(idx):
        return []
    qa = np.asarray(qvec, dtype=np.float32)
    ov = idx[np.argsort(-(cp.M[idx] @ qa))]
    sc = cp.bm25(corp, year).get_scores(terms or ["없음"])
    ob = idx[np.argsort(-sc)]
    if weights[0] == 0:
        order = list(ob)
    elif weights[1] == 0:
        order = list(ov)
    else:
        order = rrf([list(ov), list(ob)], weights)

    # 층 2-B. 매칭표가 짚은 절의 조각을 앞으로 당긴다. 빼지는 않는다.
    # 경로 부스트보다 먼저 적용한다. 사람이 만든 쪽을 더 믿기 때문이다.
    if use_key:
        want = guess_keys(terms)
        if want:
            hit = [i for i in order if chunk_key(cp.byid[cp.ids[i]]) in want]
            if hit:
                seen_h = set(hit)
                rest = [i for i in order if i not in seen_h]
                order = hit[:KEY_BOOST] + rest + hit[KEY_BOOST:]

    # 층 2. 짚은 경로의 조각을 앞으로 당긴다. 빼지는 않는다
    if use_path:
        want = set(guess_paths(terms))
        if want:
            hit = [i for i in order if cp.byid[cp.ids[i]]["path"] in want]
            if hit:
                rest = [i for i in order if i not in set(hit)]
                order = hit[:PATH_BOOST] + rest + hit[PATH_BOOST:]

    out, seen = [], set()
    for i in order:
        r = cp.byid[cp.ids[i]]
        if r["section_id"] in seen:
            continue
        seen.add(r["section_id"])
        out.append(Hit(len(out) + 1, r["chunk_id"], r["section_id"],
                       r["path"] or "", r["title"] or "",
                       re.sub(r"\s+", " ", r["text"]),
                       corp=r["corp_name"] or "",
                       report=r["report_nm"] or "",
                       rcept=str(r["rcept_dt"] or ""),
                       year=r["base_year"] or 0))
        if len(out) >= topk:
            break
    return out


def grade(hits: list[Hit], gold: list[str]) -> list[int]:
    """정답 절이 몇 위에 있는지. 없으면 빈 목록."""
    return [h.rank for h in hits if any(g in h.title for g in gold)]
