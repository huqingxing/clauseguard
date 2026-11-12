"""Stage 9 验收：人工审批 interrupt 断点 + 重启恢复 + 事件 seq 续接。

为什么单独成文件：本阶段验收点是「图内断点 + 进程重启后可恢复 + seq 不重置」，
与 Stage 7 的并行合并指标正交。用 mock 替身驱动真实 high 风险路径（语料含 high 风险条款，
必然命中 human_approval 断点），不依赖外部 Postgres——checkpointer 用 MemorySaver 注入，
其语义与 PostgresSaver 一致（断点落盘），足以验证 resume 不重跑整图、seq 续接。

验收映射：
- (a) 触发审批 → 前端收到 interrupt 事件 → 停住：_run_pipeline 暂停（返回 False），
     事件流含 interrupt、无 run.completed；
- (b) 重启 Python → resume 能继续跑完，seq 继续递增不重置：恢复用同一 checkpointer，
      从 checkpoint 续接 emitter._seq，resume 后事件 seq 严格大于暂停时最大 seq，
      且不再重跑 ingest/dispatch；
- (c) 合同版本/哈希校验（409）与审批落库属 Java 职责，见 java-service 单侧；此处只验证
      Python 侧「resume 端点契约」与「不从头重跑」。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
from jsonschema import Draft202012Validator
from langgraph.checkpoint.memory import MemorySaver

from app.agents.settings import AgentSettings
from app.agents.spec import build_expert_agent
from app.db import InMemoryRunRegistry
from app.events import SENTINEL, EventEmitter, EventType
from app.graph.build import build_graph
from app.graph.state import Budget, ReviewState, ReviewTask
from app.graph.threads import review_thread_id
from app.main import _run_pipeline, create_app
from tests.agents.fixtures import specs_by_id, stage7_corpus, to_clause
from tests.agents.mock_model import MockScriptedChatModel

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "contracts" / "events.schema.json"
VALIDATOR = Draft202012Validator(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))

LATENCY_S = 0.01


def _mock_factory(specs: dict[str, Any], latency_s: float) -> Any:
    cache: dict[str, Any] = {}

    def factory(spec: Any) -> Any:
        if spec.name not in cache:
            cache[spec.name] = build_expert_agent(
                spec, MockScriptedChatModel(specs=specs, latency_s=latency_s)
            )
        return cache[spec.name]

    return factory


def _initial_state(contract_id: str) -> ReviewState:
    """带真实语料的初始状态（语料含 high 风险条款，必然触发 human_approval 断点）。"""
    corpus = stage7_corpus()
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


def _parse_sse(text: str) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for line in text.splitlines():
        if line.startswith("data:"):
            events.append(json.loads(line[len("data:") :].strip()))
    return events


# ---- (a) 触发审批 → 停住（interrupt 事件，无 run.completed）----


def test_human_approval_interrupt_pauses_pipeline() -> None:
    async def _run() -> tuple[bool, list[Any]]:
        saver = MemorySaver()
        graph = build_graph(AgentSettings(), checkpointer=saver)
        emitter = EventEmitter()
        specs = specs_by_id(stage7_corpus())
        config = {
            "configurable": {
                "thread_id": "t-pause",
                "emitter": emitter,
                "limiter": asyncio.Semaphore(5),
                "agent_factory": _mock_factory(specs, LATENCY_S),
            }
        }
        paused = await _run_pipeline(
            emitter, graph, _initial_state("c-pause"), config, "run-pause", "trace-pause"
        )
        return paused, _drain(emitter)

    paused, events = asyncio.run(_run())
    # 暂停：_run_pipeline 返回 False（未下发 run.completed）。
    assert paused is False
    types = [e.type for e in events]
    assert EventType.INTERRUPT.value in types, "必须下发 interrupt 事件让前端停住"
    assert EventType.RUN_COMPLETED.value not in types, "暂停时不得下发 run.completed"
    # 中断原因与待审项基线必须随 interrupt 事件携带（Java 据此展示 + 乐观锁校验）。
    interrupt_event = next(e for e in events if e.type == EventType.INTERRUPT.value)
    payload = interrupt_event.payload
    assert payload["reason"] == "high_risk_pending_approval"
    assert payload["contract_version"] == 1
    assert payload["content_hash"] == "hash-v1"
    assert payload["items"], "interrupt 必须逐条款列出待审 high 风险项"
    # 所有事件必须符合 events.schema.json（铁律 3）。用 model_dump_json 序列化为线格式
    # （ts 转 RFC3339 字符串）后再校验，与生产 SSE 载荷口径一致。
    for event in events:
        VALIDATOR.validate(json.loads(event.model_dump_json()))


# ---- (b) 恢复后跑完 + seq 续接不重置 + 不重跑整图 ----


def test_resume_continues_without_rerun_and_seq_not_reset() -> None:
    async def _run() -> tuple[list[Any], list[Any], int, int]:
        saver = MemorySaver()
        registry = InMemoryRunRegistry()
        graph = build_graph(AgentSettings(), checkpointer=saver)
        specs = specs_by_id(stage7_corpus())
        config_template = {
            "configurable": {
                "thread_id": "t-resume",
                "limiter": asyncio.Semaphore(5),
                "agent_factory": _mock_factory(specs, LATENCY_S),
            }
        }
        await registry.put(
            "run-resume",
            {"thread_id": "t-resume", "contract_id": "c-resume", "version": 1,
             "content_hash": "hash-v1", "trace_id": "trace-resume", "event_seq": 0},
        )
        # 首次运行到暂停。
        emitter1 = EventEmitter()
        config1 = {
            **config_template,
            "configurable": {**config_template["configurable"], "emitter": emitter1},
        }
        paused = await _run_pipeline(
            emitter1, graph, _initial_state("c-resume"), config1, "run-resume", "trace-resume",
            registry=registry,
        )
        assert paused is False
        pause_events = _drain(emitter1)
        pause_max_seq = max(e.seq for e in pause_events)

        # 模拟进程重启：新发射器，从 run_registry 续接事件序号（不重置）。
        # 注意不能读图状态：aupdate_state 写图状态会清掉断点（见 _persist_event_seq）。
        persisted = await registry.get("run-resume")
        assert persisted is not None and persisted["event_seq"] == pause_max_seq, (
            "中断时 seq 必须已落注册表，否则重启后续接不上"
        )
        emitter2 = EventEmitter()
        emitter2._seq = int(persisted["event_seq"])
        config2 = {
            **config_template,
            "configurable": {**config_template["configurable"], "emitter": emitter2},
        }
        completed = await _run_pipeline(
            emitter2, graph, None, config2, "run-resume", "trace-resume",
            emit_started=False, resume=[], registry=registry,
        )
        resume_events = _drain(emitter2)
        return pause_events, resume_events, pause_max_seq, (1 if completed else 0)

    pause_events, resume_events, pause_max_seq, completed_flag = asyncio.run(_run())
    assert completed_flag == 1, "resume 后运行必须完成（run.completed）"
    resume_types = [e.type for e in resume_events]
    assert EventType.RUN_COMPLETED.value in resume_types
    # seq 续接：resume 事件最小 seq 必须严格大于暂停时最大 seq（不重置）。
    resume_seqs = [e.seq for e in resume_events]
    assert min(resume_seqs) > pause_max_seq, (
        f"seq 必须续接而非重置：pause_max={pause_max_seq}, resume_min={min(resume_seqs)}"
    )
    # 不重跑整图：resume 阶段只应发射 render 的 node.started；human_approval 的
    # NODE_STARTED 已在暂停那次发射，resume 不重跑 ingest/dispatch/human_approval。
    resume_started_nodes = {
        e.node for e in resume_events if e.type == EventType.NODE_STARTED.value
    }
    assert resume_started_nodes == {"render"}, (
        f"resume 不得重跑整图，实际 started 节点: {resume_started_nodes}"
    )
    # human_approval 的 NODE_FINISHED 必须随 resume 下发（证明断点被续跑而非丢弃）。
    resume_finished_nodes = {
        e.node for e in resume_events if e.type == EventType.NODE_FINISHED.value
    }
    assert "human_approval" in resume_finished_nodes, "resume 必须补发 human_approval NODE_FINISHED"


# ---- (c) resume 端点契约：注册表定位断点 + 复用 checkpointer 恢复 ----


def test_resume_endpoint_resumes_paused_run() -> None:
    async def _run() -> tuple[bool, int]:
        saver = MemorySaver()
        registry = InMemoryRunRegistry()
        # 先直接把一条运行跑到暂停，并写入注册表（模拟「已暂停、等待审批」的运行）。
        graph = build_graph(AgentSettings(), checkpointer=saver)
        emitter1 = EventEmitter()
        specs = specs_by_id(stage7_corpus())
        # ADR-0006：resume 端点由 run_id 派生 thread（不再读注册表落盘值），
        # 故经 HTTP 端点续跑的用例必须用同一唯一入口注入，否则断点定位不到。
        ep_thread = review_thread_id("run-ep")
        config1 = {
            "configurable": {
                "thread_id": ep_thread,
                "emitter": emitter1,
                "limiter": asyncio.Semaphore(5),
                "agent_factory": _mock_factory(specs, LATENCY_S),
            }
        }
        paused = await _run_pipeline(
            emitter1, graph, _initial_state("c-ep"), config1, "run-ep", "trace-ep",
            registry=registry,
        )
        assert paused is False
        await registry.put(
            "run-ep",
            {
                "thread_id": ep_thread,
                "contract_id": "c-ep",
                "version": 1,
                "content_hash": "hash-v1",
                "trace_id": "trace-ep",
            },
        )
        # 经 HTTP 端点 resume（复用同一 checkpointer + 注册表，模拟重启后服务）。
        # 恢复以 SSE 流式下发，故按 SSE 消费并断言终态 run.completed 抵达。
        app = create_app(registry=registry, checkpointer=saver)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            events: list[dict[str, object]] = []
            async with client.stream("POST", "/v1/runs/run-ep/resume", json=[]) as resp:
                assert resp.status_code == 200
                async for line in resp.aiter_lines():
                    if line.startswith("data:"):
                        events.append(json.loads(line[len("data:"):].strip()))
        types = [event["type"] for event in events]
        assert "run.completed" in types, "resume 必须下发 run.completed 让 Java 续接终态"
        # 防回归：resume 必须真的续跑（render 节点开跑），而不是断点丢失后的空跑。
        started_nodes = {
            event.get("node") for event in events if event["type"] == "node.started"
        }
        assert "render" in started_nodes, (
            f"resume 后必须继续执行 render 节点（断点丢失会空跑），实际 started: {started_nodes}"
        )
        # 断点续接语义：不得重跑 ingest/dispatch。
        assert "ingest" not in started_nodes and "dispatch" not in started_nodes
        return True, 0

    asyncio.run(_run())


def test_resume_endpoint_404_when_run_unknown() -> None:
    async def _run() -> None:
        saver = MemorySaver()
        registry = InMemoryRunRegistry()
        app = create_app(registry=registry, checkpointer=saver)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/v1/runs/does-not-exist/resume", json=[])
            assert resp.status_code == 404
            assert resp.json()["code"] == "RUN_NOT_FOUND"

    asyncio.run(_run())
