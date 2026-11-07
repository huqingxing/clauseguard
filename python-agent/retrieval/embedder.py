"""文本向量化：三种后端档位共用 OpenAI 兼容 /embeddings 协议。

为什么按「32 组织 / 10 发送」两层切批：任务要求批量 32 条一次，但百炼兼容端点
实测 batch>10 直接返回 400（"batch size ... should not be larger than 10"）。把
服务端上限做成配置而非硬编码，换供应商时只改配置，32 条的语义仍保留在组织层。

为什么维度必须校验：E3 是 512 维，若拿 512 维向量写进 vector(1024) 的列，pgvector
会在入库时才报错，排查链很长。前置 fail-fast 把问题钉在向量化这一步。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import httpx

from retrieval.config import RetrievalSettings

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class EmbeddingUsage:
    """一次向量化任务的资源账本；token 数来自服务端 usage，不自行估算。"""

    total_tokens: int = 0
    requests: int = 0
    retries: int = 0
    vectors: int = 0

    def add(self, other: EmbeddingUsage) -> None:
        self.total_tokens += other.total_tokens
        self.requests += other.requests
        self.retries += other.retries
        self.vectors += other.vectors


@dataclass(slots=True)
class EmbeddingResult:
    vectors: list[list[float]]
    usage: EmbeddingUsage = field(default_factory=EmbeddingUsage)


def iter_batches(
    texts: Sequence[str], batch_size: int, provider_limit: int
) -> Iterator[tuple[int, list[str]]]:
    """先按配置批大小分组，再按服务端上限切子批；产出 (起始下标, 子批文本)。

    为什么要带起始下标：并发返回后必须按顺序回填，否则 chunk 与向量会错位——
    这是向量入库最隐蔽也最致命的错误（索引看起来正常，语义全错）。
    """
    if batch_size <= 0 or provider_limit <= 0:
        raise ValueError("batch_size 与 provider_limit 必须为正")
    limit = min(batch_size, provider_limit)
    for group_start in range(0, len(texts), batch_size):
        group = texts[group_start : group_start + batch_size]
        for offset in range(0, len(group), limit):
            yield group_start + offset, list(group[offset : offset + limit])


class Embedder:
    def __init__(
        self,
        settings: RetrievalSettings,
        *,
        client: httpx.Client | None = None,
        max_workers: int = 6,
    ) -> None:
        self.settings = settings
        self.model, self.dim, self.base_url = settings.resolved_embedding()
        self._own_client = client is None
        self.client = client or httpx.Client(timeout=settings.embedding_timeout_s)
        self.max_workers = max_workers

    def close(self) -> None:
        if self._own_client:
            self.client.close()

    def __enter__(self) -> Embedder:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _post(self, texts: Sequence[str]) -> tuple[list[list[float]], int, int]:
        """单批请求，失败重试 embedding_max_retries 次；返回 (向量, token, 重试次数)。"""
        url = f"{self.base_url}/embeddings"
        headers = {"Content-Type": "application/json"}
        if self.settings.embedding_api_key:
            headers["Authorization"] = f"Bearer {self.settings.embedding_api_key}"
        payload: dict[str, object] = {"model": self.model, "input": list(texts)}
        # dimension 仅百炼 text-embedding-v3 支持；本地 bge 端点不认这个参数。
        if self.model == "text-embedding-v3":
            payload["dimension"] = self.dim

        retries = 0
        last_error: Exception | None = None
        for attempt in range(self.settings.embedding_max_retries + 1):
            try:
                response = self.client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                body = response.json()
                data = body.get("data")
                if not isinstance(data, list) or len(data) != len(texts):
                    got = len(data) if isinstance(data, list) else "?"
                    raise ValueError(f"返回向量数 {got} != 输入 {len(texts)}")
                ordered = sorted(data, key=lambda item: item.get("index", 0))
                vectors = [list(map(float, item["embedding"])) for item in ordered]
                for vector in vectors:
                    if len(vector) != self.dim:
                        raise ValueError(
                            f"模型 {self.model} 返回 {len(vector)} 维，配置要求 {self.dim} 维"
                        )
                tokens = int((body.get("usage") or {}).get("total_tokens") or 0)
                return vectors, tokens, retries
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                last_error = exc
                if attempt < self.settings.embedding_max_retries:
                    retries += 1
                    time.sleep(0.5 * (2**attempt))
                    logger.warning("embedding 批次重试 %d/%d: %s", attempt + 1,
                                   self.settings.embedding_max_retries, exc)
        raise RuntimeError(f"embedding 批次失败（重试 {retries} 次）: {last_error}") from last_error

    def embed(self, texts: Sequence[str]) -> EmbeddingResult:
        """并发向量化全部文本；结果按输入顺序回填，token 消耗累计返回。"""
        if not texts:
            return EmbeddingResult(vectors=[])
        batches = list(iter_batches(texts, self.settings.embedding_batch_size,
                                    self.settings.provider_batch_limit))
        vectors: list[list[float] | None] = [None] * len(texts)
        usage = EmbeddingUsage()

        def run(batch: tuple[int, list[str]]) -> tuple[int, list[list[float]], int, int]:
            start, chunk = batch
            result, tokens, retries = self._post(chunk)
            return start, result, tokens, retries

        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            for start, result, tokens, retries in pool.map(run, batches):
                vectors[start : start + len(result)] = result
                usage.total_tokens += tokens
                usage.requests += 1
                usage.retries += retries
                usage.vectors += len(result)

        if any(vector is None for vector in vectors):
            raise RuntimeError("向量化结果未填满，存在漏批")
        resolved = [vector for vector in vectors if vector is not None]
        return EmbeddingResult(vectors=resolved, usage=usage)
