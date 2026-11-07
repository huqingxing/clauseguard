"""E2 · 无检索纯识别（测 H3）  与  E3 · 有检索但弱化证据（测 H2）

复用阶段 7 的真 LLM × 30 份对照框架，但用环境变量注入真模型密钥（不改 app/）。
变量设计（对照 baseline = 阶段 7 live：recall 0.2906）：
- E2：移除全部检索工具（tools=()），仅给条款原文 + 19 类风险清单，测模型纯识别。
      判读：≥85% ⇒ H3 不成立（识别没问题，问题在证据链路）；≈30% ⇒ H3 成立；中间值记录。
- E3：保留 live 检索，但系统提示追加「检索仅供参考，不足时仍须给出风险判断并标 unverified」。
      判读：显著高于 0.2906 ⇒ H2 成立（模型是被证据「吓住」不敢报，非识别不了）；持平 ⇒ H2 不成立。

注入方式：子类化 ExpertSpec 覆写 load_system_prompt()，在【内存】中读原 prompt 文件并追加
实验指令，**不写入任何 app/ 文件**。工具在 spec.tools 上用 dataclasses.replace 替换。

环境变量（必须在 import app.* 之前就位，见 eval/stage7/run.py docstring）：
  CLAUSEGUARD_AGENT_LLM_API_KEY / _BASE_URL / _MODEL / _BACKEND=live
  CLAUSEGUARD_RETRIEVAL_MODE=live   （仅 E3 用；E2 不挂检索，该值无关）
  CLAUSEGUARD_RETRIEVAL_PG_DSN=...

无密钥时本脚本优雅退出（exit 2），不静默跑 mock 冒充真模型。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from langgraph.checkpoint.memory import MemorySaver  # noqa: E402

from app.agents.registry import EXPERTS  # noqa: E402
from app.agents.settings import AgentSettings  # noqa: E402
from app.agents.spec import ExpertSpec, build_expert_agent, default_chat_model  # noqa: E402
from app.graph.build import build_graph  # noqa: E402
from app.prompts import load_prompt  # noqa: E402
from eval.runner import load_dataset, run_contract  # noqa: E402
from eval.stage7.metrics import compute_recall  # noqa: E402

OUT_DIR = ROOT / "lab" / "exp" / "attribution" / "out"
DSN = "postgresql://clauseguard@127.0.0.1:55432/clauseguard_retrieval?connect_timeout=2"

E2_INSTRUCTION = (
    "\n\n【实验指令·E2】本次审查不提供任何检索工具，你只能依据条款原文与上方风险清单判断。"
    "请对每一条疑似风险的条款输出其风险类型；即使不能确定具体法条，也要输出风险判断，"
    "citations 留空。不要因为没有检索工具而隐瞒风险。"
)
E3_INSTRUCTION = (
    "\n\n【实验指令·E3】检索结果仅供参考，不一定直接支撑结论。"
    "即使未检索到直接支撑的法条，只要条款原文显含风险特征，也必须输出该风险，"
    "并将引用标为 unverified（citations 留空）。不要因为缺少引用而隐瞒风险。"
)


@dataclass(frozen=True)
class _PromptAugmentedSpec(ExpertSpec):
    """内存中覆写系统提示（追加实验指令），不触碰磁盘上的 prompt 文件。"""

    extra_instruction: str = ""

    def load_system_prompt(self) -> str:  # type: ignore[override]
        return load_prompt(self.name, self.prompt_version) + self.extra_instruction


def _build_augmented_factory(mode: str, model: Any) -> Any:
    """构造与阶段 7 同构的工厂，但按 E2/E3 替换工具与系统提示。"""
    from langchain_core.tools import tool

    if mode == "e2":
        tools = ()  # 无检索
    else:  # e3：保留 live 检索
        from app.tools.retrieval_backend import live_payload

        @tool
        def mock_search_law(query: str) -> str:
            """检索法律法规候选。"""
            return live_payload(query) or json.dumps({"candidates": []}, ensure_ascii=False)

        @tool
        def mock_search_policy(query: str) -> str:
            """检索企业内部制度候选。"""
            return live_payload(query) or json.dumps({"candidates": []}, ensure_ascii=False)

        @tool
        def mock_search_similar_clauses(query: str) -> str:
            """检索历史相似条款候选。"""
            return live_payload(query) or json.dumps({"candidates": []}, ensure_ascii=False)

        tools = (mock_search_law, mock_search_policy, mock_search_similar_clauses)

    instruction = E2_INSTRUCTION if mode == "e2" else E3_INSTRUCTION
    cache: dict[str, Any] = {}

    def factory(spec: Any) -> Any:
        if spec.name not in cache:
            aug = _PromptAugmentedSpec(
                name=spec.name,
                agent_name=spec.agent_name,
                scope_note=spec.scope_note,
                scope_keywords=spec.scope_keywords,
                tools=tools,  # type: ignore[arg-type]
                priority=spec.priority,
                prompt_version=spec.prompt_version,
                extra_instruction=instruction,
            )
            cache[spec.name] = build_expert_agent(aug, model)
        return cache[spec.name]

    return factory


def _configure_env(mode: str) -> None:
    os.environ["CLAUSEGUARD_AGENT_MODEL_BACKEND"] = "live"
    os.environ.setdefault(
        "CLAUSEGUARD_AGENT_LLM_BASE_URL",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    os.environ.setdefault("CLAUSEGUARD_AGENT_LLM_MODEL", "qwen-plus")
    os.environ["HTTP_PROXY"] = ""
    os.environ["HTTPS_PROXY"] = ""
    os.environ["NO_PROXY"] = "localhost,127.0.0.1,dashscope.aliyuncs.com"
    if mode == "e3":
        os.environ["CLAUSEGUARD_RETRIEVAL_MODE"] = "live"
        os.environ["CLAUSEGUARD_RETRIEVAL_PG_DSN"] = DSN


async def _run_batch(records: list[dict[str, Any]], factory: Any, concurrency: int):
    graph = build_graph(AgentSettings(execution_mode="parallel"), checkpointer=MemorySaver())
    sem = asyncio.Semaphore(concurrency)

    async def bounded(rec):
        async with sem:
            res = await run_contract(rec, graph, factory)
            return json.loads(json.dumps(res, default=lambda o: o.__dict__ if hasattr(o, "__dict__") else str(o)))

    return list(await asyncio.gather(*(bounded(r) for r in records)))


def _count_unverified(results: list[dict[str, Any]]) -> int:
    n = 0
    for r in results:
        for f in r.get("findings", []):
            if f.get("stale"):
                continue
            if (f.get("risk_level") == "unverified") or not (f.get("citations") or []):
                n += 1
    return n


def _preflight_llm() -> bool:
    """跑批前单发一次 chat 探测；返回是否可达（200）。不可达时明确报 UNREACHABLE。"""
    import httpx

    base = os.environ.get("CLAUSEGUARD_AGENT_LLM_BASE_URL") or "https://dashscope.aliyuncs.com/compatible-mode/v1"
    model = os.environ.get("CLAUSEGUARD_AGENT_LLM_MODEL") or "qwen-plus"
    key = os.environ.get("CLAUSEGUARD_AGENT_LLM_API_KEY") or os.environ.get("LLM_API_KEY", "")
    if not key:
        return False
    try:
        r = httpx.post(
            base.rstrip("/") + "/chat/completions",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
            json={"model": model, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 5},
            timeout=30,
        )
        return r.status_code == 200
    except Exception:  # noqa: BLE001
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("e2", "e3"), required=True)
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--concurrency", type=int, default=2)
    args = ap.parse_args()

    key = os.environ.get("CLAUSEGUARD_AGENT_LLM_API_KEY") or os.environ.get("LLM_API_KEY", "")
    if not key:
        print("[E2/E3] BLOCKED: 未配置 LLM_API_KEY / CLAUSEGUARD_AGENT_LLM_API_KEY，无法跑真模型。"
              " 在部署环境配置密钥后重跑本脚本。")
        return 2
    if not _preflight_llm():
        print("[E2/E3] UNREACHABLE: 真模型 chat 探测非 200（本沙箱 DashScope 返回 400，"
              "与验收期 embeddings 400 同源）。E2/E3 无法在本环境执行；"
              "在可达环境配置密钥后重跑本脚本即可。")
        return 3
    _configure_env(args.mode)

    records = load_dataset()[: args.n]
    model = default_chat_model()
    factory = _build_augmented_factory(args.mode, model)
    results = asyncio.run(_run_batch(records, factory, args.concurrency))
    recall = compute_recall(records, results)
    unverified = _count_unverified(results)
    out_dir = OUT_DIR / args.mode
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in results), encoding="utf-8"
    )
    metrics = {
        "mode": args.mode,
        "baseline_live_recall": 0.2906,
        "recall": recall,
        "unverified_findings": unverified,
        "contracts": args.n,
    }
    (out_dir / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    logging.basicConfig(level=logging.WARNING)
    print(f"[E{args.mode[-1]}] recall={recall['recall']} tp={recall['tp']}/{recall['gold']} "
          f"findings={recall['findings']} unverified={unverified} -> {out_dir/'metrics.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
