"""Stage 6 验收：20 条款召回/误报 + 5 条伪造法条拦截 + P95 + 解析成功率。

指标口径：
- 召回 = 已知风险条款中产出 finding 的比例（目标 ≥ 7/10）
- 误报 = 正常条款中产出 finding 的条数（目标 ≤ 3）
- 伪造拦截 = 注入的伪造 citation 被白名单丢弃的比例（目标 100%）
- P95 = 单条款审查耗时（端到端 run_commercial_review）的 95 分位（目标 < 8000ms）
- 解析成功率 = 解析成功的条款数 / 实际送审条款数（目标 ≥ 95%）

同时产出 eval/stage6-acceptance-2026-10-23.md 对照表报告。
跑真实模型：配置 CLAUSEGUARD_AGENT_LLM_* 环境变量后，把本文件的 MockScriptedChatModel
换成 build_commercial_agent() 默认模型即可复用同一 harness。
"""

from __future__ import annotations

import asyncio
import dataclasses
import math
import time
from dataclasses import dataclass
from pathlib import Path

from app.agents.commercial import build_commercial_agent
from app.agents.validation import ReviewMetrics
from app.graph.nodes.review import run_commercial_review
from app.graph.state import Finding
from tests.agents.fixtures import (
    CONTRACT_META,
    FABRICATED_CLAUSE_IDS,
    all_specs,
    specs_by_id,
    to_clause,
)
from tests.agents.mock_model import MockScriptedChatModel

REPORT_PATH = Path(__file__).resolve().parents[3] / "eval" / "stage6-acceptance-2026-10-23.md"

RECALL_TARGET = 0.7
FALSE_POSITIVE_LIMIT = 3
P95_LIMIT_MS = 8000.0
PARSE_SUCCESS_TARGET = 0.95


@dataclass
class ClauseRow:
    """对照表一行。"""

    clause_id: str
    expected: str
    actual: str
    findings: int
    citations: int
    dropped: int
    input_tokens: int
    output_tokens: int
    duration_ms: float
    verdict: str


@dataclass
class ScenarioResult:
    rows: list[ClauseRow]
    metrics: ReviewMetrics
    findings: list[Finding]


async def _run_scenario(specs: list[object], agent: object) -> ScenarioResult:
    metrics = ReviewMetrics()
    rows: list[ClauseRow] = []
    findings: list[Finding] = []
    for index, spec in enumerate(specs, start=1):
        before = metrics.model_copy()
        started = time.perf_counter()
        outcome = await run_commercial_review(
            [to_clause(spec, index)], CONTRACT_META, agent=agent, metrics=metrics  # type: ignore[arg-type]
        )
        duration_ms = (time.perf_counter() - started) * 1000
        findings.extend(outcome.findings)
        expected = spec.risk_level if spec.expect_finding else "无风险"  # type: ignore[attr-defined]
        actual = (
            "/".join(sorted({f.risk_level for f in outcome.findings}))
            if outcome.findings
            else "无发现"
        )
        hit = bool(outcome.findings) == bool(spec.expect_finding)  # type: ignore[attr-defined]
        rows.append(
            ClauseRow(
                clause_id=spec.clause_id,  # type: ignore[attr-defined]
                expected=expected,
                actual=actual,
                findings=len(outcome.findings),
                citations=sum(len(f.citations) for f in outcome.findings),
                dropped=metrics.dropped_citations - before.dropped_citations,
                input_tokens=metrics.input_tokens - before.input_tokens,
                output_tokens=metrics.output_tokens - before.output_tokens,
                duration_ms=duration_ms,
                verdict="✓" if hit else "✗",
            )
        )
    return ScenarioResult(rows=rows, metrics=metrics, findings=findings)


def _p95(values: list[float]) -> float:
    """最近秩法 95 分位。"""
    ordered = sorted(values)
    rank = max(1, math.ceil(0.95 * len(ordered)))
    return ordered[rank - 1]


def _render_table(rows: list[ClauseRow]) -> str:
    lines = [
        "| clause_id | 预期 | 实际 | findings | 有效引用 | 丢弃 | tokens(in/out) | 耗时ms | 判定 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {row.clause_id} | {row.expected} | {row.actual} | {row.findings} | "
            f"{row.citations} | {row.dropped} | {row.input_tokens}/{row.output_tokens} | "
            f"{row.duration_ms:.1f} | {row.verdict} |"
        )
    return "\n".join(lines)


def _render_report(main: ScenarioResult, fake: ScenarioResult, p95_ms: float) -> str:
    risk_rows = [row for row in main.rows if row.clause_id.startswith("risk-")]
    normal_rows = [row for row in main.rows if row.clause_id.startswith("ok-")]
    recall_hits = sum(1 for row in risk_rows if row.findings > 0)
    false_positives = sum(1 for row in normal_rows if row.findings > 0)
    parse_success = (
        (main.metrics.clauses_reviewed - main.metrics.parse_errors)
        / main.metrics.clauses_reviewed
        if main.metrics.clauses_reviewed
        else 0.0
    )
    fake_ids = sorted(
        {
            citation.source_id
            for finding in fake.findings
            for citation in finding.citations
            if citation.source_id.startswith("FAKE-")
        }
    )
    def verdict(ok: bool) -> str:
        return "✅" if ok else "❌"

    recall_rate = recall_hits / 10
    fake_total = len(FABRICATED_CLAUSE_IDS)
    recall_ok = verdict(recall_rate >= RECALL_TARGET)
    fp_ok = verdict(false_positives <= FALSE_POSITIVE_LIMIT)
    fake_ok = verdict(fake.metrics.dropped_citations == fake_total)
    p95_ok = verdict(p95_ms < P95_LIMIT_MS)
    parse_ok = verdict(parse_success >= PARSE_SUCCESS_TARGET)
    token_cell = f"in={main.metrics.input_tokens} out={main.metrics.output_tokens}"
    return f"""# Stage 6 单专家 Agent 验收报告（商务条款审查）

- 日期：2026-10-23
- 模型：MockScriptedChatModel（CI 确定性替身；真实模型需 CLAUSEGUARD_AGENT_LLM_* 环境变量，
  harness 相同）
- 工具：mock_search_law / mock_search_policy / mock_search_similar_clauses（固定候选集）
- Prompt：app/prompts/commercial/v1.md（版本化文件，非硬编码）

## 指标汇总

| 指标 | 实测 | 目标 | 结论 |
| --- | --- | --- | --- |
| 已知风险召回 | {recall_hits}/10 = {recall_rate:.0%} | ≥ 70% | {recall_ok} |
| 正常条款误报 | {false_positives}/10 | ≤ 3 | {fp_ok} |
| 伪造法条拦截 | {fake.metrics.dropped_citations}/{fake_total} | 100% | {fake_ok} |
| 单条款 P95 耗时 | {p95_ms:.1f}ms | < 8000ms | {p95_ok} |
| 结构化解析成功率 | {parse_success:.0%} | ≥ 95% | {parse_ok} |
| token 消耗（主场景） | {token_cell} | 记日志 | ✅ |

伪造引用泄漏检查：输出中出现的 FAKE id = {fake_ids or '无'}。

## 主场景对照表（10 已知风险 + 10 正常）

{_render_table(main.rows)}

## 伪造法条注入场景（{len(FABRICATED_CLAUSE_IDS)} 条已知风险条款，模型只引用不存在的 id）

{_render_table(fake.rows)}

> 伪造 citation 全部被白名单丢弃（dropped_citations={fake.metrics.dropped_citations}），
> 对应 finding 全部降级为 unverified（{fake.metrics.unverified_findings} 条）——引用只能来自
> 检索结果白名单（红线）在代码层强制成立。

## 说明

1. 召回/误报由 scripted 模型的行为表驱动（模拟合格但不完美的模型：漏报 risk-09/risk-10，
   误报 ok-03/ok-07），指标检验的是整条管线（解析、白名单、降级、指标），不是恒真断言。
2. 解析重试与 parse_error 路径由 tests/agents/test_review_node.py 单独覆盖。
3. 复跑：`cd python-agent && uv run pytest tests/agents/test_acceptance_stage6.py -q`。
"""


def test_stage6_acceptance_metrics_and_report() -> None:
    specs = all_specs()
    model = MockScriptedChatModel(specs=specs_by_id(specs))
    agent = build_commercial_agent(model=model)

    async def _run() -> tuple[ScenarioResult, ScenarioResult]:
        main = await _run_scenario(specs, agent)
        fake_specs = [
            dataclasses.replace(spec, cite_mode="fabricated")
            if spec.clause_id in FABRICATED_CLAUSE_IDS
            else spec
            for spec in specs
            if spec.clause_id in FABRICATED_CLAUSE_IDS
        ]
        fake_model = MockScriptedChatModel(specs=specs_by_id(fake_specs))
        fake_agent = build_commercial_agent(model=fake_model)
        fake = await _run_scenario(fake_specs, fake_agent)
        return main, fake

    main, fake = asyncio.run(_run())

    risk_rows = [row for row in main.rows if row.clause_id.startswith("risk-")]
    normal_rows = [row for row in main.rows if row.clause_id.startswith("ok-")]
    recall_hits = sum(1 for row in risk_rows if row.findings > 0)
    false_positives = sum(1 for row in normal_rows if row.findings > 0)
    durations = [row.duration_ms for row in main.rows]
    p95_ms = _p95(durations)
    parse_success = (
        (main.metrics.clauses_reviewed - main.metrics.parse_errors)
        / main.metrics.clauses_reviewed
    )

    # ---- 验收断言 ----
    assert recall_hits >= 7, f"召回不足: {recall_hits}/10"
    assert false_positives <= FALSE_POSITIVE_LIMIT, f"误报过多: {false_positives}"
    assert fake.metrics.dropped_citations == len(FABRICATED_CLAUSE_IDS)
    assert fake.metrics.unverified_findings == len(FABRICATED_CLAUSE_IDS)
    assert not any(
        citation.source_id.startswith("FAKE-")
        for finding in fake.findings
        for citation in finding.citations
    ), "伪造引用泄漏到输出"
    assert p95_ms < P95_LIMIT_MS, f"P95 超时: {p95_ms:.1f}ms"
    assert parse_success >= PARSE_SUCCESS_TARGET, f"解析成功率不足: {parse_success:.0%}"
    assert main.metrics.input_tokens > 0 and main.metrics.output_tokens > 0

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(_render_report(main, fake, p95_ms), encoding="utf-8")
