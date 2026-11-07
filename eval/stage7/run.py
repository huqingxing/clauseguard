"""阶段 7 跑批：30 份子集 × 两组检索源，唯一变量是检索工具。

控制变量：两组用同一个真模型（qwen-plus / DashScope compatible-mode）、同一份
数据集、同一张图、同一套 prompt、同一份金标。只有检索工具返回的候选不同：
mock 组 = 22 条固定样例（src-*），live 组 = pgvector 真实法条（chunk_id）。

环境变量必须在 import app.* 之前设置：AgentSettings 是 lru_cache 单例，
晚设一个字符就会让整组跑到默认 openai.com 上，且日志里看不出异常。

复现（仓库根执行）：
  PYTHONPATH=python-agent:. python-agent/.venv/Scripts/python.exe -m eval.stage7.run --mode mock --n 30
  PYTHONPATH=python-agent:. python-agent/.venv/Scripts/python.exe -m eval.stage7.run --mode live --n 30
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import subprocess
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "eval" / "out" / "stage7"
DSN = "postgresql://clauseguard@127.0.0.1:55432/clauseguard_retrieval?connect_timeout=2"
_DROPPED_RE = re.compile(r"dropped_citations=(\d+)")


def configure_env(mode: str) -> None:
    """把真模型与检索源钉死；顺序敏感，见模块 docstring。"""
    os.environ["CLAUSEGUARD_AGENT_MODEL_BACKEND"] = "live"
    os.environ.setdefault(
        "CLAUSEGUARD_AGENT_LLM_BASE_URL",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    os.environ.setdefault("CLAUSEGUARD_AGENT_LLM_MODEL", "qwen-plus")
    # 本机系统代理会把百炼请求绕进 502；只清代理变量，不动用户全局配置。
    os.environ["HTTP_PROXY"] = ""
    os.environ["HTTPS_PROXY"] = ""
    os.environ["NO_PROXY"] = "localhost,127.0.0.1,dashscope.aliyuncs.com"
    os.environ["CLAUSEGUARD_RETRIEVAL_MODE"] = mode
    os.environ["CLAUSEGUARD_RETRIEVAL_PG_DSN"] = DSN


class _LogCounter(logging.Handler):
    """把「为什么召回率高/低」的证据留下来：解析失败、模型报错、引用被丢弃次数。

    只看终态数字无法区分「模型没检出风险」和「模型调 API 被限流」，报告必须能解释。
    """

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.counts: dict[str, int] = {
            "parse_failures": 0,
            "clauses_parse_failed": 0,
            "llm_errors": 0,
            "evidence_mismatches": 0,
            "dropped_citations": 0,
        }

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if "输出解析失败" in message:
            self.counts["parse_failures"] += 1
        if "连续" in message and "解析失败" in message:
            self.counts["clauses_parse_failed"] += 1
        if "Agent 调用失败" in message:
            self.counts["llm_errors"] += 1
        if "evidence 非条款原文" in message:
            self.counts["evidence_mismatches"] += 1
        match = _DROPPED_RE.search(message)
        if match:
            self.counts["dropped_citations"] += int(match.group(1))


def _commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, OSError):
        return "unknown"


async def _run_batch(
    records: list[dict[str, Any]], mode: str, concurrency: int
) -> list[dict[str, Any]]:
    from app.agents.settings import AgentSettings
    from app.graph.build import build_graph
    from langgraph.checkpoint.memory import MemorySaver

    from eval.runner import run_contract
    from eval.stage7.factory import build_stage7_agent_factory

    graph = build_graph(
        AgentSettings(execution_mode="parallel"), checkpointer=MemorySaver()
    )
    factory = build_stage7_agent_factory(mode)
    semaphore = asyncio.Semaphore(concurrency)

    async def bounded(record: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            result = await run_contract(record, graph, factory)
            return asdict(result)

    return list(await asyncio.gather(*(bounded(record) for record in records)))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="阶段 7：真模型 × 30 份 × mock/live 检索对照"
    )
    parser.add_argument("--mode", choices=("mock", "live"), required=True)
    parser.add_argument(
        "--n", type=int, default=30, help="子集份数（固定取数据集前 N 份）"
    )
    parser.add_argument(
        "--concurrency", type=int, default=2, help="合同间并发；合同内固定 5"
    )
    parser.add_argument(
        "--label", type=str, default="", help="输出子目录名，缺省用 mode"
    )
    args = parser.parse_args()

    configure_env(args.mode)

    from eval.runner import load_dataset
    from eval.stage7.metrics import compute_recall

    records = load_dataset()[: args.n]
    out_dir = OUT_DIR / (args.label or args.mode)
    out_dir.mkdir(parents=True, exist_ok=True)

    counter = _LogCounter()
    review_logger = logging.getLogger("app.graph.nodes.review")
    review_logger.addHandler(counter)
    review_logger.setLevel(logging.WARNING)
    started = time.perf_counter()
    try:
        results = asyncio.run(_run_batch(records, args.mode, args.concurrency))
    finally:
        review_logger.removeHandler(counter)
    wall_s = round(time.perf_counter() - started, 2)

    with (out_dir / "results.jsonl").open("w", encoding="utf-8") as handle:
        for result in results:
            handle.write(json.dumps(result, ensure_ascii=False) + "\n")

    recall = compute_recall(records, results)
    metrics = {
        "mode": args.mode,
        "meta": {
            "model": os.environ["CLAUSEGUARD_AGENT_LLM_MODEL"],
            "base_url": os.environ["CLAUSEGUARD_AGENT_LLM_BASE_URL"],
            "model_backend": "live",
            "retrieval_mode": args.mode,
            "contracts": args.n,
            "concurrency": args.concurrency,
            "commit": _commit(),
            "wall_s": wall_s,
        },
        "recall": recall,
        "diagnostics": counter.counts,
        "tokens": {
            "input": sum(int(r["input_tokens"]) for r in results),
            "output": sum(int(r["output_tokens"]) for r in results),
        },
        "cost_cny": round(sum(float(r["cost_cny"]) for r in results), 4),
        "duration_ms": {
            "mean": round(sum(int(r["duration_ms"]) for r in results) / len(results)),
            "max": max(int(r["duration_ms"]) for r in results),
        },
        "degraded_contracts": sum(1 for r in results if r["degraded"]),
    }
    (out_dir / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"[stage7] mode={args.mode} contracts={len(results)} "
        f"recall={recall['recall']} tp={recall['tp']}/{recall['gold']} "
        f"fp={recall['fp']} findings={recall['findings']} "
        f"parse_failures={counter.counts['parse_failures']} "
        f"llm_errors={counter.counts['llm_errors']} "
        f"tokens={metrics['tokens']['input']}+{metrics['tokens']['output']} "
        f"cost_cny={metrics['cost_cny']} wall_s={wall_s}"
    )
    print(f"[stage7] -> {out_dir / 'metrics.json'}")


if __name__ == "__main__":
    main()
