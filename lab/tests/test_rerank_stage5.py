# ruff: noqa: E402
"""阶段 5 reranker 单测：不联网、不读 .env、不依赖真实密钥。

真实 HTTP 用 httpx.MockTransport 打桩，既验证请求/响应解析与重试策略，又不
把 API 费用和网络抖动带进 CI。缓存用例专门验证「同 label 重跑逐位一致」。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

# 其他 lab 测试把 lab/ 当顶层目录（from metrics import ...），本模块要按包导入
# lab.rerank 才能覆盖到真实的包内相对引用；两个阶段目录都得在 sys.path 上。
_ROOT = Path(__file__).resolve().parents[2]
for _path in (str(_ROOT / "python-agent"), str(_ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from lab import rerank as rk
from lab import report as rp
from lab import run_rerank as rr
from retrieval.service import Hit


def make_hits(n: int = 20) -> list[Hit]:
    return [
        Hit(
            chunk_id=f"civil_code:第{i}条:0:S1_whole",
            law="民法典",
            article=f"第{i}条",
            text=f"第{i}条 当事人关于第{i}项义务的约定与违约责任条款正文。",
            score=1.0 - i / 100,
            dense_score=0.9 - i / 100,
            sparse_score=0.0,
        )
        for i in range(n)
    ]


def rerank_body(indices: list[int], tokens: int = 1234) -> dict[str, Any]:
    return {
        "output": {
            "results": [
                {"index": i, "relevance_score": 1.0 - j / 10} for j, i in enumerate(indices)
            ]
        },
        "usage": {"total_tokens": tokens},
        "request_id": "mock-request",
    }


def mock_client(
    counter: list[int], *, status: int = 200, body: dict[str, Any] | None = None
) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        counter[0] += 1
        if status != 200:
            return httpx.Response(status, text=f"mock error {status}")
        payload = body
        if payload is None:
            sent = json.loads(request.content)
            n = len(sent["input"]["documents"])
            payload = rerank_body(list(range(n)))
        return httpx.Response(200, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_cache_fingerprint_is_stable_and_content_sensitive(tmp_path: Path) -> None:
    hits = make_hits(3)
    a = rk.RerankCache.fingerprint("违约金上限", hits, "gte-rerank-v2", 5)
    b = rk.RerankCache.fingerprint("违约金上限", make_hits(3), "gte-rerank-v2", 5)
    assert a == b
    assert a != rk.RerankCache.fingerprint("定金罚则", hits, "gte-rerank-v2", 5)
    assert a != rk.RerankCache.fingerprint("违约金上限", hits[:2], "gte-rerank-v2", 5)
    assert a != rk.RerankCache.fingerprint("违约金上限", hits, "other-model", 5)
    cache = rk.RerankCache(tmp_path / "cache")
    assert cache.load(cache.path_for(a)) is None
    cache.save(cache.path_for(a), {"order": [0, 1, 2]})
    assert cache.load(cache.path_for(a)) == {"order": [0, 1, 2]}


def test_deterministic_reranker_contract_and_zero_cost() -> None:
    r = rk.DeterministicLexicalRerank()
    hits = make_hits()
    result = r.rerank("第3条 违约责任", hits, top_n=5)
    assert len(result.ranked) == len(hits)
    assert len(result.top5) == 5
    assert result.tokens == 0
    assert r.usage.tokens == 0
    assert r.usage.calls == 1
    # 同输入两次结果逐位一致（稳定 tie-break，不依赖解释器实现）。
    again = r.rerank("第3条 违约责任", hits, top_n=5)
    assert [h.chunk_id for h in result.ranked] == [h.chunk_id for h in again.ranked]
    assert r.rerank("x", [], top_n=5).ranked == ()


def test_live_reranker_parses_scores_and_tracks_usage(tmp_path: Path) -> None:
    counter = [0]
    hits = make_hits()
    # 让服务端把候选顺序倒过来，验证实现真的按 relevance_score 重排而非照抄原序。
    body = rerank_body(list(reversed(range(len(hits)))), tokens=1804)
    client = mock_client(counter, body=body)
    r = rk.DashScopeGteRerank(
        api_key="unit-test-key", cache=rk.RerankCache(tmp_path), client=client
    )
    result = r.rerank("违约金上限怎么规定的", hits, top_n=5)
    assert counter[0] == 1
    assert len(result.ranked) == 20
    assert len(result.top5) == 5
    assert [h.chunk_id for h in result.ranked[:3]] == [h.chunk_id for h in reversed(hits)][:3]
    assert result.tokens == 1804
    assert r.usage.tokens == 1804
    assert r.usage.requests == 1
    assert r.usage.live_calls == 1
    assert r.usage.live_latency_ms_total > 0
    assert r.usage.cost_cny == 0.0
    r.close()


def test_live_cache_hit_keeps_order_and_charges_nothing(tmp_path: Path) -> None:
    counter = [0]
    hits = make_hits()
    client = mock_client(counter)
    cache = rk.RerankCache(tmp_path)
    r = rk.DashScopeGteRerank(api_key="unit-test-key", cache=cache, client=client)
    first = r.rerank("同一条查询", hits, top_n=5)
    second = r.rerank("同一条查询", hits, top_n=5)
    assert counter[0] == 1  # 第二次不发起 HTTP
    assert second.cache_hit is True
    assert second.tokens == 0
    assert [h.chunk_id for h in first.ranked] == [h.chunk_id for h in second.ranked]
    assert [h.chunk_id for h in second.top5] == [h.chunk_id for h in first.top5]
    assert r.usage.cache_hits == 1
    assert r.usage.tokens == 1234
    r.close()


def test_live_4xx_fails_fast_without_retry(tmp_path: Path) -> None:
    counter = [0]
    r = rk.DashScopeGteRerank(
        api_key="unit-test-key",
        client=mock_client(counter, status=403),
        cache=rk.RerankCache(tmp_path),
    )
    with pytest.raises(rk.RerankUnavailable, match="HTTP 403"):
        r.rerank("违约金", make_hits(), top_n=5)
    assert counter[0] == 1
    assert r.usage.retries == 0
    r.close()


def test_live_5xx_retries_twice_then_raises(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(rk.time, "sleep", lambda _seconds: None)
    counter = [0]
    r = rk.DashScopeGteRerank(
        api_key="unit-test-key",
        client=mock_client(counter, status=503),
        cache=rk.RerankCache(tmp_path),
    )
    with pytest.raises(rk.RerankUnavailable, match="重试 2 次"):
        r.rerank("违约金", make_hits(), top_n=5)
    assert counter[0] == 3
    assert r.usage.retries == 2
    r.close()


def test_live_fills_missing_candidates_to_keep_pool_size(tmp_path: Path) -> None:
    # API 只回 3 条时，ranked 仍须有全部 20 条，否则 @10 分母在 on/off 侧不等。
    counter = [0]
    body = rerank_body([7, 2, 11])
    r = rk.DashScopeGteRerank(
        api_key="unit-test-key",
        client=mock_client(counter, body=body),
        cache=rk.RerankCache(tmp_path),
    )
    result = r.rerank("违约金", make_hits(), top_n=5)
    assert len(result.ranked) == 20
    assert len({hit.chunk_id for hit in result.ranked}) == 20
    assert [hit.chunk_id for hit in result.ranked[:3]] == [
        make_hits()[7].chunk_id,
        make_hits()[2].chunk_id,
        make_hits()[11].chunk_id,
    ]
    r.close()


def test_missing_key_never_silently_degrades(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.delenv("CLAUSEGUARD_AGENT_LLM_API_KEY", raising=False)
    with pytest.raises(rk.RerankUnavailable, match="缺少"):
        rk.DashScopeGteRerank(cache=rk.RerankCache(tmp_path))
    with pytest.raises(ValueError, match="未知 reranker"):
        rk.make_reranker("not-a-mode", cache_dir=tmp_path)


def test_tradeoff_table_boundaries_and_trigger_rates() -> None:
    rows = [
        {
            "type": "concept" if i % 2 == 0 else "scene",
            "off_top1_score": float(i),
            "off_recall": 0.5,
            "on_recall": 0.8,
            "off_mrr": 0.4,
            "on_mrr": 0.6,
            "off_latency_ms": 5.0,
            "rerank_latency_ms": 100.0,
            "tokens": 1000,
        }
        for i in range(10)
    ]
    table, quantiles = rr.build_tradeoff(rows)
    assert table[0]["policy"] == "全关" and table[0]["recall"] == 0.5
    assert table[0]["latency_mean_ms"] == 5.0
    assert table[1]["policy"] == "全开" and table[1]["recall"] == 0.8
    assert table[1]["latency_mean_ms"] == 105.0
    by_type = next(row for row in table if row["policy"] == "条件开：非概念型")
    assert by_type["trigger_rate"] == 0.5
    assert by_type["recall"] == 0.65
    assert by_type["latency_mean_ms"] == 55.0
    assert set(quantiles) == {"p25", "p50", "p75"}


def test_recommendation_is_always_one_of_three() -> None:
    base = {
        "off": {"overall": {"recall": 0.60, "latency_ms_mean": 6.0}},
        "on": {"overall": {"recall": 0.62, "latency_ms_mean": 140.0}},
        "bootstrap": {"significant": False},
        "usage": {"tokens": 100000},
        "tradeoff": [
            {
                "policy": "全关",
                "recall": 0.60,
                "latency_mean_ms": 6.0,
                "trigger_rate": 0.0,
                "tokens_per_query": 0,
            },
            {
                "policy": "全开",
                "recall": 0.62,
                "latency_mean_ms": 140.0,
                "trigger_rate": 1.0,
                "tokens_per_query": 1000,
            },
            {
                "policy": "条件开：非概念型",
                "recall": 0.619,
                "latency_mean_ms": 40.0,
                "trigger_rate": 0.3,
                "tokens_per_query": 300,
            },
        ],
    }
    verdict, reason = rp._recommendation(base)
    assert verdict == "条件开"
    assert "触发率" in reason

    base["bootstrap"]["significant"] = True
    base["tradeoff"] = base["tradeoff"][:2]
    assert rp._recommendation(base)[0] == "开"

    base["on"]["overall"]["recall"] = 0.58
    assert rp._recommendation(base)[0] == "关"
