"""평가용 API 서버. 심사측이 GET 을 보내는 유일한 창구다.

`docs/BRIEF.md` 의 API 스키마를 그대로 구현한다.

    GET /answer?question_id={id}&question={평가 질의}

    {
      "question_id":       "Q-001",
      "question":          "평가 질의 원문",
      "retrieved_context": "답변 생성에 참고한 검색 문서",
      "think_trace":       "사고 · 추론 · 도구 사용 과정",
      "answer":            "최종 생성 답변"
    }

## 왜 Corpus 를 캐시하나

`answer.py` 를 그냥 부르면 질의마다 `Corpus` 를 새로 만든다. 그 기업의 조각
임베딩을 DB 에서 읽어 메모리에 올리는 일이다. 지금은 삼성전자 830조각에 약
1초라 캐시가 없어도 견딜 만하지만, 어차피 만들어 둔 것을 버릴 이유가 없다.

처음 이 값은 101초였다. 조회가 색인을 안 타서 chunk 17만행을 전부 읽고 있었다.
`retrieval.Corpus` 설명 참조. 캐시로 덮지 말고 원인을 고쳤다.

기업마다 한 번만 만들고 재사용한다. 자주 나올 기업을 미리 올려 두려면 `WARM`
에 적는다. 지금은 비워 둔다. 1초면 굳이 미리 올릴 값어치가 없다.

## 메모리를 다 올리지 않는 이유

서버가 8GB 다. 70개사를 전부 올리면 약 700MB 를 상시 붙든다(70 × 830조각 ×
12KB). 들어는 가지만 다른 곳에서 터질 여지를 만든다. 물어본 기업만 올리고,
너무 많이 쌓이면 오래된 것부터 버린다(`MAX_CORP`).

## think_trace 를 문자열로 바꿔 내보낸다

안에서는 JSON 구조로 다루고 내보낼 때만 사람이 읽을 문자열로 만든다.
과제 스키마의 예시가 문자열이기 때문이다. 구조가 필요하면 `?trace=json` 을
붙이면 원래 형태로 준다. 평가에는 안 쓰이고 우리가 볼 때 쓴다.

## 띄우는 법

    python -m uvicorn src.server:app --host 0.0.0.0 --port 8000

`--host 0.0.0.0` 이 있어야 바깥에서 들어올 수 있다. 빼면 그 컴퓨터 안에서만
열린다. 서버에 올릴 때 이걸 빠뜨리면 "켜 뒀는데 접속이 안 된다" 가 된다.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from collections import OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

import query as Q
from answer import answer as run_answer
from hcx import Chat
from retrieval import Corpus

MAX_CORP = 8        # 메모리에 동시에 둘 기업 수
WARM: list[str] = []  # 기동할 때 미리 올릴 기업. 비워 두면 안 올린다

app = FastAPI(title="공시 Agent", version="0.1",
              description="공시를 근거로 질의에 답한다. GET /answer 하나뿐이다.")

_lock = threading.Lock()
_corpus: OrderedDict[tuple, Corpus] = OrderedDict()
_chat: Chat | None = None
_stat = {"요청": 0, "성공": 0, "실패": 0, "적재": 0}


def get_chat() -> Chat:
    global _chat
    if _chat is None:
        _chat = Chat()
    return _chat


def get_corpus(corps: list[str], subtype: str) -> Corpus:
    """기업별로 한 번만 만들어 재사용한다. 오래된 것부터 버린다."""
    key = (tuple(sorted(corps)), subtype)
    with _lock:
        if key in _corpus:
            _corpus.move_to_end(key)
            return _corpus[key]
    cp = Corpus(list(corps), subtype=subtype)      # 락 밖에서 만든다. 오래 걸린다
    with _lock:
        _corpus[key] = cp
        _corpus.move_to_end(key)
        _stat["적재"] += 1
        while len(_corpus) > MAX_CORP:
            _corpus.popitem(last=False)
    return cp


def as_text(trace: list[dict]) -> str:
    """실행 기록을 사람이 읽을 한 덩어리 글로 바꾼다."""
    out = []
    for t in trace:
        name = t.get("단계", "")
        rest = {k: v for k, v in t.items() if k != "단계"}
        out.append(f"{name} :: " + json.dumps(rest, ensure_ascii=False))
    return "\n".join(out)


@app.on_event("startup")
def warm() -> None:
    print(f"기동. 미리 올릴 기업 {len(WARM)}곳", flush=True)
    for c in WARM:
        t0 = time.time()
        get_corpus([c], "annual")
        print(f"   {c} 적재 {time.time() - t0:.1f}초", flush=True)


@app.get("/health")
def health() -> dict:
    """살아 있는지 보는 창구. 자동 재시작 감시에도 쓴다."""
    return {"ok": True, "적재된 기업": len(_corpus), **_stat}


@app.get("/answer")
def get_answer(
    question: str = Query(..., description="평가 질의"),
    question_id: str = Query("", description="질의 식별자"),
    trace: str = Query("text", description="think_trace 형식. text 또는 json"),
) -> JSONResponse:
    _stat["요청"] += 1
    t0 = time.time()
    try:
        p = Q.parse(question)
        cp = get_corpus(p.corps, p.subtype or "annual") if p.corps else None
        r = run_answer(question, question_id, cp=cp, chat=get_chat())
        _stat["성공"] += 1
    except Exception as e:                          # 어떤 오류든 4필드는 채워 보낸다
        _stat["실패"] += 1
        r = {"question_id": question_id, "question": question,
             "retrieved_context": "",
             "think_trace": [{"단계": "오류", "종류": type(e).__name__, "내용": str(e)[:300]}],
             "answer": "처리 중 오류가 발생했다."}

    if trace != "json":
        r["think_trace"] = as_text(r["think_trace"])
    print(f"[{question_id or '-'}] {time.time() - t0:.1f}초  {question[:40]}",
          flush=True)
    return JSONResponse(content=r, media_type="application/json; charset=utf-8")
