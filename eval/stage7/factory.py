"""阶段 7 的 Agent 工厂：真模型固定不变，唯一变量是检索工具返回什么。

为什么不改 eval/examiner.py：那里的 EvalExaminer 是 CI 确定性基线的唯一入口，
替换它会让「同一 commit 两次跑指标逐位一致」的既有验收失真。本次是一次性对照实验，
用独立工厂把「模型」这个控制变量钉死，跑完不污染基线。

工具名必须与生产 prompt 里的 `mock_search_law` / `mock_search_policy` /
`mock_search_similar_clauses` 一致：真模型是按名字发起 function call 的，
名字对不上会导致工具调用失败、白名单为空、findings 全被降级。
"""

from __future__ import annotations

import json
from typing import Any


def _mock_candidates(query: str) -> str:
    """改造前：评测检索池，返回风险目录里 22 条固定候选，与 query 无关。"""
    from eval.examiner import mock_search_eval_law

    return mock_search_eval_law(query)


def _live_candidates(query: str) -> str:
    """改造后：阶段 6 接好的 pgvector 真实检索，候选 id = chunk_id。"""
    from app.tools.retrieval_backend import live_payload

    # live_payload 在检索后端不可用时返回空候选集而不是抛异常，这正是生产降级语义；
    # 返回 None 只可能是配置被改成 mock，此时同样给空集，避免悄悄混进假 ID。
    return live_payload(query) or json.dumps({"candidates": []}, ensure_ascii=False)


def build_tools(mode: str) -> tuple[Any, Any, Any]:
    """按 mode 造三个同构工具；三个名字都取自生产 prompt，模型无需感知差别。"""
    from langchain_core.tools import tool

    if mode not in ("mock", "live"):
        raise ValueError(f"unknown retrieval mode: {mode}")
    payload = _mock_candidates if mode == "mock" else _live_candidates

    @tool
    def mock_search_law(query: str) -> str:
        """检索法律法规候选，返回 JSON {"candidates":[{id,article,title,text}]}。"""
        return payload(query)

    @tool
    def mock_search_policy(query: str) -> str:
        """检索企业内部制度候选，返回 JSON {"candidates":[{id,article,title,text}]}。"""
        return payload(query)

    @tool
    def mock_search_similar_clauses(query: str) -> str:
        """检索历史相似条款候选，返回 JSON {"candidates":[{id,article,title,text}]}。"""
        return payload(query)

    return mock_search_law, mock_search_policy, mock_search_similar_clauses


def build_stage7_agent_factory(mode: str, model: Any | None = None) -> Any:
    """每专家一个 Agent 实例（缓存），模型全场共用同一个真模型实例。"""
    import dataclasses

    from app.agents.spec import build_expert_agent, default_chat_model

    tools = build_tools(mode)
    base_model = model or default_chat_model()
    cache: dict[str, Any] = {}

    def factory(spec: Any) -> Any:
        if spec.name not in cache:
            cache[spec.name] = build_expert_agent(
                dataclasses.replace(spec, tools=tools), base_model
            )
        return cache[spec.name]

    return factory
