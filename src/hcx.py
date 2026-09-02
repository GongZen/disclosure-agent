"""HyperCLOVA X 생성 모델을 부른다. 답변을 만드는 유일한 경로다.

`clova.py` 의 `Embedder` 와 짝이다. 둘 다 CLOVA Studio 를 부르지만 하는 일이
완전히 다르다.

    Embedder   글을 숫자 1,024개로 바꾼다.  검색에 쓴다.  말을 못 만든다
    Chat       질문과 근거를 주면 한국어 답변을 쓴다.  이쪽이 생성 모델이다

대회 규칙상 생성 모델은 HyperCLOVA X 만 쓸 수 있다. 이 파일이 그 통로다.

## 주소가 임베딩과 다르다

    임베딩   https://clovastudio.stream.ntruss.com/v1/api-tools/embedding/v2
    생성     https://clovastudio.stream.ntruss.com/v3/chat-completions/HCX-005

인증 방식은 같다. `Authorization: Bearer <key>` 다. 그래서 `.env` 의
`CLOVA_API_KEY` 를 그대로 쓴다. 다른 키가 필요하면 `CLOVA_CHAT_KEY` 를 두면
그쪽을 먼저 본다.

## 스트리밍을 안 쓴다

콘솔이 주는 예시 코드는 `Accept: text/event-stream` 으로 답을 조금씩 받는다.
사람이 화면에서 글자가 흘러나오는 것을 볼 때 쓰는 방식이다.

우리는 평가자가 GET 을 한 번 보내고 완성된 JSON 을 받아 가므로 중간 과정을
보여 줄 곳이 없다. 그래서 `Accept: application/json` 으로 한 번에 받는다.
서버가 그래도 스트리밍으로 주면 아래 `_parse` 가 이어 붙인다.

## 429 를 임베딩과 같은 방식으로 다룬다

호출 제한에 걸리면 잠깐 쉬었다 다시 던진다. 임베딩에서 확인된 방식이라
그대로 가져왔다. 다만 생성은 임베딩만큼 많이 부르지 않으므로 간격 자동조절은
안 넣었다. 질의 하나에 한 번 부른다.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from clova import load_env

__all__ = ["Chat", "DEFAULT_HOST", "DEFAULT_MODEL"]

DEFAULT_HOST = "https://clovastudio.stream.ntruss.com"
DEFAULT_MODEL = "HCX-005"


class Chat:
    """생성 모델 한 번 호출. (본문, 상태) 를 낸다."""

    def __init__(self, env: dict | None = None, model: str | None = None,
                 timeout: int = 60):
        self.env = env or load_env()
        self.key = self.env.get("CLOVA_CHAT_KEY") or self.env.get("CLOVA_API_KEY", "")
        host = self.env.get("CLOVA_CHAT_HOST") or DEFAULT_HOST
        self.model = model or self.env.get("CLOVA_CHAT_MODEL") or DEFAULT_MODEL
        self.url = f"{host.rstrip('/')}/v3/chat-completions/{self.model}"
        self.timeout = timeout
        self.n_call = self.n_429 = 0
        if not self.key:
            raise RuntimeError(".env 에 CLOVA_API_KEY 가 필요하다")

    def _body(self, system: str, user: str, max_tokens: int,
              temperature: float) -> dict:
        """콘솔 예시와 같은 형태로 만든다. content 가 리스트인 것이 v3 형식이다."""
        def msg(role: str, text: str) -> dict:
            return {"role": role, "content": [{"type": "text", "text": text}]}

        out = []
        if system:
            out.append(msg("system", system))
        out.append(msg("user", user))
        return {
            "messages": out,
            "topP": 0.8,
            "topK": 0,
            "maxTokens": max_tokens,
            "temperature": temperature,
            "repetitionPenalty": 1.1,
            "stop": [],
            "includeAiFilters": True,
        }

    @staticmethod
    def _parse(raw: str) -> str:
        """JSON 한 덩어리로 오면 그대로, 스트리밍으로 오면 이어 붙인다."""
        raw = raw.strip()
        if raw.startswith("{"):
            d = json.loads(raw)
            m = (d.get("result") or {}).get("message") or d.get("message") or {}
            c = m.get("content")
            if isinstance(c, list):                      # v3 는 리스트로 온다
                return "".join(x.get("text", "") for x in c)
            return c or ""
        # text/event-stream. data: 줄만 모은다
        parts = []
        for line in raw.splitlines():
            if not line.startswith("data:"):
                continue
            chunk = line[5:].strip()
            if not chunk or chunk == "[DONE]":
                continue
            try:
                d = json.loads(chunk)
            except json.JSONDecodeError:
                continue
            m = d.get("message") or {}
            c = m.get("content")
            if isinstance(c, list):
                parts.append("".join(x.get("text", "") for x in c))
            elif isinstance(c, str):
                parts.append(c)
        return "".join(parts)

    def ask(self, user: str, system: str = "", max_tokens: int = 1024,
            temperature: float = 0.2, retry: int = 4) -> tuple[str | None, str]:
        """(본문, 상태). 실패하면 본문이 None 이고 상태에 이유가 담긴다."""
        body = json.dumps(self._body(system, user, max_tokens, temperature),
                          ensure_ascii=False).encode("utf-8")
        last = ""
        for i in range(retry):
            req = urllib.request.Request(self.url, data=body, method="POST")
            req.add_header("Authorization", f"Bearer {self.key}")
            req.add_header("X-NCP-CLOVASTUDIO-REQUEST-ID", uuid.uuid4().hex)
            req.add_header("Content-Type", "application/json; charset=utf-8")
            req.add_header("Accept", "application/json")
            try:
                self.n_call += 1
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    text = self._parse(r.read().decode("utf-8"))
                if text:
                    return text, "ok"
                return None, "빈 응답"
            except urllib.error.HTTPError as e:
                raw = e.read().decode("utf-8", "replace")[:200]
                last = f"HTTP {e.code}: {raw}"
                if e.code == 429:
                    self.n_429 += 1
                    wait = e.headers.get("Retry-After")
                    time.sleep(float(wait) if wait else min(8, 2 ** i))
                    continue
                if e.code >= 500:
                    time.sleep(min(20, 2 ** i))
                    continue
                return None, last
            except Exception as e:
                last = f"{type(e).__name__}: {e}"
                time.sleep(min(10, 2 ** i))
        return None, last


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    ch = Chat()
    print(f"주소  {ch.url}")
    q = " ".join(sys.argv[1:]) or "한 문장으로 자기소개를 해줘."
    t0 = time.time()
    text, st = ch.ask(q, system="한국어로 간결하게 답한다.", max_tokens=200)
    print(f"질문  {q}")
    print(f"상태  {st} · {time.time() - t0:.1f}초 · 429 {ch.n_429}회")
    print(f"답변  {text}")
