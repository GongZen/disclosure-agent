"""OpenAI 임베딩 호출.

CLOVA 와 견주기 위해 만들었다. 두 가지가 다르다.

    묶음 호출    CLOVA 는 한 번에 텍스트 하나만 받는데 여기는 여러 개를 받는다
                 호출 횟수가 줄어 rate limit 부담이 작다
    차원         3,072. CLOVA v2 는 1,024

임베딩 모델은 대회 제약 대상이 아니다. 생성 모델만 HyperCLOVA X 로 제한된다.
"""
from __future__ import annotations

import json
import math
import urllib.error
import urllib.request

from clova import load_env

URL = "https://api.openai.com/v1/embeddings"
MODEL = "text-embedding-3-large"
DIM = 3072


class OpenAIEmbedder:
    def __init__(self, env: dict | None = None, model: str = MODEL,
                 timeout: int = 60):
        self.env = env or load_env()
        self.key = self.env.get("OPENAI_API_KEY", "")
        self.model = model
        self.timeout = timeout
        self.n_call = self.n_429 = 0
        if not self.key:
            raise RuntimeError(".env 에 OPENAI_API_KEY 가 필요하다")

    def embed_many(self, texts: list[str], retry: int = 3
                   ) -> tuple[list[list[float]] | None, str]:
        """여러 텍스트를 한 번에. 순서대로 벡터 목록을 낸다."""
        body = json.dumps({"model": self.model, "input": texts}).encode("utf-8")
        last = ""
        for i in range(retry):
            req = urllib.request.Request(URL, data=body, method="POST")
            req.add_header("Authorization", f"Bearer {self.key}")
            req.add_header("Content-Type", "application/json")
            try:
                self.n_call += 1
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    d = json.loads(r.read().decode("utf-8"))
                items = sorted(d.get("data", []), key=lambda x: x.get("index", 0))
                return [x["embedding"] for x in items], "ok"
            except urllib.error.HTTPError as e:
                raw = e.read().decode("utf-8", "replace")[:200]
                last = f"HTTP {e.code}: {raw}"
                if e.code == 429:
                    self.n_429 += 1
                import time
                time.sleep(min(20, 2 ** i))
            except Exception as e:
                last = f"{type(e).__name__}: {e}"
                import time
                time.sleep(2 ** i)
        return None, last

    def embed(self, text: str) -> tuple[list[float] | None, str]:
        v, st = self.embed_many([text])
        return (v[0] if v else None), st


def normalize(vec: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in vec))
    return [x / n for x in vec] if n else vec
