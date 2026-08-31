"""빈 절 조회. 문서에 그 절이 있으나 내용이 없는 경우를 찾는다.

벡터 검색으로는 이것을 못 잡는다. 내용이 없으니 임베딩 대상이 아니고,
검색에 안 걸리면 "찾지 못했습니다" 로 답하게 된다. 그런데 실제로는 그 절이
존재하고 비어 있으므로 "해당 사항 없음" 이 정답이다.

DECISIONS 2026-08-14 의 네 갈래에서 not_disclosed 를 extract_failed 로
잘못 답하는 셈이고, 지표 7 정보한계 대응의 감점 지점이다.

빈 절이 두 종류다. 이것을 구분하지 않으면 틀린 답을 한다.

    진짜 비어 있다     그 회사가 그 해에 그 일을 안 했다
                       XI/4 작성기준일 이후 주요사항  954건 중 350건이 빔
                       나머지 604건에는 내용이 있다

    하위에 있다        제목만 있고 내용은 하위 절에 있다
                       III/7 증권의 발행을 통한 자금조달  961건 전부 빔
                       내용은 III/7-1 · III/7-2 에 있다
"""
from __future__ import annotations

from db import connect


def section_status(con, doc_id: str, path: str) -> dict:
    """한 문서의 한 절이 어떤 상태인지 낸다.

    셋 중 하나다.
        has_content   내용이 있다
        empty         절은 있으나 비어 있다. "해당 사항 없음"
        in_children   제목만 있고 내용은 하위 절에 있다
        no_section    그 절 자체가 문서에 없다
    """
    rows = con.execute(
        """SELECT path, title, char_len, text_len, table_len, section_id
           FROM section WHERE doc_id = ? AND (path = ? OR path LIKE ?)
           ORDER BY seq""", (doc_id, path, path + "/%")).fetchall()
    if not rows:
        return {"status": "no_section", "path": path}

    self_rows = [r for r in rows if r["path"] == path]
    kids = [r for r in rows if r["path"] != path]
    self_len = sum(r["char_len"] for r in self_rows)
    kid_len = sum(r["char_len"] for r in kids)

    if self_len > 0:
        return {"status": "has_content", "path": path,
                "title": self_rows[0]["title"],
                "char_len": self_len,
                "section_ids": [r["section_id"] for r in self_rows]}
    if kid_len > 0:
        return {"status": "in_children", "path": path,
                "title": self_rows[0]["title"] if self_rows else "",
                "children": [{"path": r["path"], "title": r["title"],
                              "char_len": r["char_len"],
                              "section_id": r["section_id"]}
                             for r in kids if r["char_len"] > 0]}
    return {"status": "empty", "path": path,
            "title": self_rows[0]["title"] if self_rows else "",
            "children": [{"path": r["path"], "title": r["title"]} for r in kids]}


def find_sections(con, doc_ids: list[str], keyword: str) -> list[dict]:
    """제목에 그 말이 든 절을 찾는다.

    "자금조달" 로 물으면 "7. 증권의 발행을 통한 자금조달에 관한 사항" 을 찾는다.
    벡터 검색과 별개 경로다. 절 제목으로 직접 찾으므로 내용이 비어 있어도 걸린다.
    """
    if not doc_ids:
        return []
    q = ",".join("?" * len(doc_ids))
    rows = con.execute(
        f"""SELECT s.doc_id, s.path, s.title, s.char_len, s.section_id,
                   d.corp_name, d.report_nm
            FROM section s JOIN document d ON s.doc_id = d.doc_id
            WHERE s.doc_id IN ({q}) AND s.title LIKE ?
            ORDER BY s.doc_id, s.seq""", doc_ids + [f"%{keyword}%"]).fetchall()
    out = []
    for r in rows:
        st = section_status(con, r["doc_id"], r["path"])
        out.append({"doc_id": r["doc_id"], "path": r["path"], "title": r["title"],
                    "corp_name": r["corp_name"], "report": r["report_nm"],
                    "char_len": r["char_len"], "status": st["status"],
                    "detail": st})
    return out


def answer_empty(st: dict, corp: str = "", report: str = "") -> str:
    """상태를 답변 문구로.

    D7 의 네 갈래 중 무엇인지 밝힌다. "찾지 못했습니다" 와
    "기재되지 않았습니다" 는 다른 말이다.
    """
    who = f"{corp} {report}".strip()
    t = st.get("title", st.get("path", ""))
    if st["status"] == "no_section":
        return (f"{who} 에는 해당 항목이 없습니다. "
                f"보고서 종류에 따라 기재하지 않는 절입니다.")
    if st["status"] == "empty":
        return (f"{who} 의 「{t}」 항목은 존재하나 기재된 내용이 없습니다. "
                f"해당 기간에 관련 사항이 없었던 것으로 보입니다.")
    if st["status"] == "in_children":
        subs = " · ".join(c["title"] for c in st.get("children", [])[:4])
        return (f"「{t}」 는 제목만 있고 내용은 하위 항목에 있습니다: {subs}")
    return ""
