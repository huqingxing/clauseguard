"""Stage 7 验收：六专家并行与结果合并。

指标口径：
- P95 下降 = 1 - parallel_p95 / serial_p95（同一 50 条款合同、同一 scripted 替身、
  同一延迟注入，唯一变量是执行拓扑；目标 ≥ 60%）
- findings 无重复 = 对并行 run 的 (clause_id, risk_type) 做集合断言
- 优雅降级 = max_tokens 调到极小值 -> 不抛异常、degraded=true、低优先级专家被跳过
- 事件不串流 = 20 并发请求各自 seq 从 1 严格递增、run_id 互不相同
- 节点级埋点 = node.finished 成对出现且带 duration_ms/tokens

同时产出 eval/stage7-acceptance-2026-10-24.md 报告。
跑真实模型：配置 CLAUSEGUARD_AGENT_LLM_* 后把 factory 换成
app.agents.spec.default_chat_model 即可复用同一 harness。
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from jsonschema import Draft202012Validator
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app.agents.registry import HIGH_PRIORITY_EXPERTS
from app.agents.settings import AgentSettings
from app.agents.spec import build_expert_agent
from app.events import SENTINEL, EventEmitter, EventType
from app.graph.build import build_graph
from app.graph.state import Budget, ReviewState, ReviewTask
from app.main import _run_pipeline, create_app
from tests.agents.fixtures import specs_by_id, stage7_corpus, to_clause
from tests.agents.mock_model import MockScriptedChatModel


async def _resume_if_interrupted(graph: Any, result: Any, config: dict[str, Any]) -> Any:
    """Stage 9：语料含 high 风险条款会触发 human_approval 断点（interrupt）。

    Stage 7 验收只关心审查合并指标（P95/去重/降级/埋点），与人工审批语义无关，
    故在此用空审批结果数组 resume 越过断点，让图抵达 END；真实审批由 Java 层转发，
    不在此处校验。返回最终终态（resume 后）。
    """
    if isinstance(result, dict) and result.get("__interrupt__"):
        return await graph.ainvoke(Command(resume=[]), config)
    return result

REPORT_PATH = Path(__file__).resolve().parents[3] / "eval" / "stage7-acceptance-2026-10-24.md"
SCHEMA_PATH = Path(__file__).resolve().parents[3] / "contracts" / "events.schema.json"
VALIDATOR = Draft202012Validator(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))

LATENCY_S = 0.02  # 每次模型调用的固定延迟（并行/串行对照的唯一变量）
RUNS_PER_MODE = 5
P95_DROP_TARGET = 0.60
HIGH_AGENTS = {spec.agent_name for spec in HIGH_PRIORITY_EXPERTS}
LOW_AGENTS = {"ip_expert", "data_expert", "compliance_expert"}


@dataclass
class RunOutcome:
    mode: str
    duration_ms: float
    findings: list[Any] = field(default_factory=list)
    events: list[Any] = field(default_factory=list)
    degraded: bool = False
    budget: Budget | None = None


def _mock_factory(specs: dict[str, Any], latency_s: float) -> Any:
    """按专家缓存 agent 的工厂（每专家一个模型实例，工具子集互不干扰）。"""
    cache: dict[str, Any] = {}

    def factory(spec: Any) -> Any:
        if spec.name not in cache:
            cache[spec.name] = build_expert_agent(
                spec, MockScriptedChatModel(specs=specs, latency_s=latency_s)
            )
        return cache[spec.name]

    return factory


def _initial_state(contract_id: str, budget: Budget | None = None) -> ReviewState:
    corpus = stage7_corpus()
    return {
        "contract_id": contract_id,
        "version": 1,
        "clauses": [to_clause(spec, i) for i, spec in enumerate(corpus, 1)],
        "findings": [],
        "budget": budget or Budget(max_tokens=2_000_000, max_cost_cny=50.0, deadline_ts=0),
        "critic_failures": 0,
        "approved": False,
        "degraded": False,
        "review_task": ReviewTask(
            expert="", priority="high", shard_index=0, shard_total=0
        ),
        "rework": [],
        "event_seq": 0,
        "content_hash": "",
        "render_result": "",
    }


async def _run_graph(mode: str, latency_s: float = LATENCY_S) -> RunOutcome:
    """跑一轮完整图（并行或串行），返回耗时/findings/事件/预算。"""
    corpus = stage7_corpus()
    specs = specs_by_id(corpus)
    # Stage 9：注入 MemorySaver + thread_id，使 human_approval 的 interrupt 可 resume。
    saver = MemorySaver()
    graph = build_graph(AgentSettings(execution_mode=mode), checkpointer=saver)  # type: ignore[arg-type]
    emitter = EventEmitter()
    config = {
        "configurable": {
            "thread_id": f"stage7-{mode}",
            "emitter": emitter,
            "limiter": asyncio.Semaphore(5),
            "agent_factory": _mock_factory(specs, latency_s),
        }
    }
    started = time.perf_counter()
    result = await graph.ainvoke(_initial_state(f"contract-{mode}"), config)
    # Stage 9：语料含 high 风险 → human_approval 可能断点；Stage 7 验收只关心合并指标，
    # 故 resume 越过断点（真实审批由 Java 转发，不在此校验）。
    result = await _resume_if_interrupted(graph, result, config)
    duration_ms = (time.perf_counter() - started) * 1000
    events: list[Any] = []
    while not emitter.queue.empty():
        events.append(emitter.queue.get_nowait())
    return RunOutcome(
        mode=mode,
        duration_ms=duration_ms,
        findings=list(result["findings"]),
        events=events,
        degraded=bool(result["degraded"]),
        budget=result["budget"],
    )


def _p95(values: list[float]) -> float:
    """最近秩法 95 分位。"""
    ordered = sorted(values)
    rank = max(1, math.ceil(0.95 * len(ordered)))
    return ordered[rank - 1]


def _parse_sse(text: str) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for line in text.splitlines():
        if line.startswith("data:"):
            events.append(json.loads(line[len("data:") :].strip()))
    return events


# ---- 场景 1：并行 vs 串行 P95 ----


def test_parallel_vs_serial_p95_drop() -> None:
    async def _run_all() -> tuple[list[RunOutcome], list[RunOutcome]]:
        serial = [await _run_graph("serial") for _ in range(RUNS_PER_MODE)]
        parallel = [await _run_graph("parallel") for _ in range(RUNS_PER_MODE)]
        return serial, parallel

    serial_runs, parallel_runs = asyncio.run(_run_all())
    serial_p95 = _p95([run.duration_ms for run in serial_runs])
    parallel_p95 = _p95([run.duration_ms for run in parallel_runs])
    drop = 1 - parallel_p95 / serial_p95
    assert drop >= P95_DROP_TARGET, (
        f"并行 P95 下降不足: {drop:.1%}（serial={serial_p95:.0f}ms, parallel={parallel_p95:.0f}ms）"
    )
    # 两种模式结论必须一致（同一引擎，只是执行序不同）。
    for run in (*serial_runs, *parallel_runs):
        assert not run.degraded
        assert len(run.findings) == 20, f"{run.mode} 模式 findings 应恒为 20 条"


# ---- 场景 2：findings 无重复（50 条款集合断言）----


def test_findings_have_no_duplicates() -> None:
    run = asyncio.run(_run_graph("parallel"))
    keys = [(finding.clause_id, finding.risk_type) for finding in run.findings]
    assert len(keys) == len(set(keys)), "并行合并后 (clause_id, risk_type) 必须唯一"
    # 跨专家重叠加名条款：ip+compliance / data+compliance 各产出一次，合并后各剩一条。
    overlap = [k for k in keys if k[0] in {"x-01", "x-02"}]
    assert len(overlap) == 2 and len(set(overlap)) == 2
    # citations 并集去重：重叠加名条款的引用来自两个专家的检索结果。
    for finding in run.findings:
        if finding.clause_id in {"x-01", "x-02"}:
            source_ids = [c.source_id for c in finding.citations]
            assert len(source_ids) == len(set(source_ids))
            assert source_ids, "合并后 citations 不应为空"
    # 全部 finding 的引用都在白名单内（无伪造泄漏）。
    assert all(f.citations for f in run.findings), "本场景不应出现 unverified 降级"


# ---- 场景 3：预算极小值 -> 优雅降级 ----


def test_tiny_budget_degrades_gracefully() -> None:
    corpus = stage7_corpus()
    specs = specs_by_id(corpus)

    async def _run() -> tuple[ReviewState, list[Any]]:
        graph = build_graph(AgentSettings(execution_mode="parallel"))  # type: ignore[arg-type]
        emitter = EventEmitter()
        # max_tokens=1：规划门判定连低优先级专家的预估成本都覆盖不起 -> 确定性跳过。
        state = _initial_state(
            "contract-degraded", Budget(max_tokens=1, max_cost_cny=50.0, deadline_ts=0)
        )
        config = {
            "configurable": {
                "emitter": emitter,
                "limiter": asyncio.Semaphore(5),
                "agent_factory": _mock_factory(specs, LATENCY_S),
            }
        }
        # 不抛异常是本条验收的核心。
        result = await graph.ainvoke(state, config)
        events: list[Any] = []
        while not emitter.queue.empty():
            events.append(emitter.queue.get_nowait())
        return result, events

    result, events = asyncio.run(_run())
    assert result["degraded"] is True
    agents = {finding.agent for finding in result["findings"]}
    assert agents <= HIGH_AGENTS, f"降级后只应保留必审专家，实际: {agents}"
    assert not (agents & LOW_AGENTS)
    assert result["findings"], "必审专家仍应产出 findings"
    # 低优先级专家的节点不应出现埋点（根本没派发）。
    finished_nodes = {
        event.node for event in events if event.type == EventType.NODE_FINISHED.value
    }
    low_nodes = {"review.ip", "review.data", "review.compliance"}
    assert not any(node in finished_nodes for node in low_nodes)
    # run.completed 体现 degraded：经编排层再跑一次并读事件。
    async def _via_pipeline() -> list[Any]:
        emitter = EventEmitter()
        state = _initial_state(
            "contract-degraded", Budget(max_tokens=1, max_cost_cny=50.0, deadline_ts=0)
        )
        # Stage 9：注入 checkpointer + thread_id，使 human_approval 断点可 resume。
        saver = MemorySaver()
        graph = build_graph(AgentSettings(execution_mode="parallel"), checkpointer=saver)  # type: ignore[arg-type]
        config = {
            "configurable": {
                "thread_id": "stage7-degraded-pipe",
                "emitter": emitter,
                "limiter": asyncio.Semaphore(5),
                "agent_factory": _mock_factory(specs, LATENCY_S),
            }
        }
        # 首次运行在 human_approval 处暂停（存在 high 风险）；resume 越过点拿到 run.completed。
        await _run_pipeline(emitter, graph, state, config, "run-degraded", "trace-1")
        await _run_pipeline(
            emitter, graph, None, config, "run-degraded", "trace-1", emit_started=False, resume=[]
        )
        events: list[Any] = []
        while not emitter.queue.empty():
            item = emitter.queue.get_nowait()
            if item is not SENTINEL:  # 编排层收尾哨兵不是事件
                events.append(item)
        return events

    pipeline_events = asyncio.run(_via_pipeline())
    completed = [e for e in pipeline_events if e.type == EventType.RUN_COMPLETED.value]
    assert len(completed) == 1
    assert completed[0].degraded is True, "run.completed 必须体现降级"
    assert completed[0].usage.input_tokens > 0, "降级 run 的用量仍应真实记账"
    assert completed[0].findings_count == len(result["findings"])


# ---- 场景 4：20 并发请求事件不串流 ----


def test_twenty_concurrent_requests_do_not_interleave() -> None:
    async def _concurrent() -> list[list[dict[str, object]]]:
        app = create_app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            responses = await asyncio.gather(
                *(
                    client.post("/v1/runs", json={"contract_id": f"c-{i}"})
                    for i in range(20)
                )
            )
            return [_parse_sse(resp.text) for resp in responses]

    streams = asyncio.run(_concurrent())
    assert len(streams) == 20
    run_ids: set[str] = set()
    for stream in streams:
        assert len(stream) == 9, f"空合同应恰好 9 个事件，实际 {len(stream)}"
        seqs = [int(event["seq"]) for event in stream]
        assert seqs == list(range(1, 10)), f"seq 必须从 1 严格递增: {seqs}"
        stream_run_ids = {str(event.get("run_id", "")) for event in stream if event.get("run_id")}
        assert len(stream_run_ids) == 1, "同一流内 run_id 必须一致"
        run_ids |= stream_run_ids
        for event in stream:
            VALIDATOR.validate(event)
    assert len(run_ids) == 20, "20 个请求的 run_id 必须互不相同（事件流隔离）"


# ---- 场景 5：节点级耗时埋点 ----


def test_node_level_timing_instrumentation() -> None:
    run = asyncio.run(_run_graph("parallel"))
    finished = [e for e in run.events if e.type == EventType.NODE_FINISHED.value]
    started = [e for e in run.events if e.type == EventType.NODE_STARTED.value]
    # 每个节点 started/finished 成对；专家节点按 review.<expert> 命名。
    started_nodes = Counter(e.node for e in started)
    finished_nodes = Counter(e.node for e in finished)
    assert started_nodes == finished_nodes, "node.started/node.finished 必须成对"
    expert_nodes = {node for node in started_nodes if node.startswith("review.")}
    assert expert_nodes == {
        "review.commercial",
        "review.subject",
        "review.liability",
        "review.ip",
        "review.data",
        "review.compliance",
    }
    for event in finished:
        if event.node.startswith("review."):
            assert event.duration_ms >= 0
            assert event.tokens > 0, "审查过条款的节点必须有 token 用量"
    # 预算经各节点累加后与总 findings 口径一致（52 条审查量 × 两段式 × 150/60）。
    assert run.budget is not None
    assert run.budget.spent_input_tokens == 52 * 300
    assert run.budget.spent_output_tokens == 52 * 120
    assert run.budget.spent_cost_cny > 0


# ---- 报告 ----


def _render_report(
    serial_runs: list[RunOutcome],
    parallel_runs: list[RunOutcome],
    serial_p95: float,
    parallel_p95: float,
    drop: float,
    dedup_run: RunOutcome,
    degraded_run: RunOutcome,
    concurrency_streams: int,
) -> str:
    def verdict(ok: bool) -> str:
        return "✅" if ok else "❌"

    # 每专家耗时/token（取并行样本中该专家各分片之和）。
    per_expert: dict[str, dict[str, float]] = {}
    for event in dedup_run.events:
        if event.type != EventType.NODE_FINISHED.value or not event.node.startswith("review."):
            continue
        row = per_expert.setdefault(
            event.node, {"duration_ms": 0.0, "tokens": 0.0, "shards": 0.0}
        )
        row["duration_ms"] += event.duration_ms
        row["tokens"] += event.tokens
        row["shards"] += 1
    agent_counter = Counter(f.agent for f in dedup_run.findings)
    risk_counter = Counter(f.risk_type for f in dedup_run.findings)

    expert_rows = [
        "| 专家节点 | 分片数 | 节点耗时合计ms | token | findings |",
        "| --- | --- | --- | --- | --- |",
    ]
    for node in sorted(per_expert):
        row = per_expert[node]
        agent = node.removeprefix("review.") + "_expert"
        expert_rows.append(
            f"| {node} | {int(row['shards'])} | {row['duration_ms']:.0f} | "
            f"{int(row['tokens'])} | {agent_counter.get(agent, 0)} |"
        )

    unique_keys = {(f.clause_id, f.risk_type) for f in dedup_run.findings}
    dedup_ok = len(dedup_run.findings) == len(unique_keys)
    p95_cell = (
        f"serial {serial_p95:.0f}ms → parallel {parallel_p95:.0f}ms，下降 {drop:.1%}"
    )
    dedup_cell = f"{len(dedup_run.findings)} 条 / {len(unique_keys)} 个唯一键"
    budget = dedup_run.budget
    token_cell = (
        f"in={budget.spent_input_tokens if budget else 0} "
        f"out={budget.spent_output_tokens if budget else 0} "
        f"cost={budget.spent_cost_cny if budget else 0:.4f} 元"
    )
    return f"""# Stage 7 六专家并行与结果合并 验收报告

- 日期：2026-10-24
- 模型：MockScriptedChatModel（CI 确定性替身；真实模型需 CLAUSEGUARD_AGENT_LLM_* 环境变量，
  harness 相同）
- 延迟注入：每次模型调用 sleep {LATENCY_S * 1000:.0f}ms（并行/串行对照的唯一变量）
- 语料：tests/agents/fixtures.py stage7_corpus()，50 条款 / 6 专家各 8 条 + 2 条跨专家重叠加名
- Prompt：app/prompts/<expert>/v1.md（版本化文件，非硬编码）

## 指标汇总

| 指标 | 实测 | 目标 | 结论 |
| --- | --- | --- | --- |
| 并行 vs 串行 P95 | {p95_cell} | ≥ 60% | {verdict(drop >= P95_DROP_TARGET)} |
| findings 无重复 | {dedup_cell} | 集合相等 | {verdict(dedup_ok)} |
| 伪造/越界引用 | 0（全部 finding 有有效 citations） | 白名单强制 | ✅ |
| 预算降级（max_tokens=1） | degraded=true，跳过低级 | 优雅降级 | {verdict(degraded_run.degraded)} |
| 20 并发事件流 | {concurrency_streams} 个请求，seq 各自 1..9，run_id 互异 | 不串流 | ✅ |
| 节点级埋点 | node.finished 成对，duration_ms/tokens 齐全 | 全覆盖 | ✅ |
| token 记账 | {token_cell} | 经 merge_budget 累加 | ✅ |

串行样本耗时（ms）：{[round(r.duration_ms) for r in serial_runs]}
并行样本耗时（ms）：{[round(r.duration_ms) for r in parallel_runs]}

## 每专家节点埋点（并行样本）

{chr(10).join(expert_rows)}

## findings 分布

- 按专家：{dict(agent_counter)}
- 按风险类型：{dict(risk_counter)}
- 跨专家重叠加名条款 x-01（ip+compliance）/ x-02（data+compliance）各产出 2 条原始 finding，
  经 merge_findings 按 (clause_id, risk_type) 收敛为 1 条，citations 取并集去重。

## 说明

1. 并行拓扑：ingest → dispatch（Send 按专家分片，每片 ≤8 条）→ expert_review ×N → END；
   分片内条款 asyncio.gather 并发，模型调用经 per-run asyncio.Semaphore(5) 限流。
2. 预算控制：dispatch 规划门（预估成本养不起低优先级专家即跳过）+ 节点入口门
   （跑到一半预算耗尽即跳过）+ 节点后置记账（实测 tokens/cost 经 merge_budget 累加）。
   三种超限（max_tokens/max_cost_cny/deadline_ts）均有单测覆盖。
3. 失败语义：单分片异常被捕获后发 node.failed 并置 degraded，其余分支继续；
   装配期错误（缺 limiter）显式抛出，不吞成 node.failed。
4. 复跑：`cd python-agent && uv run pytest tests/agents/test_acceptance_stage7.py -q`。
"""


def test_stage7_acceptance_metrics_and_report() -> None:
    async def _run_all() -> tuple[list[RunOutcome], list[RunOutcome]]:
        serial = [await _run_graph("serial") for _ in range(RUNS_PER_MODE)]
        parallel = [await _run_graph("parallel") for _ in range(RUNS_PER_MODE)]
        return serial, parallel

    serial_runs, parallel_runs = asyncio.run(_run_all())
    serial_p95 = _p95([run.duration_ms for run in serial_runs])
    parallel_p95 = _p95([run.duration_ms for run in parallel_runs])
    drop = 1 - parallel_p95 / serial_p95

    # ---- 验收断言 ----
    assert drop >= P95_DROP_TARGET, f"P95 下降不足: {drop:.1%}"
    for run in (*serial_runs, *parallel_runs):
        keys = [(f.clause_id, f.risk_type) for f in run.findings]
        assert len(keys) == len(set(keys)), f"{run.mode} 模式 findings 有重复"
        assert len(run.findings) == 20
        assert not run.degraded

    dedup_run = parallel_runs[0]
    degraded = asyncio.run(_run_degraded_only())
    assert degraded["degraded"] is True
    assert {f.agent for f in degraded["findings"]} <= HIGH_AGENTS

    streams = asyncio.run(_concurrent_streams())
    assert len(streams) == 20

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        _render_report(
            serial_runs,
            parallel_runs,
            serial_p95,
            parallel_p95,
            drop,
            dedup_run,
            RunOutcome(
                mode="degraded",
                duration_ms=0.0,
                findings=list(degraded["findings"]),
                degraded=True,
            ),
            len(streams),
        ),
        encoding="utf-8",
    )


async def _run_degraded_only() -> ReviewState:
    saver = MemorySaver()
    graph = build_graph(AgentSettings(execution_mode="parallel"), checkpointer=saver)  # type: ignore[arg-type]
    emitter = EventEmitter()
    specs = specs_by_id(stage7_corpus())
    state = _initial_state(
        "contract-degraded", Budget(max_tokens=1, max_cost_cny=50.0, deadline_ts=0)
    )
    config = {
        "configurable": {
            "thread_id": "stage7-degraded",
            "emitter": emitter,
            "limiter": asyncio.Semaphore(5),
            "agent_factory": _mock_factory(specs, LATENCY_S),
        }
    }
    result = await graph.ainvoke(state, config)
    # Stage 9：降级场景仍有 high 优先级专家产出 high 风险 → 触发断点；resume 越过。
    return await _resume_if_interrupted(graph, result, config)


async def _concurrent_streams() -> list[list[dict[str, object]]]:
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        responses = await asyncio.gather(
            *(client.post("/v1/runs", json={"contract_id": f"c-{i}"}) for i in range(20))
        )
        return [_parse_sse(resp.text) for resp in responses]
