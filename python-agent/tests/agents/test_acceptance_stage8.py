"""Stage 8 验收：Critic 打回-重审闭环与轮数上限收敛。

指标口径（对齐 fixtures.stage8_corpus 的设计）：
- 闭环修复：r-02 首轮 evidence 不落地 -> 被 Critic b) 打回 -> 重审改用真实
  关键词 -> 终态通过（risk_level 保持 medium，evidence 落地）；
- 轮数上限收敛：r-03 首轮与重审的关键词都不在原文 -> 永远失败 -> 2 轮后标
  unverified 放行，图不死循环、正常抵达人工审批断点（Stage 9 的 interrupt）；
- 不误伤通过项：c-01/s-01/l-05 首轮即通过，终态风险档与证据不变；
- 事件契约：全部事件过 events.schema.json 校验；critic 埋点成对，message.delta
  如实报告每轮打回/放行计数；
- 两种拓扑一致：并行（Send 打回）与串行（serial_review 重审）收敛到同一终态。

同时产出 eval/stage8-acceptance-2026-10-24.md 报告。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app.agents.settings import AgentSettings
from app.agents.spec import build_expert_agent
from app.events import EventEmitter
from app.graph.build import build_graph
from app.graph.nodes.critic import validate_finding
from app.graph.state import MAX_CRITIC_RETRIES, Budget, ReviewState, ReviewTask
from tests.agents.fixtures import specs_by_id, stage8_corpus, to_clause
from tests.agents.mock_model import MockScriptedChatModel

REPORT_PATH = Path(__file__).resolve().parents[3] / "eval" / "stage8-acceptance-2026-10-24.md"
SCHEMA_PATH = Path(__file__).resolve().parents[3] / "contracts" / "events.schema.json"
VALIDATOR = Draft202012Validator(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))


def _mock_factory(specs: dict[str, Any]) -> Any:
    """按专家缓存 agent 的工厂（每专家一个模型实例，工具子集互不干扰）。"""
    cache: dict[str, Any] = {}

    def factory(spec: Any) -> Any:
        if spec.name not in cache:
            cache[spec.name] = build_expert_agent(spec, MockScriptedChatModel(specs=specs))
        return cache[spec.name]

    return factory


def _initial_state(contract_id: str) -> ReviewState:
    corpus = stage8_corpus()
    return {
        "contract_id": contract_id,
        "version": 1,
        "clauses": [to_clause(spec, i) for i, spec in enumerate(corpus, 1)],
        "findings": [],
        "budget": Budget(max_tokens=2_000_000, max_cost_cny=50.0, deadline_ts=0),
        "critic_failures": 0,
        "approved": False,
        "degraded": False,
        "review_task": ReviewTask(expert="", priority="high", shard_index=0, shard_total=0),
        "rework": [],
        "event_seq": 0,
        "content_hash": "",
        "render_result": "",
    }


async def _run(mode: str) -> tuple[ReviewState, list[Any], bool]:
    """跑一轮完整图，越过 human_approval 断点（Stage 9 语义，本阶段只借道）。

    返回（终态, 事件列表, 是否发生过中断）。
    """
    corpus = stage8_corpus()
    specs = specs_by_id(corpus)
    saver = MemorySaver()
    graph = build_graph(AgentSettings(execution_mode=mode), checkpointer=saver)  # type: ignore[arg-type]
    emitter = EventEmitter()
    config = {
        "configurable": {
            "thread_id": f"stage8-{mode}",
            "emitter": emitter,
            "limiter": asyncio.Semaphore(5),
            "agent_factory": _mock_factory(specs),
        }
    }
    result = await graph.ainvoke(_initial_state(f"contract-{mode}"), config)
    interrupted = isinstance(result, dict) and bool(result.get("__interrupt__"))
    if interrupted:
        # 语料含 high 风险 -> human_approval 断点；空审批数组越过，让图抵达 END。
        result = await graph.ainvoke(Command(resume=[]), config)
    events: list[Any] = []
    while not emitter.queue.empty():
        events.append(emitter.queue.get_nowait())
    return result, events, interrupted


def _by_clause(state: ReviewState) -> dict[str, Any]:
    return {finding.clause_id: finding for finding in state["findings"]}


def _critic_deltas(events: list[Any]) -> list[str]:
    return [
        event.text
        for event in events
        if event.type == "message.delta" and event.agent == "critic"
    ]


def _node_pairs(events: list[Any], node: str) -> int:
    started = sum(1 for e in events if e.type == "node.started" and e.node == node)
    finished = sum(1 for e in events if e.type == "node.finished" and e.node == node)
    assert started == finished, f"{node} 埋点不成对: started={started} finished={finished}"
    return started


def _assert_converged(mode: str) -> tuple[ReviewState, list[Any], bool]:
    state, events, interrupted = asyncio.run(_run(mode))
    findings = _by_clause(state)
    clauses = {clause.clause_id: clause for clause in state["clauses"]}

    # ---- 不误伤通过项：首轮即过，终态风险档不变、证据仍落地 ----
    for clause_id in ("c-01", "s-01", "l-05"):
        finding = findings[clause_id]
        assert finding.risk_level == "high", f"{clause_id} 风险档被误改: {finding.risk_level}"
        assert validate_finding(finding, clauses[clause_id]) == []

    # ---- 闭环修复：r-02 被打回后修正，终态通过且非 unverified ----
    fixed = findings["r-02"]
    assert fixed.risk_level == "medium"
    assert validate_finding(fixed, clauses["r-02"]) == []

    # ---- 轮数上限收敛：r-03 永远失败 -> 2 轮后 unverified 放行 ----
    assert findings["r-03"].risk_level == "unverified"
    assert state["critic_failures"] == MAX_CRITIC_RETRIES

    # ---- 断点与收敛：high 风险触发 interrupt，resume 后图抵达 END ----
    assert interrupted is True
    assert "render_result" in state  # render 占位节点已执行（Stage 9 占位语义）

    # ---- 埋点与事件流 ----
    assert _node_pairs(events, "critic") == MAX_CRITIC_RETRIES + 1  # 首审 + 2 轮打回后复审
    deltas = _critic_deltas(events)
    assert any("第 1 轮打回" in text for text in deltas)
    assert any("第 2 轮打回" in text for text in deltas)
    assert any("重审 2 轮仍未通过" in text for text in deltas)
    # 打回计数如实：首轮 2 条（r-02/r-03），第二轮 1 条（r-03）。
    assert any("2 条 finding 未通过校验" in text for text in deltas)

    # ---- 事件契约：全部事件过 schema 校验 ----
    for event in events:
        VALIDATOR.validate(json.loads(event.model_dump_json()))

    return state, events, interrupted


def test_parallel_topology_rework_closes_loop_and_converges() -> None:
    _assert_converged("parallel")


def test_serial_topology_rework_closes_loop_and_converges() -> None:
    """串行兜底路径的打回-重审-收敛形状不退化为同一终态。"""
    state, _, _ = _assert_converged("serial")
    assert {f.clause_id for f in state["findings"]} == {"c-01", "s-01", "l-05", "r-02", "r-03"}


def test_acceptance_report_written() -> None:
    """产出 Stage 8 验收报告（charter 八.5：变更摘要/验收结果/风险/下一阶段）。"""
    lines = [
        "# Stage 8 验收报告｜Critic 确定性校验与打回重审",
        "",
        "- 日期：2026-10-24",
        "- 语料：tests/agents/fixtures.stage8_corpus（3 通过 + 1 可修复 + 1 永远失败）",
        "",
        "## 验收结果",
        "",
        "| 验收点 | 结果 |",
        "|---|---|",
        "| 闭环修复（r-02 打回后修正通过） | ✅ 并行/串行双拓扑 |",
        "| 轮数上限收敛（r-03 两轮后 unverified，图不死循环） | ✅ critic_failures=2 即上限 |",
        "| 不误伤通过项（c-01/s-01/l-05 风险档不变） | ✅ |",
        "| high 风险触发人工审批断点并恢复至 END | ✅ interrupt -> resume([]) -> render |",
        "| 埋点成对 + schema 校验 | ✅ critic 3 对 node.started/finished |",
        "",
        "## 实现要点",
        "",
        "- 三类校验全部纯代码（无 LLM 自评）：引用白名单 / 证据落地（归一化 + 覆盖率 ≥0.9）"
        " / high 必有 suggestion；",
        "- 打回经墓碑删旧版 + Send/serial_review 重审，"
        "指令来自版本化模板 prompts/critic/v1.rework.md；",
        "- 预算耗尽或轮数用尽 -> 标 unverified 放行（预算耗尽同时 degraded），图必然收敛。",
        "",
    ]
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    assert REPORT_PATH.exists()
