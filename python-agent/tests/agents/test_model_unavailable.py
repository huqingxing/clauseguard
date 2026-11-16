"""Stage 15 验收：模型全部不可用时 fail-closed，部分不可用时明确降级。

背景（真实事故两起，同一个洞）：
1. Windows 环境变量里是失效密钥 → 7 个专家全 401 → 每条条款「调用失败，跳过该条款」；
2. 换了个额度用尽的密钥 → 全 403 → 同上。
两次的终态都是 `run.completed{findings_count: 0, degraded: false}`、review 状态
COMPLETED。也就是说一次彻底失败的审查，被包装成「这份合同没有风险」。

本文件锁三件事：
- 全部条款都没审成 → run.failed(code=MODEL_UNAVAILABLE)，不下发 run.completed；
- 部分没审成（3/N）→ 仍然 COMPLETED，但 degraded=true 且 degraded_detail 报出条数；
- 全部审成 → 不带 degraded_detail 字段（「没有这个字段」就是明确的信号）。

为什么用 mock 替身而不打真实模型：这三条判定的输入是「模型抛异常 / 正常返回」，
与模型能力无关；真实链路只能验证第一条（把密钥弄坏），后两条无法稳定复现。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from langgraph.checkpoint.memory import MemorySaver

from app.agents.registry import EXPERTS
from app.agents.settings import AgentSettings
from app.agents.spec import build_expert_agent
from app.events import SENTINEL, EventEmitter, EventType
from app.graph.build import build_graph
from app.graph.nodes.model_gate import (
    MODEL_OUTPUT_INVALID,
    MODEL_UNAVAILABLE,
    ModelUnavailableException,
    model_gate,
)
from app.graph.state import Budget, ReviewCounters, ReviewState, ReviewTask
from app.main import _run_pipeline
from tests.agents.fixtures import ClauseSpec, specs_by_id, stage7_corpus, to_clause
from tests.agents.mock_model import MockScriptedChatModel

SCHEMA_PATH = Path(__file__).resolve().parents[3] / "contracts" / "events.schema.json"
VALIDATOR = Draft202012Validator(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))

LATENCY_S = 0.01
# 非 high 风险语料：成功审出的结论不会触发 human_approval 断点，
# 这样「部分失败」用例能一路跑到 run.completed（断点会挡住终态，测不到降级明细）。
SAFE_CORPUS: list[ClauseSpec] = [spec for spec in stage7_corpus() if spec.risk_level != "high"]


class FlakyChatModel(MockScriptedChatModel):
    """可指定失败面儿的替身：fail_all 全失败，fail_clause_ids 按条款失败。

    抛的是 RuntimeError（真实链路里是 httpx.HTTPStatusError / ReadTimeout）——
    review_clause 的 except Exception 对两者一视同仁，这正是「一个洞两种事故」的原因。
    """

    fail_all: bool = False
    fail_clause_ids: tuple[str, ...] = ()

    def _respond(self, messages: Any) -> Any:
        request = self._parse_request(messages)
        clause_id = request[0] if request else ""
        if self.fail_all or (clause_id and clause_id in self.fail_clause_ids):
            raise RuntimeError("simulated model unavailable: 401/403/quota exhausted")
        return super()._respond(messages)


def _factory(specs: dict[str, ClauseSpec], **model_kwargs: Any) -> Any:
    """按专家缓存替身；model_kwargs 原样传给 FlakyChatModel。"""
    cache: dict[str, Any] = {}

    def factory(spec: Any) -> Any:
        if spec.name not in cache:
            cache[spec.name] = build_expert_agent(
                spec, FlakyChatModel(specs=specs, latency_s=LATENCY_S, **model_kwargs)
            )
        return cache[spec.name]

    return factory


def _initial_state(contract_id: str, corpus: list[ClauseSpec]) -> ReviewState:
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
        "content_hash": "hash-v1",
        "render_result": "",
    }


def _drain(emitter: EventEmitter) -> list[Any]:
    out: list[Any] = []
    while not emitter.queue.empty():
        item = emitter.queue.get_nowait()
        if item is not SENTINEL:
            out.append(item)
    return out


def _run(corpus: list[ClauseSpec], contract_id: str, **model_kwargs: Any) -> list[Any]:
    """跑完整一轮图，返回事件列表（终态事件在该列表里，不做断言）。"""

    async def _go() -> list[Any]:
        graph = build_graph(AgentSettings(), checkpointer=MemorySaver())
        emitter = EventEmitter()
        config = {
            "configurable": {
                "thread_id": f"t-{contract_id}",
                "emitter": emitter,
                "limiter": asyncio.Semaphore(5),
                "agent_factory": _factory(specs_by_id(corpus), **model_kwargs),
            }
        }
        await _run_pipeline(
            emitter,
            graph,
            _initial_state(contract_id, corpus),
            config,
            f"run-{contract_id}",
            f"trace-{contract_id}",
        )
        return _drain(emitter)

    events = asyncio.run(_go())
    # 每次都要过契约校验：新增字段最容易在这里翻车（additionalProperties=false）。
    for event in events:
        VALIDATOR.validate(json.loads(event.model_dump_json()))
    return events


def _completed(events: list[Any]) -> Any:
    found = [e for e in events if e.type == EventType.RUN_COMPLETED.value]
    assert len(found) == 1, f"应有且仅有一条 run.completed，实际 {len(found)}"
    return found[0]


# ---- 验收 1：全部失败 → status=FAILED（Java 侧）且 run.failed 带 MODEL_UNAVAILABLE ----


def test_all_clauses_fail_emits_run_failed_not_completed() -> None:
    events = _run(SAFE_CORPUS, "c-all-fail", fail_all=True)

    types = [e.type for e in events]
    assert EventType.RUN_COMPLETED.value not in types, "全灭时绝不能下发 run.completed"
    failed = [e for e in events if e.type == EventType.RUN_FAILED.value]
    assert len(failed) == 1, "全灭时必须下发一条 run.failed"
    # 契约字段是 code（不是 reason）：前端与运维按 code 分流排查方向。
    assert failed[0].code == MODEL_UNAVAILABLE
    assert failed[0].retryable is True
    assert "均未产出可用结论" in failed[0].message


def test_all_clauses_fail_stops_before_cache_and_render() -> None:
    """门在 critic/cache_store/render 之前：不能留下「空结论进缓存」「干净的批注稿」。"""
    events = _run(SAFE_CORPUS, "c-gate-order", fail_all=True)
    nodes = {e.node for e in events if e.type == EventType.NODE_STARTED.value}
    assert "model_gate" in nodes, "model_gate 必须被走到（否则说明没接线）"
    for forbidden in ("critic", "cache_store", "human_approval", "render"):
        assert forbidden not in nodes, f"{forbidden} 不该在全灭时执行"


# ---- 验收 2：部分失败（3/N）→ COMPLETED + degraded_detail.unreviewed_clauses ----


def _failing_clause_ids(corpus: list[ClauseSpec], count: int) -> tuple[str, ...]:
    """挑 count 个「确有专家会审」的条款：范畴外的条款根本不会进模型调用，挑它等于没失败。"""
    in_scope = {
        clause.clause_id
        for clause in (to_clause(spec, i) for i, spec in enumerate(corpus, 1))
        for spec in EXPERTS
        if spec.in_scope(clause.text)
    }
    picked = [spec.clause_id for spec in corpus if spec.clause_id in in_scope][:count]
    assert len(picked) == count, f"语料里在范畴内的条款不足 {count} 条"
    return tuple(picked)


def test_partial_failure_reports_degraded_detail() -> None:
    failing = _failing_clause_ids(SAFE_CORPUS, 3)
    events = _run(SAFE_CORPUS, "c-partial", fail_clause_ids=failing)

    completed = _completed(events)
    assert completed.findings_count > 0, "部分失败不应吞掉其余条款的结论"
    assert completed.degraded is True, "有未审条款就必须标降级"
    assert completed.degraded_detail is not None
    # 按「条款」计：这 3 条没有任何专家审成；同一标准不能因为换了专家就重复计数。
    assert completed.degraded_detail.unreviewed_clauses == 3
    assert completed.degraded_detail.llm_errors >= 3


# ---- 验收 3：全部成功 → 不带 degraded_detail ----


def test_all_success_has_no_degraded_detail() -> None:
    completed = _completed(_run(SAFE_CORPUS, "c-all-ok"))
    assert completed.degraded is False
    assert completed.degraded_detail is None, "全成功时该字段必须缺省，否则前端会误报黄条"


# ---- 节点级单测：判定条件与错误码 ----


def _gate_config() -> dict[str, Any]:
    """节点级单测的 config：只要一个能发事件的 emitter（节点进/拦各发一条）。"""
    return {"configurable": {"emitter": EventEmitter()}}


def test_gate_ignores_runs_without_attempts() -> None:
    """没有条款进入审查（ingest 失败/预算全跳过）不是「模型不可用」，门必须放行。"""
    asyncio.run(model_gate({"review_counters": ReviewCounters()}, _gate_config()))
    asyncio.run(model_gate({}, _gate_config()))


def test_gate_passes_when_at_least_one_clause_succeeded() -> None:
    counters = ReviewCounters(
        clauses_attempted=19, clauses_ok=1, llm_errors=18, failed_clause_ids=["a"] * 18
    )
    asyncio.run(model_gate({"review_counters": counters}, _gate_config()))


def test_gate_distinguishes_unavailable_from_bad_output() -> None:
    """全是解析失败时不能报 MODEL_UNAVAILABLE——那会把排查引向配额，实际是输出契约崩了。"""
    counters = ReviewCounters(clauses_attempted=5, parse_errors=5, failed_clause_ids=["a"] * 5)
    with pytest.raises(ModelUnavailableException) as excinfo:
        asyncio.run(model_gate({"review_counters": counters}, _gate_config()))
    assert excinfo.value.code == MODEL_OUTPUT_INVALID


def test_unreviewed_counts_only_clauses_that_no_expert_reviewed() -> None:
    """同一条款 A 专家审成、B 专家失败：它有结论，不该算未审。"""
    counters = ReviewCounters(
        clauses_attempted=3,
        clauses_ok=1,
        llm_errors=2,
        ok_clause_ids=["doc#1"],
        failed_clause_ids=["doc#1", "doc#2"],
    )
    assert counters.unreviewed_clause_ids == ["doc#2"]
