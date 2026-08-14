# -*- coding: utf-8 -*-
"""제공 코퍼스 접근 계층.

원문은 읽기 전용이다. 이 모듈은 읽기 함수만 노출하고 쓰기 경로를 두지 않는다.
모든 산출물은 data/ 아래에만 만든다.

경로 처리 주의
    manifest.jsonl 의 file_path 는 NFC(완성형)인데 파일시스템은 NFD(자모 분리)일 수
    있다. 압축을 어떻게 풀었느냐에 따라 달라진다. resolve() 가 양쪽을 모두 시도한다.
"""

from __future__ import annotations

import os
import re
import unicodedata as ud
from functools import lru_cache
from pathlib import Path

import pandas as pd

__all__ = [
    "PROJECT_ROOT", "DATA_DIR", "corpus_root", "resolve",
    "load_universe", "load_manifest", "doc_files", "read_pdf_text",
    "read_raw", "to_text",
]

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PROJECT_ROOT / "assets"
DATA_DIR = PROJECT_ROOT / "data"


# ─────────────────────────────────────────────────────────────
# 코퍼스 위치
# ─────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def corpus_root() -> Path:
    """assets/ 아래에서 manifest.jsonl 이 있는 폴더를 코퍼스 루트로 판별한다.

    경로를 고정하지 않는 이유는 압축을 푸는 방식에 따라 폴더 구조가 달라지기
    때문이다. 현재 배포본은 assets/공시/공시/corpus/ 이지만 이 값에 의존하지 않는다.
    """
    if not ASSETS_DIR.exists():
        raise FileNotFoundError(
            f"assets 폴더가 없습니다: {ASSETS_DIR}\n"
            "주최측 배포 코퍼스를 assets/ 아래에 배치하세요. docs/SETUP.md 참조."
        )
    hits = sorted(ASSETS_DIR.rglob("manifest.jsonl"))
    if not hits:
        raise FileNotFoundError(
            f"assets/ 아래에서 manifest.jsonl 을 찾지 못했습니다: {ASSETS_DIR}"
        )
    if len(hits) > 1:
        raise RuntimeError(
            "manifest.jsonl 이 여러 개입니다. 하나만 남기세요.\n  "
            + "\n  ".join(str(h) for h in hits)
        )
    return hits[0].parent


def resolve(rel_path: str) -> Path:
    """manifest 의 file_path 를 실제 파일시스템 경로로 바꾼다.

    NFD 와 NFC 를 모두 시도한다. 둘 다 실패하면 예외를 던진다.
    """
    root = corpus_root()
    rel = rel_path.replace("/", os.sep)
    for form in ("NFD", "NFC"):
        p = Path(os.path.join(str(root), ud.normalize(form, rel)))
        if p.exists():
            return p
    raise FileNotFoundError(f"코퍼스에서 찾을 수 없습니다: {rel_path}")


# ─────────────────────────────────────────────────────────────
# 인덱스 로딩
# ─────────────────────────────────────────────────────────────

_STR_COLS = {"corp_code": str, "stock_code": str, "rcept_no": str, "rcept_dt": str}


@lru_cache(maxsize=1)
def load_universe() -> pd.DataFrame:
    """기업 마스터 70행. corp_code 와 stock_code 의 선행 0 을 지키기 위해 문자열로 읽는다."""
    return pd.read_csv(corpus_root() / "universe.csv", dtype=_STR_COLS)


@lru_cache(maxsize=1)
def load_manifest() -> pd.DataFrame:
    """문서 메타데이터 4,204행. 한 줄이 문서 하나다."""
    return pd.read_json(corpus_root() / "manifest.jsonl", lines=True, dtype=_STR_COLS)


# ─────────────────────────────────────────────────────────────
# 원문 읽기
# ─────────────────────────────────────────────────────────────

def doc_files(file_path: str) -> list[Path]:
    """문서 폴더 안의 원문 파일 목록. 사업보고서는 본문 + 첨부 감사보고서로 여러 개다."""
    d = resolve(file_path)
    return sorted(p for p in d.glob("*") if p.suffix.lower() in (".xml", ".html", ".pdf"))


def read_raw(file_path: str) -> str:
    """문서의 원문 전체를 이어붙여 반환한다. 태그를 그대로 둔다.

    거래소공시는 확장자가 .xml 이지만 실제로는 HTML 이고, 선언된 charset 은
    euc-kr 이지만 파일 인코딩은 UTF-8 이다. 전부 UTF-8 로 읽는다.
    """
    parts = []
    for f in doc_files(file_path):
        if f.suffix.lower() == ".pdf":
            continue  # PDF 는 태그가 없다. read_pdf_text 로 따로 읽는다
        parts.append(f.read_text(encoding="utf-8", errors="replace"))
    return "".join(parts)


def read_pdf_text(file_path: str, stop: str | None = None, max_pages: int = 40) -> str:
    """대체 수집분의 PDF 본문을 읽는다. PDF 가 없으면 빈 문자열.

    pdf+html 3건은 뷰어 HTML 에 문서 목록만 들어 있고 본문이 없다. 텍스트로 뽑으면
    350~570자뿐이라 정정 헤더도 재무 항목도 나오지 않는다. 내용은 PDF 에만 있다.

    전부 읽지 않는다. KB금융 사업보고서는 1,085쪽이다. stop 문자열이 나오면 멈추고,
    없으면 max_pages 까지만 읽는다. 정정 헤더는 목차 뒤 5쪽 안에 있다.
    """
    pdfs = [f for f in doc_files(file_path) if f.suffix.lower() == ".pdf"]
    if not pdfs:
        return ""
    from pypdf import PdfReader          # 대체 수집분에만 필요하므로 지연 임포트

    out = []
    for pdf in pdfs:
        for i, page in enumerate(PdfReader(pdf).pages):
            out.append(page.extract_text() or "")
            if (stop and stop in out[-1]) or i + 1 >= max_pages:
                break
    return "\n".join(out)


_RE_DROP = re.compile(r"<(STYLE|style|script)[\s\S]*?</\1>")
_RE_BLOCK = re.compile(r"</(TR|tr|P|p|TITLE|SECTION-\d)>")
_RE_CELL = re.compile(r"</(TD|TH|TE|TU|td|th)>")
_RE_TAG = re.compile(r"<[^>]+>")
_RE_PIPES = re.compile(r"[ \t]*\|[ \t]*(\|[ \t]*)+")


def to_text(raw: str, keep_cells: bool = True) -> str:
    """태그를 걷어내 사람이 읽을 수 있는 텍스트로 만든다.

    keep_cells=True 면 표 셀 경계를 ' | ' 로 남긴다. 표에서 값을 뽑을 때
    행과 열 구조가 필요하기 때문이다.
    """
    s = _RE_DROP.sub(" ", raw)
    s = _RE_BLOCK.sub("\n", s)
    s = _RE_CELL.sub(" | " if keep_cells else " ", s)
    s = _RE_TAG.sub("", s)
    s = s.replace("&nbsp;", " ").replace("&nbsp", " ")
    s = _RE_PIPES.sub(" | ", s)
    s = re.sub(r"[ \t]{2,}", " ", s)
    s = re.sub(r"\n\s*\n+", "\n", s)
    return "\n".join(ln.strip(" |").strip() for ln in s.split("\n") if ln.strip(" |").strip())
