"""阶段 6 单测：配置源、live/mock 分流、候选映射、降级、端点缺省参数。

为什么不连真库：这些断言检查的是「参数有没有被读进来 / 契约有没有被投影对」，
用假服务即可；真库与完整审查链路的验收在 `python -m retrieval.acceptance_stage6`。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.tools import law as law_tool
from app.tools import retrieval_backend
from retrieval import api
from retrieval.config import RETRIEVAL_YAML, RetrievalSettings
from retrieval.service import Hit


def _live_settings(**overrides: object) -> SimpleNamespace:
    base = dict(
        retrieval_mode="live",
        default_k=5,
        index_strategy="S5_context",
        use_bm25_default=True,
        use_rerank_default=False,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _hit(chunk_id: str = "civil_code:第五百八十五条:0:S5_context") -> Hit:
    return Hit(
        chunk_id=chunk_id,
        law="民法典",
        article="第五百八十五条",
        text="当事人可以约定一方违约时应当支付一定数额的违约金。",
        score=0.03278689,
        dense_score=0.79,
        sparse_score=48.3,
    )


def test_yaml_is_loaded_from_package_path_not_cwd() -> None:
    assert RETRIEVAL_YAML.exists()
    settings = RetrievalSettings()
    assert settings.index_strategy == "S5_context"
    assert settings.embedding_backend == "E1"
    assert settings.fusion_method == "rrf"
    assert settings.rrf_k == 60
    assert settings.default_k == 5
    assert settings.use_bm25_default is True
    assert settings.use_rerank_default is False
    assert "55432" in settings.pg_dsn, "默认 DSN 不应指向 5432 的 checkpoint 库"


def test_env_var_overrides_yaml(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUSEGUARD_RETRIEVAL_DEFAULT_K", "9")
    monkeypatch.setenv("CLAUSEGUARD_RETRIEVAL_INDEX_STRATEGY", "S1_whole")
    monkeypatch.setenv("CLAUSEGUARD_RETRIEVAL_MODE", "live")
    settings = RetrievalSettings()
    assert settings.default_k == 9
    assert settings.index_strategy == "S1_whole"
    assert settings.retrieval_mode == "live"


def test_mock_mode_returns_none_so_tool_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        retrieval_backend,
        "get_retrieval_settings",
        lambda: _live_settings(retrieval_mode="mock"),
    )
    assert retrieval_backend.live_payload("违约金") is None


def test_live_payload_maps_chunk_id_and_real_article(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(retrieval_backend, "get_retrieval_settings", lambda: _live_settings())
    monkeypatch.setattr(retrieval_backend, "_query_vector", lambda _q: (0.1,) * 1024)
    captured: dict[str, object] = {}

    class FakeService:
        def search(self, **kwargs: object) -> list[Hit]:
            captured.update(kwargs)
            return [_hit()]

    monkeypatch.setattr(api, "get_service", lambda: FakeService())
    payload = json.loads(retrieval_backend.live_payload("违约金过高") or "{}")
    assert payload["candidates"][0]["id"] == "civil_code:第五百八十五条:0:S5_context"
    assert payload["candidates"][0]["article"] == "《民法典》第五百八十五条"
    assert captured["k"] == 5  # 来自 YAML 的 top_k，不是 Java/工具写死
    assert captured["use_rerank"] is False


def test_backend_failure_degrades_to_empty_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(retrieval_backend, "get_retrieval_settings", lambda: _live_settings())
    monkeypatch.setattr(retrieval_backend, "_query_vector", lambda _q: (0.1,) * 1024)

    def boom() -> object:
        raise RuntimeError("pgvector down")

    monkeypatch.setattr(api, "get_service", boom)
    assert json.loads(retrieval_backend.live_payload("违约金") or "{}") == {"candidates": []}


def test_tool_delegates_to_live_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = json.dumps(
        {"candidates": [{"id": "civil_code:第五百八十五条:0:S5_context"}]},
        ensure_ascii=False,
    )
    monkeypatch.setattr(law_tool, "live_payload", lambda _q: expected)
    result = law_tool.mock_search_law.invoke({"query": "违约金"})
    assert result == expected


class _FakeService:
    settings = RetrievalSettings()

    def describe(self) -> dict[str, object]:
        return {"embedding_backend": "E1"}

    def search(self, **kwargs: object) -> list[Hit]:
        return [_hit(), _hit("civil_code:第五百八十八条:0:S5_context")]


def test_endpoint_defaults_k_from_yaml_and_echoes_config() -> None:
    api.set_service(_FakeService())  # type: ignore[arg-type]
    try:
        client = TestClient(create_app())
        resp = client.post("/v1/retrieval/search", json={"query": "违约金"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["config"]["top_k"] == 5
        assert body["config"]["strategy"] == "S5_context"
        assert body["config"]["use_rerank"] is False
        assert body["hits"][0]["chunk_id"].startswith("civil_code:")

        explicit = client.post(
            "/v1/retrieval/search", json={"query": "违约金", "k": 9, "use_rerank": False}
        )
        assert explicit.json()["config"]["top_k"] == 9
    finally:
        api.set_service(None)
