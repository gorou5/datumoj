"""嵌入模型（Embedder）：Ollama（默认 bge-m3）和测试用的确定性 HashEmbedder。"""

from __future__ import annotations

import hashlib
import math
import time
import unicodedata
from collections.abc import Sequence

import httpx

from datumoj.rikolto.core.contracts import EmbeddingError


class HashEmbedder:
    """确定性哈希嵌入：字和相邻两字各哈希到一个维度，再归一化。不联网，测试用。"""

    def __init__(self, dim: int = 64, model: str = "hash-v1") -> None:
        if dim < 1:
            raise ValueError("dim 必须 ≥ 1")
        self.dim = dim
        self.model = model

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]

    def _one(self, text: str) -> list[float]:
        t = unicodedata.normalize("NFKC", text).casefold()
        grams = list(t) + [t[i : i + 2] for i in range(len(t) - 1)]
        v = [0.0] * self.dim
        for g in grams or [""]:
            h = int.from_bytes(hashlib.md5(g.encode()).digest()[:8], "big")
            v[h % self.dim] += 1.0 if (h >> 32) & 1 else -1.0
        norm = math.sqrt(sum(x * x for x in v))
        if norm == 0:  # 符号恰好抵消时仍要给出非零向量
            v[0], norm = 1.0, 1.0
        return [x / norm for x in v]


class OllamaEmbedder:
    """通过 Ollama ``/api/embed`` 嵌入。批量请求；超时或网络错误、5xx 按退避重试，4xx 直接失败。

    ``dim`` 是模型的输出维度（bge-m3 为 1024），返回的向量维度不符会抛 EmbeddingError。
    """

    def __init__(
        self,
        model: str = "bge-m3",
        dim: int = 1024,
        base_url: str = "http://localhost:11434",
        batch_size: int = 32,
        timeout: float = 60.0,
        retries: int = 3,
        backoff: float = 0.5,
        client: httpx.Client | None = None,
    ) -> None:
        if dim < 1 or batch_size < 1 or timeout <= 0 or retries < 0 or backoff < 0:
            raise ValueError("dim、batch_size、timeout 必须为正，retries、backoff 不能为负")
        self.model = model
        self.dim = dim
        self.batch_size = batch_size
        self.retries = retries
        self.backoff = backoff
        self._client = client or httpx.Client(base_url=base_url, timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            out.extend(self._embed_batch(list(texts[i : i + self.batch_size])))
        return out

    def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            if attempt:
                time.sleep(self.backoff * 2 ** (attempt - 1))
            try:
                resp = self._client.post("/api/embed", json={"model": self.model, "input": batch})
            except httpx.TransportError as e:  # 含超时
                last = e
                continue
            if resp.status_code >= 500:
                last = EmbeddingError(f"Ollama 返回 {resp.status_code}")
                continue
            if resp.status_code != 200:
                raise EmbeddingError(f"Ollama 返回 {resp.status_code}：{resp.text[:200]}")
            return self._parse(resp, len(batch))
        raise EmbeddingError(f"Ollama 嵌入失败（已重试 {self.retries} 次）：{last}") from last

    def _parse(self, resp: httpx.Response, n: int) -> list[list[float]]:
        try:
            vectors = resp.json()["embeddings"]
            ok = len(vectors) == n and all(
                len(v) == self.dim and all(math.isfinite(x) for x in v) for v in vectors
            )
        except (ValueError, KeyError, TypeError) as e:
            raise EmbeddingError(f"Ollama 响应格式不对：{e}") from e
        if not ok:
            raise EmbeddingError(f"Ollama 返回的向量数量或维度不符（期望 {n} 条、{self.dim} 维）")
        return [[float(x) for x in v] for v in vectors]
