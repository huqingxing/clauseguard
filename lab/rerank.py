"""阶段 5｜reranker：把 top-20 候选重排成 top-5，并把延迟与成本记成可复现的账本。

## 为什么只选 DashScope gte-rerank-v2 一种

任务书允许 bge-reranker-v2-m3 或 gte-rerank 二选一。bge 需要
sentence-transformers / torch 这类框架级权重依赖（铁律禁止），本机
127.0.0.1:9997 也没有推理服务；gte-rerank-v2 走原生 HTTP，复用项目已有的
httpx 即可，零新依赖。实测 `gte-rerank` 返回 403、
`compatible-mode/v1/rerank` 返回 404，只有 `gte-rerank-v2` + 原生端点可用。

## 为什么结果必须落盘缓存

真实 API 每次调用既花钱又受网络抖动影响。阶段 4 的「同 label 重跑逐位一致」
在阶段 5 同样是硬要求：rerank 分数只要有一次抖动，top-5 就可能换序，
「rerank 值不值」这个结论就不可复现。缓存键含 query、候选 chunk_id 与正文、
模型名与候选条数，语料或模型一变就自动失效，不会静默复用旧排序。
延迟统计只计首次真实调用，缓存命中的亚毫秒耗时不混进「延迟增加多少」。

## 为什么失败不静默降级

项目一贯禁止「读不到 key 就降级 mock」。rerank 不可用时抛 RerankUnavailable，
由 run_rerank.py 记为失败并保留，绝不拿离线代理的结果冒充真实模型收益。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx

from retrieval.service import Hit

RERANK_MODEL = "gte-rerank-v2"
RERANK_ENDPOINT = "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"
DEFAULT_CANDIDATES = 20
DEFAULT_TOP_N = 5

# 实测 80 条查询的 top-20 候选平均 2403 字符，远低于端点限制；留一个兜底上限，
# 防止语料换成超长条款后一次请求把整轮实验打挂。截断本身要计数并写进报告。
MAX_DOC_CHARS = 1024


class RerankUnavailable(RuntimeError):
    """真实 rerank 不可用；调用方必须记为失败组合，不能回退到假结果。"""


@dataclass(slots=True)
class RerankUsage:
    """整轮实验累计的资源账本。tokens 只取 API 真实回传值，不做估算。

    cost_cny 恒为 0：gte-rerank-v2 的单价需要以控制台实时报价为准，把它硬编码
    进代码会让「成本」随调价失真，也等于用一个没验证过的数字冒充真实支出。
    报告用 tokens / requests / 延迟作为可比成本口径。
    """

    source: str = "dashscope-gte-rerank-v2"
    model: str = RERANK_MODEL
    calls: int = 0
    live_calls: int = 0
    cache_hits: int = 0
    requests: int = 0
    retries: int = 0
    tokens: int = 0
    doc_chars: int = 0
    truncated_docs: int = 0
    live_latency_ms_total: float = 0.0
    cost_cny: float = 0.0
    note: str = "tokens/requests 为 API 真实回传；单价未验证，不填估算费用"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RerankResult:
    """top5 是对外契约（任务书要求「输出重排后的 top-5」）。

    ranked 保留完整 top-N 有序列表：mrr@10 / ndcg@10 需要前 10 条才有区分度，
    若只交出 5 条，@10 指标会退化成 @5，on/off 的排序质量差异将被截断抹平。
    """

    top5: tuple[Hit, ...]
    ranked: tuple[Hit, ...]
    scores: tuple[float, ...]
    latency_ms: float
    cache_hit: bool
    tokens: int


class Reranker(Protocol):
    usage: RerankUsage

    def rerank(
        self, query: str, candidates: Sequence[Hit], *, top_n: int = DEFAULT_TOP_N
    ) -> RerankResult: ...


class RerankCache:
    """按「query + 候选内容 + 模型」指纹缓存重排下标。

    为什么存下标而不是 chunk_id 列表：下标能直接映射回本次 candidates，
    chunk_id 还要再查一次表；指纹里已经含 chunk_id 与正文，串数据风险由
    sha256 挡住，存下标不会引入新的歧义。
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def fingerprint(query: str, candidates: Sequence[Hit], model: str, top_n: int) -> str:
        digest = hashlib.sha256()
        digest.update(model.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(str(top_n).encode("utf-8"))
        digest.update(b"\x00")
        digest.update(query.encode("utf-8"))
        for hit in candidates:
            digest.update(b"\x00")
            digest.update(hit.chunk_id.encode("utf-8"))
            digest.update(b"\x00")
            digest.update(hit.text.encode("utf-8"))
        return digest.hexdigest()[:24]

    def path_for(self, fingerprint: str) -> Path:
        return self.root / f"rerank__{fingerprint}.json"

    def load(self, path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return payload if isinstance(payload, dict) and "order" in payload else None

    def save(self, path: Path, payload: dict[str, Any]) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _bigrams(text: str) -> set[str]:
    cleaned = "".join(text.split())
    return {cleaned[i : i + 2] for i in range(len(cleaned) - 1)}


class DeterministicLexicalRerank:
    """离线确定性代理：按 query 与候选的字符二元组覆盖率重排。

    用途仅限「无密钥环境跑通链路」和单测。它不是 cross-encoder，也**不能**
    用来回答「rerank 值不值」——覆盖率与阶段 3 标注问法同源，会系统性高估收益。
    报告在 provider=deterministic 时必须原样声明这一点。
    """

    def __init__(self) -> None:
        self.usage = RerankUsage(
            source="deterministic-lexical",
            model="char-bigram-coverage",
            note="离线代理，不是真实 rerank 模型；结论不可用于真实收益判断",
        )

    def rerank(
        self, query: str, candidates: Sequence[Hit], *, top_n: int = DEFAULT_TOP_N
    ) -> RerankResult:
        started = time.perf_counter()
        grams = _bigrams(query)
        scored = [
            (
                len(grams & _bigrams(hit.text)) / len(grams) if grams else 0.0,
                hit.chunk_id,
                index,
            )
            for index, hit in enumerate(candidates)
        ]
        # 覆盖率相同时按 chunk_id、再按原下标稳定兜底；浮点分数不能当唯一排序键，
        # 否则同分候选的次序依赖 Python 解释器实现，重跑结论会漂。
        scored.sort(key=lambda item: (-item[0], item[1], item[2]))
        ranked = tuple(candidates[item[2]] for item in scored)
        scores = tuple(item[0] for item in scored)
        self.usage.calls += 1
        self.usage.live_calls += 1
        self.usage.doc_chars += sum(len(hit.text) for hit in candidates)
        elapsed = (time.perf_counter() - started) * 1000
        self.usage.live_latency_ms_total += elapsed
        return RerankResult(
            top5=ranked[:top_n],
            ranked=ranked,
            scores=scores,
            latency_ms=elapsed,
            cache_hit=False,
            tokens=0,
        )


class DashScopeGteRerank:
    """gte-rerank-v2 原生 HTTP 客户端；只依赖项目已有的 httpx。"""

    def __init__(
        self,
        *,
        api_key: str = "",
        model: str = RERANK_MODEL,
        endpoint: str = RERANK_ENDPOINT,
        max_retries: int = 2,
        timeout: float = 60.0,
        max_doc_chars: int = MAX_DOC_CHARS,
        cache: RerankCache | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("CLAUSEGUARD_AGENT_LLM_API_KEY", "")
        if not self.api_key:
            raise RerankUnavailable("缺少 CLAUSEGUARD_AGENT_LLM_API_KEY，不静默降级")
        self.model = model
        self.endpoint = endpoint
        self.max_retries = max_retries
        self.timeout = timeout
        self.max_doc_chars = max_doc_chars
        self.cache = cache
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=timeout)
        self.usage = RerankUsage(model=model)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        last: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self.usage.requests += 1
            try:
                response = self._client.post(self.endpoint, headers=headers, json=payload)
                if response.status_code == 200:
                    body = response.json()
                    if not isinstance(body, dict) or "output" not in body:
                        raise ValueError(f"响应缺少 output 字段: {str(body)[:200]}")
                    return body
                # 4xx 是参数/授权问题，重试只会重复扣费并放大故障，直接退出。
                # 实测 gte-rerank 无授权就是 403，这里必须一次失败而不是三次。
                if 400 <= response.status_code < 500:
                    last = RerankUnavailable(f"HTTP {response.status_code}: {response.text[:200]}")
                    break
                last = RerankUnavailable(f"HTTP {response.status_code}: {response.text[:200]}")
            except (httpx.HTTPError, ValueError, RerankUnavailable) as exc:
                last = exc if isinstance(exc, RerankUnavailable) else RerankUnavailable(str(exc))
            if attempt < self.max_retries:
                self.usage.retries += 1
                time.sleep(0.5 * (attempt + 1))
        raise RerankUnavailable(f"rerank 重试 {self.max_retries} 次仍失败: {last}")

    def rerank(
        self, query: str, candidates: Sequence[Hit], *, top_n: int = DEFAULT_TOP_N
    ) -> RerankResult:
        if not candidates:
            self.usage.calls += 1
            return RerankResult((), (), (), 0.0, False, 0)
        started = time.perf_counter()
        fingerprint = RerankCache.fingerprint(query, candidates, self.model, top_n)
        path = self.cache.path_for(fingerprint) if self.cache else None
        cached = self.cache.load(path) if (self.cache and path) else None
        if cached is not None:
            order = [int(index) for index in cached["order"]]
            ranked = tuple(candidates[index] for index in order if 0 <= index < len(candidates))
            self.usage.calls += 1
            self.usage.cache_hits += 1
            return RerankResult(
                top5=ranked[:top_n],
                ranked=ranked,
                scores=tuple(float(score) for score in cached.get("scores", [])),
                latency_ms=(time.perf_counter() - started) * 1000,
                cache_hit=True,
                tokens=0,  # 缓存命中不重复计费；累计账本也不加 tokens
            )

        docs = [hit.text[: self.max_doc_chars] for hit in candidates]
        truncated = sum(1 for hit in candidates if len(hit.text) > self.max_doc_chars)
        payload = {
            "model": self.model,
            "input": {"query": query, "documents": docs},
            # 要完整候选池的排序才能算 @10 指标；top_n 只截对外输出的 top-5。
            "parameters": {"return_documents": False, "top_n": len(candidates)},
        }
        body = self._post(payload)
        results = body["output"].get("results", [])
        if not isinstance(results, list) or not results:
            raise RerankUnavailable(f"rerank 返回空 results: {str(body)[:200]}")
        parsed = [
            (int(item["index"]), float(item["relevance_score"]))
            for item in results
            if isinstance(item, dict) and "index" in item and "relevance_score" in item
        ]
        # API 只保证返回 top_n 条；把未被返回的候选按原融合次序补在末尾，
        # 保证 ranked 长度恒等于候选池，@10 的分母在 on/off 两侧才一致。
        seen = {index for index, _ in parsed}
        # -1.0 而非 -inf：gte-rerank 分数恒 ≥ 0，-1.0 保证补齐项排在末尾；
        # 用 -inf 会被 json.dumps 写成非标准的 -Infinity，破坏缓存可移植性。
        parsed.extend((index, -1.0) for index in range(len(candidates)) if index not in seen)
        parsed.sort(key=lambda item: (-item[1], item[0]))
        ranked = tuple(candidates[index] for index, _ in parsed)
        scores = tuple(score for _, score in parsed)
        elapsed = (time.perf_counter() - started) * 1000

        tokens = int(body.get("usage", {}).get("total_tokens", 0))
        self.usage.calls += 1
        self.usage.live_calls += 1
        self.usage.tokens += tokens
        self.usage.doc_chars += sum(len(doc) for doc in docs)
        self.usage.truncated_docs += truncated
        self.usage.live_latency_ms_total += elapsed
        if path is not None and self.cache is not None:
            self.cache.save(
                path,
                {
                    "order": [index for index, _ in parsed],
                    "scores": list(scores),
                    "tokens": tokens,
                    "model": self.model,
                    "query": query,
                    "candidate_ids": [hit.chunk_id for hit in candidates],
                    "elapsed_ms": elapsed,
                },
            )
        return RerankResult(
            top5=ranked[:top_n],
            ranked=ranked,
            scores=scores,
            latency_ms=elapsed,
            cache_hit=False,
            tokens=tokens,
        )


def make_reranker(mode: str, *, cache_dir: Path | None = None, api_key: str = "") -> Reranker:
    if mode == "deterministic":
        return DeterministicLexicalRerank()
    if mode == "live":
        return DashScopeGteRerank(
            api_key=api_key,
            cache=RerankCache(cache_dir) if cache_dir else None,
        )
    raise ValueError(f"未知 reranker 模式: {mode}（可选 live / deterministic）")
