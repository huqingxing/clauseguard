"""Stage 14 验收：一条 trace 串起 Java（父上下文）与 6 个专家 Agent span。

为什么用 InMemorySpanExporter 而不是连 Langfuse：验收要断言的是「父子关系与属性正确」，
用内存导出器可以逐 span 断言 trace_id/parent/attributes，Langfuse 只是同一个 OTLP payload
的消费端（deploy/otel/verify_trace.py 用真实 OTLP 采集器验证上报链路）。

覆盖点：
1. traceparent 解析与全零/畸形头的拒绝（不能被当成父上下文）；
2. 根 span 的父 = Java 传来的 span_id（跨服务一条 trace）；
3. 每个图节点都有 span；6 个专家 span 标成 agent 且带 tokens；
4. 专家 span 的父 = 根 span（contextvar 传播，不需要手工穿参）。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from langgraph.checkpoint.memory import MemorySaver
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app import observability
from app.agents.mock_model import mock_chat_model
from app.agents.settings import AgentSettings
from app.agents.spec import build_expert_agent
from app.config import Settings
from app.events import EventEmitter
from app.graph.build import build_graph
from app.graph.state import Clause, ReviewState, ReviewTask
from app.main import _make_initial_state, _run_pipeline

# 伪造一份 Java 生成的 traceparent：父 span 属于 Java 进程，Python 不负责收尾。
PARENT_TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
PARENT_SPAN_ID = "00f067aa0ba902b7"
TRACEPARENT = f"00-{PARENT_TRACE_ID}-{PARENT_SPAN_ID}-01"

# 每个专家一条条款（关键词命中各自 scope_keywords），保证 6 个 agent span 都会出现。
CLAUSES: list[str] = [
    "买方应在收到发票后三十日内支付合同价款。",
    "乙方应具备相应的营业执照与经营资质。",
    "任何一方违约的，应向对方支付违约金。",
    "履约过程中产生的知识产权归甲方所有。",
    "乙方处理个人信息应当遵守数据安全的要求。",
    "因本合同发生的争议提交仲裁委员会仲裁。",
]


@pytest.fixture()
def exporter() -> Any:
    """每个测试重建 provider（OTel 的全局 provider 一个进程只能设置一次）。"""
    observability.reset_tracing()
    span_exporter = InMemorySpanExporter()
    observability.setup_tracing(Settings(otel_enabled=True), exporter=span_exporter)
    yield span_exporter
    observability.reset_tracing()


def _agent_factory() -> Any:
    cache: dict[str, Any] = {}

    def factory(spec: Any) -> Any:
        if spec.name not in cache:
            cache[spec.name] = build_expert_agent(
                spec, mock_chat_model(spec.name, latency_s=0.0)
            )
        return cache[spec.name]

    return factory


def _state(contract_id: str = "contract-trace") -> ReviewState:
    state = _make_initial_state(contract_id, AgentSettings(), "")
    state["clauses"] = [
        Clause(clause_id=f"doc#{index:04d}", index=index, text=text)
        for index, text in enumerate(CLAUSES)
    ]
    return state


async def _run_traced(
    span_exporter: InMemorySpanExporter, contract_id: str = "contract-trace"
) -> list[ReadableSpan]:
    settings = AgentSettings()
    emitter = EventEmitter()
    graph = build_graph(settings, MemorySaver())
    config: dict[str, Any] = {
        "configurable": {
            "thread_id": f"{contract_id}:v1",
            "emitter": emitter,
            "limiter": asyncio.Semaphore(5),
            "agent_factory": _agent_factory(),
        }
    }
    state = _state(contract_id)
    assert state["review_task"] == ReviewTask(
        expert="", priority="high", shard_index=0, shard_total=0
    )
    await _run_pipeline(
        emitter,
        graph,
        state,
        config,
        f"run-{contract_id}",
        PARENT_TRACE_ID,
        traceparent=TRACEPARENT,
        session=f"{contract_id}:v1",
        mode="full",
    )
    return list(span_exporter.get_finished_spans())


def _collect(span_exporter: InMemorySpanExporter) -> list[ReadableSpan]:
    return asyncio.run(_run_traced(span_exporter))



# ---- 1. traceparent 解析 ----


def test_parse_traceparent_accepts_valid_header() -> None:
    parsed = observability.parse_traceparent(TRACEPARENT)
    assert parsed == (PARENT_TRACE_ID, PARENT_SPAN_ID)


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "garbage",
        "00-" + "0" * 32 + "-" + "0" * 16 + "-01",  # 全零：W3C 明令非法
        "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7",  # 少一段
    ],
)
def test_parse_traceparent_rejects_invalid(header: str | None) -> None:
    assert observability.parse_traceparent(header) is None
    # 非法头不能让 trace_id 为空：前端按它聚合可见，空 trace_id 就是不可观测。
    assert observability.trace_id_from(header)


def test_extract_parent_context_keeps_remote_trace_id() -> None:
    context = observability.extract_parent_context(TRACEPARENT)
    span = observability.trace.get_current_span(context)
    span_context = span.get_span_context()
    assert f"{span_context.trace_id:032x}" == PARENT_TRACE_ID
    assert span_context.is_remote is True


# ---- 2. 完整运行的 span 树 ----


def test_run_span_is_child_of_java_traceparent(exporter: InMemorySpanExporter) -> None:
    spans = _collect(exporter)
    roots = [span for span in spans if span.name == "run.contract-review"]
    assert len(roots) == 1, f"应有且仅有一个根 span，实际 {[s.name for s in roots]}"
    root = roots[0]
    assert f"{root.context.trace_id:032x}" == PARENT_TRACE_ID
    assert root.parent is not None
    assert f"{root.parent.span_id:016x}" == PARENT_SPAN_ID
    assert root.attributes is not None
    assert root.attributes["langfuse.session.id"] == "contract-trace:v1"
    assert root.attributes["clauseguard.review.mode"] == "full"


def test_every_node_has_span(exporter: InMemorySpanExporter) -> None:
    spans = _collect(exporter)
    names = {span.name for span in spans}
    for node in ["ingest", "diff", "dispatch", "critic", "cache_store", "render"]:
        assert f"node.{node}" in names, f"缺失节点 span: {node}，实有 {sorted(names)}"


def test_six_agent_spans_share_one_trace(exporter: InMemorySpanExporter) -> None:
    spans = _collect(exporter)
    agents = [
        span
        for span in spans
        if span.name.startswith("node.review.") and span.attributes is not None
    ]
    assert len(agents) == 6, f"应有 6 个专家 span，实际 {[s.name for s in agents]}"
    root = next(span for span in spans if span.name == "run.contract-review")
    for span in agents:
        assert f"{span.context.trace_id:032x}" == PARENT_TRACE_ID, "专家 span 跑到了别的 trace"
        # 父 = 根 span：证明 contextvar 在图执行中正确传播（无需手工穿参）。
        assert span.parent is not None
        assert span.parent.span_id == root.context.span_id
        assert span.attributes is not None
        assert span.attributes["langfuse.observation.type"] == "agent"
        # agent 名与事件流 node.started.agent 同一口径（spec.agent_name）。
        assert span.attributes["gen_ai.agent.name"] in {
            "commercial_expert",
            "subject_expert",
            "liability_expert",
            "ip_expert",
            "data_expert",
            "compliance_expert",
        }
        # tokens 必须落在 agent span 上（Langfuse 的成本面板读这两个字段）。
        assert span.attributes["gen_ai.usage.input_tokens"] > 0
        assert span.attributes["gen_ai.usage.output_tokens"] > 0

    agent_names = {span.attributes["gen_ai.agent.name"] for span in agents if span.attributes}
    assert agent_names == {
        "commercial_expert",
        "subject_expert",
        "liability_expert",
        "ip_expert",
        "data_expert",
        "compliance_expert",
    }


def test_root_span_aggregates_usage(exporter: InMemorySpanExporter) -> None:
    spans = _collect(exporter)
    root = next(span for span in spans if span.name == "run.contract-review")
    assert root.attributes is not None
    # 整轮用量回填根 span：Langfuse 的 trace 汇总（tokens/成本）只读根 span。
    assert root.attributes["gen_ai.usage.input_tokens"] > 0
    assert root.attributes["gen_ai.usage.output_tokens"] > 0
    assert root.attributes["clauseguard.findings.count"] > 0
