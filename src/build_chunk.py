"""section 을 검색용 조각으로 나눠 chunk 에 담는다.

내용이 없는 section 은 담지 않는다. 39,677개가 제목만 있고 비어 있는데
그 절이 존재하나 기재가 없다는 뜻이다. 검색 대상이 아니라 SQL 로 조회할
사항이고, W7 에서 그 경로를 만든다.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8")

from db import connect, create_chunk_schema
from chunk import split, est_tokens, make_header


def main(reset: bool = False) -> int:
    con = connect()
    create_chunk_schema(con)
    if reset:
        con.execute("DELETE FROM chunk")
        con.commit()

    rows = con.execute("""SELECT s.section_id, s.doc_id, s.corp_code, s.path,
                                 s.title, s.text, s.n_table, s.char_len,
                                 d.corp_name, d.report_nm
                          FROM section s
                          JOIN document d ON s.doc_id = d.doc_id
                          WHERE s.char_len > 0
                          ORDER BY s.section_id""").fetchall()
    n_sec = n_chunk = 0
    buf = []
    for r in rows:
        n_sec += 1
        header = make_header(r["corp_name"], r["report_nm"],
                             r["path"], r["title"])
        for i, part in enumerate(split(r["text"])):
            if not part.strip():
                continue
            buf.append((r["section_id"], r["doc_id"], r["corp_code"], i,
                        header, part, len(part), est_tokens(part),
                        r["n_table"] if i == 0 else 0))
            n_chunk += 1
        if len(buf) >= 5000:
            con.executemany(
                """INSERT OR IGNORE INTO chunk
                   (section_id,doc_id,corp_code,seq,header,text,
                    char_len,token_est,n_table)
                   VALUES (?,?,?,?,?,?,?,?,?)""", buf)
            con.commit()
            buf = []
            print(f"   section {n_sec:,} · chunk {n_chunk:,}")
    if buf:
        con.executemany(
            """INSERT OR IGNORE INTO chunk
               (section_id,doc_id,corp_code,seq,header,text,
                char_len,token_est,n_table)
               VALUES (?,?,?,?,?,?,?,?,?)""", buf)
    con.commit()
    print(f"section {n_sec:,}건 → chunk {n_chunk:,}개")
    return 0


if __name__ == "__main__":
    sys.exit(main(reset="--reset" in sys.argv))
