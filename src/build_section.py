"""정기공시를 목차 단위로 잘라 section 에 담는다.

판단하지 않고 전부 담는다. 무엇을 검색 대상으로 삼을지는 임베딩 단계에서
정한다. 텍스트를 갖고 있으면 나중에 임베딩만 추가할 수 있으나, 안 담으면
그때 문서를 다시 열어야 한다.

두 가지가 1차 시도와 다르다.

    sanitize      원문의 이스케이프 누락을 파싱 전에 바로잡는다
                  안 하면 문서 468건에서 표가 문서 나머지를 삼킨다

    파일별 파싱    이어붙이지 않는다. 사업보고서는 본문·감사보고서·
                  연결감사보고서 세 파일이고, 이어붙이면 루트가 셋이 되어
                  파서가 첫 루트만 읽는다. 210건에서 감사보고서가 통째로
                  빠져 있었다

경위는 `docs/feedback/W6.md`, 결정은 `DECISIONS.md` 2026-08-21 두 항목에 있다.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8")

from corpus import doc_files
from db import connect, create_section_schema
from sanitize import sanitize
from section import parse


def main(reset: bool = False) -> int:
    con = connect()
    if reset:
        # 컬럼이 늘었으므로 DELETE 로는 안 된다. chunk 가 section 을
        # 참조하므로 함께 지운다. 어차피 다시 만들어야 하는 것들이다.
        con.executescript("DROP TABLE IF EXISTS chunk;"
                          "DROP TABLE IF EXISTS section;")
        con.commit()
    create_section_schema(con)

    docs = con.execute("""SELECT doc_id,corp_code,corp_name,rcept_dt,file_path
                          FROM document
                          WHERE doc_group='periodic' AND file_format='xml'
                          ORDER BY corp_name,rcept_dt""").fetchall()
    print(f"대상 {len(docs):,}건", flush=True)
    t0 = time.time()
    n_doc = n_sec = n_skip = n_file = 0
    for i, d in enumerate(docs, 1):
        files = [f for f in doc_files(d["file_path"])
                 if f.suffix.lower() in (".xml", ".html")]
        if not files:
            n_skip += 1
            continue
        secs = []
        for f in files:
            try:
                raw = sanitize(f.read_text(encoding="utf-8", errors="replace"))
                got = parse(raw)
            except Exception as e:
                print(f"   ! {d['corp_name']} {f.name}: {type(e).__name__} {e}")
                continue
            n_file += 1
            for s in got:
                s["src_file"] = f.name
            secs += got
        if not secs:
            n_skip += 1
            continue
        n_doc += 1
        rows = [(d["doc_id"], d["corp_code"], j, s["path"], s["level"],
                 s["aclass"], s["atocid"], s["title"],
                 s["char_len"], s["text_len"], s["table_len"],
                 s["n_table"], s["src_file"], s["text"])
                for j, s in enumerate(secs)]
        con.executemany(
            """INSERT OR IGNORE INTO section
               (doc_id,corp_code,seq,path,level,aclass,atocid,title,
                char_len,text_len,table_len,n_table,src_file,text)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
        n_sec += len(rows)
        if i % 100 == 0:
            con.commit()
            dt = time.time() - t0
            eta = dt / i * (len(docs) - i) / 60
            print(f"   {i:,}/{len(docs):,} · 조각 {n_sec:,} · "
                  f"{dt/60:.1f}분 경과 · 남은 {eta:.0f}분", flush=True)
    con.commit()
    print(f"문서 {n_doc:,}건 · 파일 {n_file:,}개 · 조각 {n_sec:,} · "
          f"건너뜀 {n_skip} · {(time.time()-t0)/60:.1f}분")
    return 0


if __name__ == "__main__":
    sys.exit(main(reset="--reset" in sys.argv))
