"""Stage 6 验收：review.commercial 节点的解析、重试、降级与事件流。

覆盖任务清单第 4/5 条：解析失败重试 1 次仍失败记 parse_error 返回空列表（不抛异常）；
伪造 citation 丢弃并累加 dropped_citation；空 citations 降级 unverified；
token 消耗记日志；事件严格符合 events.schema.json。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from langchain_core.runnables import RunnableConfig

from app.agents.commercial import build_commercial_agent
from app.agents.validation import ReviewMetrics
from app.events import EventEmitter, EventType
from app.graph.nodes.review import (
    NODE_NAME,
    AgentOutputParseError,
    parse_agent_output,
    review_commercial,
    run_commercial_review,
)
from app.graph.state import Clause, ReviewState
from tests.agents.fixtures import (
    CONTRACT_META,
    NORMAL_CLAUSES,
    RISK_CLAUSES,
    specs_by_id,
    to_clause,
)
from tests.agents.mock_model import MockScriptedChatModel

SCHEMA_PATH = Path(__file__).resolve().parents[3] / "contracts" / "events.schema.json"
VALIDATOR = Draft202012Validator(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))

OUT_OF_SCOPE_TEXT = "双方应对商业秘密承担保密义务，未经对方书面同意不得向任何第三方披露。"


def _make_state(clauses: list[Clause]) -> ReviewState:
    from app.graph.state import Budget

    return {
        "contract_id": "contract-stage6",
        "version": 1,
        "clauses": clauses,
        "findings": [],
        "budget": Budget(max_tokens=8192, max_cost_cny=5.0, deadline_ts=0),
        "critic_failures": 0,
        "approved": False,
        "degraded": False,
        "rework": [],
        "event_seq": 0,
        "content_hash": "",
        "render_result": "",
    }


def _run_node(
    clauses: list[Clause], agent: Any
) -> tuple[dict[str, object], list[Any]]:
    emitter = EventEmitter()
    state = _make_state(clauses)
    config: RunnableConfig = {"configurable": {"emitter": emitter, "commercial_agent": agent}}
    result = asyncio.run(review_commercial(state, config))
    events: list[Any] = []
    while not emitter.queue.empty():
        events.append(emitter.queue.get_nowait())
    return result, events


@contextlib.contextmanager
def _capture_review_logs() -> Iterator[list[str]]:
    """直接挂 handler 捕获 app.graph.review 日志（不依赖 root logger 传播）。"""
    records: list[str] = []
    logger = logging.getLogger("app.graph.review")
    previous_level = logger.level
    logger.setLevel(logging.INFO)
    handler = logging.Handler()
    handler.emit = lambda record: records.append(record.getMessage())  # type: ignore[method-assign]
    logger.addHandler(handler)
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)


# ---- parse_agent_output 单元行为 ----


def test_parse_plain_json() -> None:
    content = '{"clause_id": "c-1", "findings": []}'
    output = parse_agent_output(content, "c-1")
    assert output.clause_id == "c-1"
    assert output.findings == []


def test_parse_tolerates_markdown_fence() -> None:
    content = '```json\n{"clause_id": "c-1", "findings": []}\n```'
    output = parse_agent_output(content, "c-1")
    assert output.clause_id == "c-1"


def test_parse_rejects_non_json() -> None:
    with pytest.raises(AgentOutputParseError, match="不是合法 JSON"):
        parse_agent_output("我认为这个条款有风险。", "c-1")


def test_parse_rejects_schema_violation_extra_field() -> None:
    content = '{"clause_id": "c-1", "findings": [], "note": "多写了一个字段"}'
    with pytest.raises(AgentOutputParseError, match="不符合 schema"):
        parse_agent_output(content, "c-1")


def test_parse_rejects_clause_id_mismatch() -> None:
    content = '{"clause_id": "c-2", "findings": []}'
    with pytest.raises(AgentOutputParseError, match="与输入不一致"):
        parse_agent_output(content, "c-1")


def test_parse_rejects_invalid_risk_level() -> None:
    content = (
        '{"clause_id": "c-1", "findings": [{"clause_id": "c-1", "risk_level": "critical", '
        '"summary": "s", "evidence": "e", "citations": [{"source_id": "law-001"}]}]}'
    )
    with pytest.raises(AgentOutputParseError, match="不符合 schema"):
        parse_agent_output(content, "c-1")


# ---- 节点端到端行为 ----


def test_node_emits_schema_valid_events_in_order() -> None:
    specs = specs_by_id(RISK_CLAUSES)
    model = MockScriptedChatModel(specs=specs)
    agent = build_commercial_agent(model=model)
    clauses = [to_clause(RISK_CLAUSES[0], 1)]
    result, events = _run_node(clauses, agent)

    types = [event.type for event in events]
    assert types[0] == EventType.NODE_STARTED.value
    assert types[-1] == EventType.NODE_FINISHED.value
    assert all(t == EventType.MESSAGE_DELTA.value for t in types[1:-1])
    for event in events:
        VALIDATOR.validate(json.loads(event.model_dump_json()))
    assert events[0].node == NODE_NAME
    assert events[0].agent == "commercial_expert"

    findings = result["findings"]
    assert isinstance(findings, list) and len(findings) == 1
    finding = findings[0]
    assert finding.clause_id == "risk-01"
    assert finding.agent == "commercial_expert"
    assert finding.risk_level == "high"
    assert finding.evidence and finding.evidence in RISK_CLAUSES[0].text
    assert finding.citations


def test_parse_retry_recovers_on_second_attempt() -> None:
    specs = specs_by_id(RISK_CLAUSES)
    model = MockScriptedChatModel(specs=specs, malformed_first=True)
    agent = build_commercial_agent(model=model)
    metrics = ReviewMetrics()
    outcome = asyncio.run(
        run_commercial_review(
            [to_clause(RISK_CLAUSES[0], 1)], CONTRACT_META, agent=agent, metrics=metrics
        )
    )
    assert metrics.parse_errors == 0
    assert len(outcome.findings) == 1
    # 两次尝试 × 两段式（工具调用 + 最终输出）= 4 次模型调用。
    assert model.call_count == 4


def test_persistent_parse_failure_records_error_and_returns_empty() -> None:
    specs = specs_by_id(RISK_CLAUSES)
    model = MockScriptedChatModel(specs=specs, malformed_always=True)
    agent = build_commercial_agent(model=model)
    metrics = ReviewMetrics()
    # 不抛异常是本条验收的核心。
    outcome = asyncio.run(
        run_commercial_review(
            [to_clause(RISK_CLAUSES[0], 1)], CONTRACT_META, agent=agent, metrics=metrics
        )
    )
    assert outcome.findings == []
    assert metrics.parse_errors == 1
    assert metrics.clauses_reviewed == 1


def test_fabricated_citations_dropped_and_downgraded() -> None:
    from dataclasses import replace

    specs = {
        spec.clause_id: replace(spec, cite_mode="fabricated")
        for spec in RISK_CLAUSES
        if spec.expect_finding
    }
    model = MockScriptedChatModel(specs=specs)
    agent = build_commercial_agent(model=model)
    metrics = ReviewMetrics()
    outcome = asyncio.run(
        run_commercial_review(
            [to_clause(spec, 1) for spec in RISK_CLAUSES if spec.expect_finding],
            CONTRACT_META,
            agent=agent,
            metrics=metrics,
        )
    )
    # 8 条 finding 各注入 1 条伪造 citation：全部拦截，finding 全部降级。
    assert metrics.dropped_citations == 8
    assert metrics.unverified_findings == 8
    assert all(f.risk_level == "unverified" for f in outcome.findings)
    assert all(f.citations == [] for f in outcome.findings)


def test_out_of_scope_clause_skipped_without_model_call() -> None:
    model = MockScriptedChatModel(specs=specs_by_id(RISK_CLAUSES))
    agent = build_commercial_agent(model=model)
    metrics = ReviewMetrics()
    outcome = asyncio.run(
        run_commercial_review(
            [Clause(clause_id="other-01", index=1, text=OUT_OF_SCOPE_TEXT)],
            CONTRACT_META,
            agent=agent,
            metrics=metrics,
        )
    )
    assert outcome.findings == []
    assert metrics.clauses_reviewed == 0
    assert metrics.clauses_skipped == 1
    assert model.call_count == 0


def test_token_usage_logged_per_clause() -> None:
    specs = specs_by_id(RISK_CLAUSES)
    model = MockScriptedChatModel(specs=specs)
    agent = build_commercial_agent(model=model)
    metrics = ReviewMetrics()
    with _capture_review_logs() as records:
        asyncio.run(
            run_commercial_review(
                [to_clause(RISK_CLAUSES[0], 1)], CONTRACT_META, agent=agent, metrics=metrics
            )
        )
    usage_logs = [line for line in records if "input_tokens=" in line]
    assert usage_logs, "token 消耗必须记入日志"
    # 替身固定 usage：两段式调用 × (150 in / 60 out)。
    assert "input_tokens=300" in usage_logs[0]
    assert "output_tokens=120" in usage_logs[0]
    assert metrics.input_tokens == 300
    assert metrics.output_tokens == 120


def test_normal_clause_without_finding_yields_empty() -> None:
    specs = specs_by_id(NORMAL_CLAUSES)
    model = MockScriptedChatModel(specs=specs)
    agent = build_commercial_agent(model=model)
    metrics = ReviewMetrics()
    outcome = asyncio.run(
        run_commercial_review(
            [to_clause(NORMAL_CLAUSES[0], 1)], CONTRACT_META, agent=agent, metrics=metrics
        )
    )
    assert outcome.findings == []
    assert metrics.findings_emitted == 0
    assert metrics.parse_errors == 0
