"""阶段 2 检索层单测：全部使用内存假实现 / MockTransport，不连真库、不读本机 .env。

为什么坚持不读开发者 .env：CI 与本地行为必须一致（阶段 15 已吃过 CWD 决定
配置来源的亏），测试里用 _env_file=None + 显式构造配置把外部输入彻底切断。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from retrieval import api
from retrieval.bm25 import BM25Index, load_stopwords, tokenize
from retrieval.config import RetrievalSettings
from retrieval.embedder import Embedder, iter_batches
from retrieval.fusion import fuse
from retrieval.service import RetrievalService
from retrieval.store import ChunkRow, ScoredRow


def make_settings(**overrides: Any) -> RetrievalSettings:
    """显式构造；_env_file=None 断开仓库根 .env，避免测试依赖开发者密钥。"""
    defaults: dict[str, Any] = {
        "_env_file": None,
        "pg_dsn": "postgresql://unused",
        "embedding_backend": "E1",
        "embedding_model": "mock-embed",
        "embedding_dim": 4,
        "embedding_base_url": "http://test.invalid/v1",
        "embedding_api_key": "test-key-not-a-real-secret",
        "embedding_batch_size": 32,
        "embedding_max_retries": 2,
        "provider_batch_limit": 10,
        "fusion_method": "rrf",
        "rrf_k": 60,
        "dense_weight": 0.6,
        "sparse_weight": 0.4,
        "candidate_k": 10,
        "default_k": 3,
        "index_strategy": "S1_whole",
    }
    defaults.update(overrides)
    return RetrievalSettings(**defaults)


def make_rows() -> list[ChunkRow]:
    return [
        ChunkRow("c1", "民法典", "第五百八十五条", "第三编 合同",
                 "当事人可以约定违约金 违约金过分高于损失可以适当减少", "S1_whole"),
        ChunkRow("c2", "公司法", "第二十三条", "第二章 公司登记",
                 "股东应当按期足额缴纳公司章程规定的认缴出资额", "S1_whole"),
        ChunkRow("c3", "民法典", "第五百零九条", "第三编 合同",
                 "当事人应当按照约定全面履行自己的义务遵循诚信原则", "S1_whole"),
    ]


class FakeStore:
    """内存替身：只实现 service 用到的协议，便于验证编排而非 SQL。"""

    def __init__(self, rows: Sequence[ChunkRow] | None = None) -> None:
        self.rows = list(rows or make_rows())
        self.dense_calls: list[tuple[int, str | None]] = []

    def dense_search(self, _vec: Sequence[float], k: int, strategy: str | None) -> list[ScoredRow]:
        self.dense_calls.append((k, strategy))
        return [
            ScoredRow(row=row, dense_score=score)
            for row, score in zip(self.rows, [0.95, 0.70, 0.40], strict=False)
        ][:k]

    def load_all(self, _strategy: str | None = None) -> list[ChunkRow]:
        return list(self.rows)

    def fetch_by_ids(self, ids: Sequence[str]) -> dict[str, ChunkRow]:
        return {row.chunk_id: row for row in self.rows if row.chunk_id in ids}


class FakeEmbedder:
    def __init__(self, dim: int = 4) -> None:
        self.dim = dim
        self.calls = 0

    def embed(self, texts: Sequence[str]) -> Any:
        self.calls += 1
        from retrieval.embedder import EmbeddingResult, EmbeddingUsage

        vectors = [[0.1] * self.dim for _ in texts]
        usage = EmbeddingUsage(total_tokens=sum(len(t) for t in texts),
                               requests=1, vectors=len(texts))
        return EmbeddingResult(vectors=vectors, usage=usage)


# ---- 分词 / 切批 -----------------------------------------------------------

def test_tokenize_filters_stopwords_single_chars_and_digits() -> None:
    stops = load_stopwords()
    assert "应当" not in stops and "不得" not in stops  # 法律情态词必须保留
    tokens = tokenize("  当事人  应当 按照 约定 履行 123 的 义务，不得 违反！", stops)
    assert "的" not in tokens and "123" not in tokens
    assert "应当" in tokens and "不得" in tokens
    assert all(len(token) >= 2 for token in tokens)
    assert all(not token.isdigit() for token in tokens)


def test_bm25_index_ranks_relevant_chunk_first() -> None:
    index = BM25Index(make_rows())
    assert len(index) == 3
    hits = index.search("违约金 过分高于损失", k=3)
    assert hits and hits[0].chunk_id == "c1"
    assert all(hit.sparse_score > 0 for hit in hits)
    assert index.search("完全不相关的量子纠缠", k=3) == []


def test_iter_batches_splits_32_by_provider_limit_and_keeps_offsets() -> None:
    texts = [str(i) for i in range(75)]
    batches = list(iter_batches(texts, batch_size=32, provider_limit=10))
    assert [start for start, _ in batches] == [0, 10, 20, 30, 32, 42, 52, 62, 64, 74]
    assert sum(len(chunk) for _, chunk in batches) == 75
    assert all(texts[start:start + len(chunk)] == chunk for start, chunk in batches)
    with pytest.raises(ValueError, match="必须为正"):
        list(iter_batches(texts, 0, 10))


# ---- embedder：重试 / token / 维度 -----------------------------------------

def _mock_client(handler: Any) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_embedder_batches_retries_and_counts_tokens() -> None:
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        payload = json.loads(request.content)
        inputs = payload["input"]
        # 第一个子批首两次故意 500，验证重试 2 次后成功且不影响其他批。
        if payload.get("_fail") or (inputs and inputs[0] == "t0" and attempts["n"] <= 2):
            return httpx.Response(500, json={"error": "transient"})
        data = [
            {"index": index, "embedding": [0.1, 0.2, 0.3, 0.4]}
            for index, _ in enumerate(inputs)
        ]
        return httpx.Response(200, json={"data": data, "usage": {"total_tokens": 7}})

    settings = make_settings(embedding_max_retries=2)
    texts = [f"t{i}" for i in range(35)]
    with Embedder(settings, client=_mock_client(handler), max_workers=1) as embedder:
        result = embedder.embed(texts)
    assert len(result.vectors) == 35
    assert all(len(vector) == 4 for vector in result.vectors)
    # 35 条 / 批 32 / 子批 10 → 5 个子批；其中 1 个重试 2 次 → 7 次请求
    assert result.usage.requests == 5
    assert result.usage.retries == 2
    assert result.usage.total_tokens == 35
    assert result.usage.vectors == 35


def test_embedder_fails_fast_on_dimension_mismatch() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        count = len(json.loads(request.content)["input"])
        return httpx.Response(200, json={
            "data": [{"index": i, "embedding": [0.1, 0.2]} for i in range(count)],
            "usage": {"total_tokens": 1},
        })

    settings = make_settings(embedding_max_retries=0)
    with Embedder(settings, client=_mock_client(handler), max_workers=1) as embedder:
        with pytest.raises(RuntimeError, match="2 维，配置要求 4 维"):
            embedder.embed(["一条法条"])


def test_embedder_empty_input_does_no_request() -> None:
    settings = make_settings()
    with Embedder(settings, client=_mock_client(lambda _: pytest.fail("不应发请求")),
                  max_workers=1) as embedder:
        result = embedder.embed([])
    assert result.vectors == []
    assert result.usage.total_tokens == 0


# ---- service：dense-only / bm25-only / hybrid ------------------------------

def test_service_three_modes_and_hit_shape() -> None:
    settings = make_settings(fusion_method="rrf", candidate_k=3, default_k=3)
    service = RetrievalService(FakeStore(), embedder=FakeEmbedder(), settings=settings)
    service.warmup()

    dense = service.search("违约金", k=3, use_bm25=False)
    assert [hit.chunk_id for hit in dense] == ["c1", "c2", "c3"]
    assert dense[0].dense_score == 0.95 and dense[0].sparse_score == 0.0

    sparse = service.search("违约金", k=3, use_dense=False)
    assert sparse and sparse[0].chunk_id == "c1"
    assert sparse[0].dense_score == 0.0 and sparse[0].sparse_score > 0

    hybrid = service.search("违约金", k=3)
    assert len(hybrid) == 3
    assert all(isinstance(hit.score, float) for hit in hybrid)
    assert {"chunk_id", "law", "article", "text", "score",
            "dense_score", "sparse_score"} == set(asdict(hybrid[0]))
    # 融合后 c1 同时被两路命中，应排第一
    assert hybrid[0].chunk_id == "c1"


def test_service_config_and_validation() -> None:
    service = RetrievalService(FakeStore(), embedder=FakeEmbedder(), settings=make_settings())
    config = service.describe()
    assert config["embedding_model"] == "mock-embed"
    assert config["embedding_dim"] == 4
    assert config["rrf_k"] == 60
    assert "embedding_api_key" not in config  # 配置回显绝不能带密钥

    with pytest.raises(ValueError, match="query 不能为空"):
        service.search("  ")
    with pytest.raises(NotImplementedError, match="阶段 2 不接 rerank"):
        service.search("违约金", use_rerank=True)
    with pytest.raises(ValueError, match="不能同时关闭"):
        service.search("违约金", use_bm25=False, use_dense=False)
    with pytest.raises(ValueError, match="维"):
        service.search("违约金", embedding=[0.1, 0.2], use_bm25=False)


def test_weighted_fusion_mode_is_configurable() -> None:
    settings = make_settings(fusion_method="weighted", dense_weight=1.0,
                             sparse_weight=0.0, candidate_k=3)
    service = RetrievalService(FakeStore(), embedder=FakeEmbedder(), settings=settings)
    service.warmup()
    hits = service.search("违约金", k=3)
    assert [hit.chunk_id for hit in hits] == ["c1", "c2", "c3"]
    assert fuse([("a", 1.0)], [("b", 1.0)], method="weighted",
                dense_weight=1.0, sparse_weight=0.0)[0].score == 1.0


# ---- 端点 ------------------------------------------------------------------

def test_endpoint_returns_hits_elapsed_and_config(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = make_settings(candidate_k=3)
    service = RetrievalService(FakeStore(), embedder=FakeEmbedder(), settings=settings)
    service.warmup()
    api.set_service(service)
    monkeypatch.setattr("retrieval.api.get_retrieval_settings", lambda: settings)
    try:
        client = TestClient(create_app())
        response = client.post("/v1/retrieval/search", json={"query": "违约金", "k": 3})
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["hits"]) == 3
        assert body["hits"][0]["chunk_id"] == "c1"
        assert body["elapsed_ms"] >= 0
        assert body["config"]["fusion_method"] == "rrf"
        assert body["config"]["embedding_model"] == "mock-embed"
        assert body["config"]["query_embedding_source"].startswith("computed:")
        assert "embedding_api_key" not in json.dumps(body)

        with_vector = client.post(
            "/v1/retrieval/search",
            json={"query": "违约金", "k": 2, "embedding": [0.1, 0.2, 0.3, 0.4],
                  "use_bm25": False},
        )
        assert with_vector.status_code == 200
        assert with_vector.json()["config"]["query_embedding_source"] == "provided"

        rerank = client.post(
            "/v1/retrieval/search", json={"query": "x", "use_rerank": True}
        )
        assert rerank.status_code == 501
        assert client.post("/v1/retrieval/search", json={"query": ""}).status_code == 422
    finally:
        api.set_service(None)


def test_healthz_still_works_with_retrieval_router() -> None:
    """铁律：新增端点不得破坏既有链路（哪怕最小健康检查）。"""
    client = TestClient(create_app())
    assert client.get("/healthz").json() == {"status": "ok", "service": "python-agent"}
