"""Stage 13 评测 runner：批量跑真实审查管线（并发 3），sqlite 落库每次结果。

为什么直接建图而不是走 HTTP：评测要的是管线行为的可复现度量，HTTP 层（Java 转发、
SSE 序列化）不属于被测对象；但图本身是生产 build_graph + 生产 prompt + 生产切分器，
examiner 只是模型替身（换工厂即真模型，见 examiner.py）。

并发语义：合同间并发 3（任务口径），合同内并发沿用生产 limiter(5)；两者叠加后
单机在途模型调用上限 15，全部为 0 网络的确定性替身，无速率风险。
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app.agents.settings import AgentSettings
from app.events import EventEmitter
from app.graph.build import build_graph
from app.graph.state import Budget, Clause, ReviewState, ReviewTask
from app.graph.threads import review_thread_id
from app.parse.loader import load_document
from app.parse.splitter import split_document
from eval.examiner import build_eval_agent_factory

REPO_ROOT = Path(__file__).resolve().parents[1]
DATASET_JSONL = REPO_ROOT / "eval" / "dataset" / "dataset.jsonl"
OUT_DIR = REPO_ROOT / "eval" / "out"
RESULTS_DB = OUT_DIR / "results.db"

# 合同间并发（任务书口径）；合同内沿用生产 CONCURRENCY_LIMIT=5。
CONTRACT_CONCURRENCY = 3
# 每次模型调用的固定延迟（0.8s，接近真实 LLM 单轮）：让 P50/P95 由 sleep 主导，
# 图执行/序列化的固定开销占比压到 ~10%，两次运行分位数漂移 < 1%（验收口径）。
EXAMINER_LATENCY_S = 0.8


@dataclass
class ContractResult:
    """单份合同的评测产出：findings 序列化 + 定位/时延/成本。"""

    contract_id: str
    findings: list[dict[str, Any]] = field(default_factory=list)
    clause_spans: dict[str, list[int]] = field(default_factory=dict)
    duration_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_cny: float = 0.0
    degraded: bool = False
    events_count: int = 0


def load_dataset() -> list[dict[str, Any]]:
    """读 dataset.jsonl；缺失即显式失败（生成是 eval.run 的前置步骤）。"""
    if not DATASET_JSONL.exists():
        raise FileNotFoundError(f"数据集不存在: {DATASET_JSONL}（先运行 eval.dataset.generate）")
    lines = DATASET_JSONL.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _initial_state(contract_id: str, clauses: list[Clause]) -> ReviewState:
    """与生产 _make_initial_state 同构的初始态（评测直接注入 clauses，跳过下载）。"""
    return {
        "contract_id": contract_id,
        "version": 1,
        "clauses": clauses,
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


def _serialize_finding(finding: Any) -> dict[str, Any]:
    """终态 Finding → 可比对字典（指标只消费这些字段）。"""
    return {
        "clause_id": finding.clause_id,
        "agent": finding.agent,
        "risk_type": finding.risk_type,
        "risk_level": finding.risk_level,
        "summary": finding.summary,
        "stale": bool(finding.stale),
        "citations": [
            {"source_id": c.source_id, "title": c.title} for c in finding.citations
        ],
    }


async def run_contract(
    record: dict[str, Any],
    graph: Any,
    agent_factory: Any,
) -> ContractResult:
    """跑一份合同：解析 → 建态 → 图执行 →（高风险断点）自动 resume → 收集。"""
    contract_id = str(record["contract_id"])
    path = REPO_ROOT / str(record["path"])
    document = load_document(path)
    parsed = split_document(document)

    clauses: list[Clause] = []
    spans: dict[str, list[int]] = {}
    for index, clause in enumerate(parsed.clauses, start=1):
        clauses.append(
            Clause(
                clause_id=clause.id,
                index=index,
                text=clause.text,
                no=clause.no,
                hash=clause.hash,
            )
        )
        spans[clause.id] = [clause.start, clause.end]

    emitter = EventEmitter()
    config = {
        "configurable": {
            # ADR-0006：与生产链路共用唯一构造入口，评测每一次 run 也是独立 thread。
            "thread_id": review_thread_id(f"eval-{contract_id}"),
            "emitter": emitter,
            "limiter": asyncio.Semaphore(5),
            "agent_factory": agent_factory,
        }
    }
    # 计时只覆盖图执行：docx 解析属于评测脚手架的喂数据准备（生产里解析在 ingest
    # 节点内、经 HTTP 取文件），计入窗口会让 CPU 争用噪声污染时延指标的可复现性。
    started = time.perf_counter()
    result = await graph.ainvoke(_initial_state(contract_id, clauses), config)
    if isinstance(result, dict) and result.get("__interrupt__"):
        # 评测不校验审批语义：高风险断点用空审批数组 resume 越过（findings 保留），
        # 与 Stage 7/9 验收同一口径。
        result = await graph.ainvoke(Command(resume=[]), config)
    duration_ms = int((time.perf_counter() - started) * 1000)

    budget = result.get("budget")
    events_count = 0
    while not emitter.queue.empty():
        emitter.queue.get_nowait()
        events_count += 1
    return ContractResult(
        contract_id=contract_id,
        findings=[_serialize_finding(f) for f in result.get("findings", [])],
        clause_spans=spans,
        duration_ms=duration_ms,
        input_tokens=int(budget.spent_input_tokens) if budget else 0,
        output_tokens=int(budget.spent_output_tokens) if budget else 0,
        cost_cny=float(budget.spent_cost_cny) if budget else 0.0,
        degraded=bool(result.get("degraded")),
        events_count=events_count,
    )


async def run_all(
    records: list[dict[str, Any]],
    concurrency: int = CONTRACT_CONCURRENCY,
) -> list[ContractResult]:
    """并发 3 跑全部合同；图与 agent 工厂全局一份（thread_id 隔离运行态）。"""
    graph = build_graph(
        AgentSettings(execution_mode="parallel"),  # type: ignore[arg-type]
        checkpointer=MemorySaver(),
    )
    agent_factory = build_eval_agent_factory(latency_s=EXAMINER_LATENCY_S)
    semaphore = asyncio.Semaphore(concurrency)

    async def _bounded(record: dict[str, Any]) -> ContractResult:
        async with semaphore:
            return await run_contract(record, graph, agent_factory)

    return list(await asyncio.gather(*(_bounded(r) for r in records)))


def persist_results(
    results: list[ContractResult], label: str, db_path: Path = RESULTS_DB
) -> None:
    """每次结果落 sqlite（任务口径）：按 label 追加，供跨 run 审计与回归对比。"""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS contract_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label TEXT NOT NULL,
                contract_id TEXT NOT NULL,
                duration_ms INTEGER NOT NULL,
                input_tokens INTEGER NOT NULL,
                output_tokens INTEGER NOT NULL,
                cost_cny REAL NOT NULL,
                findings_count INTEGER NOT NULL,
                degraded INTEGER NOT NULL,
                findings_json TEXT NOT NULL,
                clause_spans_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO contract_runs (
                label, contract_id, duration_ms, input_tokens, output_tokens,
                cost_cny, findings_count, degraded, findings_json, clause_spans_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    label,
                    r.contract_id,
                    r.duration_ms,
                    r.input_tokens,
                    r.output_tokens,
                    r.cost_cny,
                    len(r.findings),
                    int(r.degraded),
                    json.dumps(r.findings, ensure_ascii=False),
                    json.dumps(r.clause_spans),
                )
                for r in results
            ],
        )
