"""Stage 7 验收：expert_review 并行分片节点。

锁定：分片审查闭环（事件顺序/埋点字段/budget 增量）、载荷驱动的专家解析、
信号量缺失显式失败（不静默退化为不限流）、低优先级分片预算超限跳过、
单分片异常走 node.failed 且不扩散（其余分支可继续）、findings 经校验层产出。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from langchain_core.runnables import RunnableConfig

from app.agents.registry import get_expert
from app.agents.spec import build_expert_agent
from app.events import EventEmitter, EventType
from app.graph.nodes.expert_review import CONCURRENCY_LIMIT, expert_review, get_limiter
from app.graph.state import Budget, ReviewState, ReviewTask
from tests.agents.fixtures import specs_by_id, stage7_corpus, to_clause
from tests.agents.mock_model import MockScriptedChatModel


def _state(
    task: ReviewTask,
    *,
    max_tokens: int = 2_000_000,
    spent_tokens: int = 0,
) -> ReviewState:
    return {
        "contract_id": "c-node",
        "version": 1,
        "clauses": list(task.clauses),
        "findings": [],
        "budget": Budget(
            max_tokens=max_tokens,
            max_cost_cny=50.0,
            deadline_ts=0,
            spent_input_tokens=spent_tokens,
        ),
        "critic_failures": 0,
        "approved": False,
        "degraded": False,
        "review_task": task,
        "rework": [],
        "event_seq": 0,
        "content_hash": "",
        "render_result": "",
    }


def _config(
    emitter: EventEmitter,
    agent: Any,
    *,
    limiter: asyncio.Semaphore | None = None,
) -> RunnableConfig:
    return {
        "configurable": {
            "emitter": emitter,
            "limiter": limiter if limiter is not None else asyncio.Semaphore(CONCURRENCY_LIMIT),
            "agent_factory": lambda spec: agent,
        }
    }


def _task(clauses: list[Any], expert: str = "commercial", priority: str = "high") -> ReviewTask:
    return ReviewTask(
        expert=expert, priority=priority, shard_index=0, shard_total=1, clauses=clauses
    )


def _drain(emitter: EventEmitter) -> list[Any]:
    events: list[Any] = []
    while not emitter.queue.empty():
        events.append(emitter.queue.get_nowait())
    return events


def test_node_reviews_shard_and_returns_findings_budget_events() -> None:
    corpus = stage7_corpus()
    specs = specs_by_id(corpus)
    agent = build_expert_agent(
        get_expert("commercial"),
        MockScriptedChatModel(specs=specs),
    )
    # commercial 范畴的前 3 条语料条款。
    scoped = [
        to_clause(spec, i)
        for i, spec in enumerate(corpus, 1)
        if get_expert("commercial").in_scope(spec.text)
    ][:3]
    task = _task(scoped)
    emitter = EventEmitter()
    result = asyncio.run(expert_review(_state(task), _config(emitter, agent)))
    events = _drain(emitter)

    types = [event.type for event in events]
    assert types[0] == EventType.NODE_STARTED.value
    assert types[-1] == EventType.NODE_FINISHED.value
    assert all(t == EventType.MESSAGE_DELTA.value for t in types[1:-1])
    # 节点级耗时埋点：duration_ms + tokens 必须落在 node.finished 上
    # （NodeFinished 契约无 agent 字段，专家身份由 node 名 review.<expert> 承载）。
    finished = events[-1]
    assert finished.node == "review.commercial"
    assert finished.duration_ms >= 0
    assert finished.tokens > 0

    # findings + budget 增量（3 条 × 两段式 × 150/60）。
    findings = result["findings"]
    assert isinstance(findings, list) and findings
    assert all(f.agent == "commercial_expert" for f in findings)
    budget_delta = result["budget"]
    assert budget_delta.spent_input_tokens == 3 * 300
    assert budget_delta.spent_output_tokens == 3 * 120
    assert budget_delta.spent_cost_cny > 0


def test_node_reads_expert_from_payload_not_registry_default() -> None:
    corpus = stage7_corpus()
    specs = specs_by_id(corpus)
    liability_scoped = [
        to_clause(spec, i)
        for i, spec in enumerate(corpus, 1)
        if get_expert("liability").in_scope(spec.text)
    ][:2]
    agent = build_expert_agent(get_expert("liability"), MockScriptedChatModel(specs=specs))
    emitter = EventEmitter()
    result = asyncio.run(
        expert_review(_state(_task(liability_scoped, expert="liability")), _config(emitter, agent))
    )
    assert result["findings"], "liability 分片应产出 findings"
    assert all(f.agent == "liability_expert" for f in result["findings"])
    finished = [e for e in _drain(emitter) if e.type == EventType.NODE_FINISHED.value][-1]
    assert finished.node == "review.liability"


def test_low_priority_shard_skipped_when_budget_exhausted() -> None:
    # 跑到一半的场景：高优先级分支已烧掉 500 token，上限 100 -> 低优先级入口门触发。
    corpus = stage7_corpus()
    agent = build_expert_agent(
        get_expert("ip"),
        MockScriptedChatModel(specs=specs_by_id(corpus)),
    )
    scoped = [
        to_clause(spec, 1)
        for spec in corpus
        if get_expert("ip").in_scope(spec.text)
    ]
    emitter = EventEmitter()
    result = asyncio.run(
        expert_review(
            _state(
                _task(scoped, expert="ip", priority="low"), max_tokens=100, spent_tokens=500
            ),
            _config(emitter, agent),
        )
    )
    assert result == {"degraded": True}
    # 跳过不产生任何事件（没有执行就没有埋点）。
    assert _drain(emitter) == []


def test_missing_limiter_fails_loudly() -> None:
    corpus = stage7_corpus()
    agent = build_expert_agent(
        get_expert("commercial"),
        MockScriptedChatModel(specs=specs_by_id(corpus)),
    )
    scoped = [
        to_clause(spec, 1)
        for spec in corpus
        if get_expert("commercial").in_scope(spec.text)
    ][:1]
    config: RunnableConfig = {
        "configurable": {
            "emitter": EventEmitter(),
            "agent_factory": lambda spec: agent,
        }
    }
    try:
        asyncio.run(expert_review(_state(_task(scoped)), config))
    except RuntimeError as exc:
        assert "limiter" in str(exc)
    else:  # pragma: no cover - 失败路径
        raise AssertionError("缺少信号量必须显式失败，不静默退化为不限流")


def test_node_failure_emits_node_failed_and_degrades_without_raising() -> None:
    corpus = stage7_corpus()
    scoped = [
        to_clause(spec, 1)
        for spec in corpus
        if get_expert("commercial").in_scope(spec.text)
    ][:2]
    emitter = EventEmitter()

    def broken_factory(spec: Any) -> Any:
        raise RuntimeError("agent 组装失败（模拟）")

    config: RunnableConfig = {
        "configurable": {
            "emitter": emitter,
            "limiter": asyncio.Semaphore(CONCURRENCY_LIMIT),
            "agent_factory": broken_factory,
        }
    }
    # 不抛异常是本条验收的核心：node.failed 语义 + degraded，其余分支可继续。
    result = asyncio.run(expert_review(_state(_task(scoped)), config))
    assert result == {"degraded": True}
    events = _drain(emitter)
    failed = [e for e in events if e.type == EventType.NODE_FAILED.value]
    assert len(failed) == 1
    assert failed[0].node == "review.commercial"
    assert failed[0].agent == "commercial_expert"
    assert failed[0].code == "EXPERT_REVIEW_FAILED"
    assert "agent 组装失败" in failed[0].message
    # node.failed 之前先有 node.started（执行尝试过才失败）。
    assert events[0].type == EventType.NODE_STARTED.value


def test_get_limiter_accepts_semaphore() -> None:
    limiter = asyncio.Semaphore(3)
    config: RunnableConfig = {"configurable": {"limiter": limiter}}
    assert get_limiter(config) is limiter


def test_llm_error_is_contained_per_clause(caplog: Any) -> None:
    """单条款 Agent 调用失败：记 llm_errors 跳过该条款，不炸分片（引擎层消化）。"""
    corpus = stage7_corpus()
    scoped = [
        to_clause(spec, 1)
        for spec in corpus
        if get_expert("commercial").in_scope(spec.text)
    ][:2]

    class ExplodingModel(MockScriptedChatModel):
        async def _agenerate(
            self,
            messages: Any,
            stop: Any = None,
            run_manager: Any = None,
            **kwargs: Any,
        ) -> Any:
            raise ConnectionError("模拟模型服务不可达")

    agent = build_expert_agent(
        get_expert("commercial"), ExplodingModel(specs=specs_by_id(corpus))
    )
    emitter = EventEmitter()
    with caplog.at_level(logging.WARNING, logger="app.graph.review"):
        result = asyncio.run(expert_review(_state(_task(scoped)), _config(emitter, agent)))
    # 两条条款都失败 -> 无 findings，但分片正常返回（预算增量为 0）。
    assert result["findings"] == []
    assert result["budget"].spent_input_tokens == 0
    assert any("Agent 调用失败" in record.message for record in caplog.records)
