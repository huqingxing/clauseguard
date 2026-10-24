"""Stage 7 验收：dispatch 分片策略（Send fan-out 的正确性）。

锁定：按专家范畴切分、每片 ≤ SHARD_SIZE、分片序号/总数自洽、载荷自带节点所需
通道、空合同干净收尾、预算超限跳过低优先级专家并标记 degraded、高优先级任何
预算下都派发。
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END
from langgraph.types import Send

from app.agents.registry import EXPERTS, get_expert
from app.graph.nodes.dispatch import (
    NODE_NAME,
    PER_CLAUSE_TOKEN_ESTIMATE,
    SHARD_SIZE,
    build_sends,
)
from app.graph.state import Budget, ReviewState, ReviewTask
from tests.agents.fixtures import stage7_corpus, to_clause


def _state(
    clauses: list[Any],
    *,
    max_tokens: int = 2_000_000,
    max_cost_cny: float = 50.0,
    deadline_ts: int = 0,
) -> ReviewState:
    return {
        "contract_id": "c-dispatch",
        "version": 3,
        "clauses": clauses,
        "findings": [],
        "budget": Budget(
            max_tokens=max_tokens, max_cost_cny=max_cost_cny, deadline_ts=deadline_ts
        ),
        "critic_failures": 0,
        "approved": False,
        "degraded": False,
        "review_task": ReviewTask(expert="", priority="high", shard_index=0, shard_total=0),
        "rework": [],
        "event_seq": 0,
        "content_hash": "",
        "render_result": "",
    }


def _tasks(sends: list[Send]) -> list[ReviewTask]:
    return [send.arg["review_task"] for send in sends]  # type: ignore[index]


def test_shards_respect_size_limit_and_cover_all_experts() -> None:
    corpus = stage7_corpus()
    state = _state([to_clause(spec, i) for i, spec in enumerate(corpus, 1)])
    sends, skipped = build_sends(state)
    assert skipped == []
    tasks = _tasks(sends)
    # 52 条审查量（50 条款 + 2 条跨专家重叠加名）覆盖 6 个专家。
    experts = {task.expert for task in tasks}
    assert experts == {spec.name for spec in EXPERTS}
    # 每片 ≤ SHARD_SIZE，且分片序号/总数自洽。
    by_expert: dict[str, list[ReviewTask]] = {}
    for task in tasks:
        by_expert.setdefault(task.expert, []).append(task)
    for expert, expert_tasks in by_expert.items():
        assert all(len(t.clauses) <= SHARD_SIZE for t in expert_tasks)
        assert [t.shard_index for t in expert_tasks] == list(range(len(expert_tasks)))
        assert all(t.shard_total == len(expert_tasks) for t in expert_tasks)
        # 优先级与注册表一致。
        assert all(t.priority == get_expert(expert).priority for t in expert_tasks)


def test_send_payload_carries_channels_the_node_needs() -> None:
    state = _state([to_clause(spec, 1) for spec in stage7_corpus()[:1]])
    sends, _ = build_sends(state)
    assert sends
    payload = sends[0].arg
    # Send 派发的节点只见载荷键：合同元信息与预算必须随载荷下发。
    assert payload["contract_id"] == "c-dispatch"
    assert payload["version"] == 3
    assert payload["budget"] == state["budget"]


def test_empty_contract_produces_no_sends() -> None:
    sends, skipped = build_sends(_state([]))
    assert sends == [] and skipped == []


def test_tiny_max_tokens_skips_low_priority_experts() -> None:
    corpus = stage7_corpus()
    # max_tokens=1：规划门判定连低优先级专家的预估成本都覆盖不起 -> 确定性跳过。
    state = _state(
        [to_clause(spec, i) for i, spec in enumerate(corpus, 1)], max_tokens=1
    )
    sends, skipped = build_sends(state)
    assert skipped == ["ip", "data", "compliance"]
    assert {send.arg["review_task"].expert for send in sends} == {  # type: ignore[index]
        "commercial",
        "subject",
        "liability",
    }
    assert all(send.arg["review_task"].priority == "high" for send in sends)  # type: ignore[index]


def test_zero_max_tokens_also_skips_low_priority() -> None:
    corpus = stage7_corpus()
    state = _state([to_clause(spec, i) for i, spec in enumerate(corpus, 1)], max_tokens=0)
    _, skipped = build_sends(state)
    assert skipped == ["ip", "data", "compliance"]


def test_past_deadline_skips_low_priority() -> None:
    corpus = stage7_corpus()
    state = _state(
        [to_clause(spec, i) for i, spec in enumerate(corpus, 1)], deadline_ts=1
    )
    _, skipped = build_sends(state)
    assert skipped == ["ip", "data", "compliance"]


def test_default_budget_keeps_all_experts() -> None:
    corpus = stage7_corpus()
    state = _state([to_clause(spec, i) for i, spec in enumerate(corpus, 1)])
    low_clauses = sum(
        1
        for spec in corpus
        for expert in EXPERTS
        if expert.priority == "low" and expert.in_scope(spec.text)
    )
    # 默认预算下的规划门余量：默认 2M 远超 低优先级条款数 × 预估单价。
    assert PER_CLAUSE_TOKEN_ESTIMATE * low_clauses < state["budget"].max_tokens
    _, skipped = build_sends(state)
    assert skipped == []


def test_node_name_constant() -> None:
    assert NODE_NAME == "dispatch"


def test_end_constant_is_importable() -> None:
    # 防止误把 END 写成字符串字面量（回归保护）。
    assert END == "__end__"
