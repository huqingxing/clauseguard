"""Stage 8 单测：critic 节点路由（打回 / 放行 / 不可路由 / 预算 / 轮数上限）。

覆盖节点在三类输入下的 Command 形状：
- 全部通过 -> 直达 cache_store（Stage 11 起：校验通过后先写缓存再进人工审批），update 为空；
- 可路由失败 -> 墓碑删旧版 + 计数 +1 + rework 指令，并行经 Send 派发专家节点、
  串行经 goto="serial_review"（state["rework"] 承载指令）；
- 不可路由失败（专家未注册 / 条款文本缺失）-> 标 unverified 放行，不炸整轮 run；
- 预算耗尽 -> 剩余失败全部 unverified 且 degraded=True（与 Stage 7 降级语义一致）；
- 轮数用尽（critic_failures ≥ MAX_CRITIC_RETRIES）-> unverified 放行，不再打回，
  保证图收敛（不死循环）。

同时锁定埋点：node.started/node.finished 成对，message.delta 报告打回/放行计数。
"""

from __future__ import annotations

import asyncio
from typing import Any

from langgraph.types import Send

from app.events import EventEmitter
from app.graph.nodes.critic import critic
from app.graph.state import (
    MAX_CRITIC_RETRIES,
    Budget,
    Clause,
    Finding,
    FindingTombstone,
    ReviewState,
    ReviewTask,
)

_SUBJECT_CLAUSE = Clause(
    clause_id="s-01",
    index=1,
    text="甲方签约主体资格存在瑕疵，其营业执照登记的经营范围不包含本合同项下业务。",
)


def _state(
    findings: list[Finding],
    clauses: list[Clause] | None = None,
    *,
    critic_failures: int = 0,
    budget: Budget | None = None,
) -> ReviewState:
    return {
        "contract_id": "contract-stage8",
        "version": 1,
        "clauses": clauses if clauses is not None else [_SUBJECT_CLAUSE],
        "findings": findings,
        "budget": budget or Budget(max_tokens=2_000_000, max_cost_cny=50.0, deadline_ts=0),
        "critic_failures": critic_failures,
        "approved": False,
        "degraded": False,
        "review_task": ReviewTask(expert="", priority="high", shard_index=0, shard_total=0),
        "rework": [],
        "event_seq": 0,
        "content_hash": "",
        "render_result": "",
    }


def _config() -> tuple[dict[str, Any], EventEmitter]:
    emitter = EventEmitter()
    return {"configurable": {"emitter": emitter}}, emitter


def _drain(emitter: EventEmitter) -> list[Any]:
    events: list[Any] = []
    while not emitter.queue.empty():
        events.append(emitter.queue.get_nowait())
    return events


def _grounded_finding(**overrides: object) -> Finding:
    """默认三类校验全过（subject 专家产出的 finding）。"""
    payload: dict[str, object] = {
        "clause_id": "s-01",
        "agent": "subject_expert",
        "risk_type": "qualification_risk",
        "risk_level": "high",
        "summary": "甲方签约主体资格与经营范围不匹配，合同效力存在瑕疵风险。",
        "evidence": "营业执照登记的经营范围不包含本合同项下业务",
        "suggestion": "签约前核验营业执照与资质文件。",
        "citations": [
            {"source_id": "law-001", "title": "《民法典》", "excerpt": "……"}
        ],
        "citation_whitelist": ("law-001",),
    }
    payload.update(overrides)
    return Finding.model_validate(payload)


def _ungrounded_finding(**overrides: object) -> Finding:
    """evidence 不落地（关键词不在原文）-> 必被 b) 拦下。"""
    return _grounded_finding(evidence="不存在的证据片段[关键词]", **overrides)


def _run(state: ReviewState, *, parallel: bool = True) -> tuple[Any, list[Any]]:
    config, emitter = _config()
    command = asyncio.run(critic(state, config, parallel=parallel))  # type: ignore[arg-type]
    return command, _drain(emitter)


# ---- 通过路径 ----


def test_all_findings_pass_routes_to_cache_store() -> None:
    command, events = _run(_state([_grounded_finding()]))
    assert command.goto == "cache_store"
    # 无失败即无状态更新（不写墓碑/计数/指令）。
    assert not command.update
    nodes = [(e.node, e.type) for e in events if e.type in ("node.started", "node.finished")]
    assert ("critic", "node.started") in nodes
    assert ("critic", "node.finished") in nodes


# ---- 打回路径 ----


def test_failing_finding_reworked_via_send_in_parallel() -> None:
    command, events = _run(_state([_ungrounded_finding()]))
    sends = command.goto
    assert isinstance(sends, list) and len(sends) == 1
    send: Send = sends[0]
    assert send.node == "expert_review"
    task = send.arg["review_task"]
    assert task.expert == "subject"
    assert task.rework_hints["s-01"]
    # 更新 = 墓碑删旧版 + 计数 +1 + 重审指令。
    update = command.update
    assert update["critic_failures"] == 1
    assert update["rework"][0].clause_id == "s-01"
    assert update["rework"][0].expert == "subject"
    assert update["rework"][0].reasons
    tombstones = [u for u in update["findings"] if isinstance(u, FindingTombstone)]
    assert [(t.clause_id, t.risk_type) for t in tombstones] == [("s-01", "qualification_risk")]
    # 打回计数进事件流（前端实时可见）。
    deltas = [e.text for e in events if e.type == "message.delta"]
    assert any("第 1 轮打回" in text for text in deltas)


def test_failing_finding_reworked_via_serial_review() -> None:
    command, _ = _run(_state([_ungrounded_finding()]), parallel=False)
    assert command.goto == "serial_review"
    assert command.update["critic_failures"] == 1
    assert command.update["rework"][0].agent == "subject_expert"


def test_same_expert_clause_failures_merge_reasons() -> None:
    """同 (专家, 条款) 的多条失败合并为一条打回指令，原因列表取并集。"""
    other = _ungrounded_finding(risk_type="authorization_risk")
    command, _ = _run(_state([_ungrounded_finding(), other]))
    assert isinstance(command.goto, list) and len(command.goto) == 1
    instruction = command.update["rework"][0]
    assert instruction.clause_id == "s-01"
    # 两条 finding 的失败原因都进入同一条指令（每条各一条 b) 原因）。
    assert len(instruction.reasons) == 2


# ---- 不可路由路径：标 unverified 放行，不炸整轮 ----


def test_unregistered_expert_marked_unverified() -> None:
    command, events = _run(_state([_ungrounded_finding(agent="ghost_expert")]))
    assert command.goto == "cache_store"
    update = command.update
    assert [f.risk_level for f in update["findings"] if isinstance(f, Finding)] == ["unverified"]
    assert any(isinstance(u, FindingTombstone) for u in update["findings"])
    deltas = [e.text for e in events if e.type == "message.delta"]
    assert any("无法路由重审" in text for text in deltas)


def test_missing_clause_text_marked_unverified() -> None:
    command, _ = _run(_state([_ungrounded_finding(clause_id="ghost-clause")], clauses=[]))
    assert command.goto == "cache_store"
    marked = [f for f in command.update["findings"] if isinstance(f, Finding)]
    assert [f.risk_level for f in marked] == ["unverified"]


# ---- 预算耗尽：全部放行 + 降级 ----


def test_budget_exhausted_marks_unverified_and_degrades() -> None:
    exhausted = Budget(max_tokens=10, max_cost_cny=50.0, deadline_ts=0, spent_input_tokens=100)
    command, events = _run(_state([_ungrounded_finding()], budget=exhausted))
    assert command.goto == "cache_store"
    assert command.update["degraded"] is True
    marked = [f for f in command.update["findings"] if isinstance(f, Finding)]
    assert [f.risk_level for f in marked] == ["unverified"]
    deltas = [e.text for e in events if e.type == "message.delta"]
    assert any("预算耗尽" in text for text in deltas)


# ---- 轮数上限：不再打回，图收敛 ----


def test_max_retries_marks_unverified_without_rework() -> None:
    command, events = _run(
        _state([_ungrounded_finding()], critic_failures=MAX_CRITIC_RETRIES)
    )
    assert command.goto == "cache_store"
    assert command.update.get("critic_failures") is None  # 不再递增
    assert "rework" not in command.update  # 不再产生打回指令
    assert command.update.get("degraded") is None  # 非预算原因，不降级
    marked = [f for f in command.update["findings"] if isinstance(f, Finding)]
    assert [f.risk_level for f in marked] == ["unverified"]
    deltas = [e.text for e in events if e.type == "message.delta"]
    assert any("重审 2 轮仍未通过" in text for text in deltas)


def test_round_counter_increments_from_current_value() -> None:
    command, _ = _run(_state([_ungrounded_finding()], critic_failures=1))
    assert command.update["critic_failures"] == 2
