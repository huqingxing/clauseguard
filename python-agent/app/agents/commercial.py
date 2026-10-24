"""商务条款审查专家（单 Agent 闭环）。

编排边界：本模块只负责「商务专家」的定义（范畴/工具子集/prompt 版本）与调用入口——
内部用 langchain.create_agent（单体 ReAct 循环 + 检索工具），图级并行与汇总是
Stage 7 的事（app/graph/nodes/expert_review.py）。
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import BaseTool

from app.agents.settings import AgentSettings
from app.agents.spec import ExpertSpec, build_expert_agent, default_chat_model
from app.graph.state import Clause
from app.tools.law import mock_search_law
from app.tools.policy import mock_search_policy
from app.tools.similar import mock_search_similar_clauses

PROMPT_EXPERT = "commercial"
PROMPT_VERSION = 1
COMMERCIAL_AGENT_NAME = "commercial_expert"

# 商务专家范畴：付款/交付/验收/发票四类（与 prompts/commercial/v1.md 的范围声明一致，
# 两侧由 test_prompt.py / test_commercial.py 保证不漂移）。
SCOPE_KEYWORDS: tuple[str, ...] = (
    "付款",
    "支付",
    "价款",
    "货款",
    "预付",
    "预付款",
    "定金",
    "账期",
    "尾款",
    "抵销",
    "交付",
    "交货",
    "运输",
    "验收",
    "检验",
    "异议",
    "发票",
    "开票",
    "税率",
    "品名",
    "payment",
    "invoice",
    "delivery",
)

_CommercialAgent = Any

COMMERCIAL_SPEC = ExpertSpec(
    name=PROMPT_EXPERT,
    agent_name=COMMERCIAL_AGENT_NAME,
    scope_note="仅付款/交付/验收/发票范畴",
    scope_keywords=SCOPE_KEYWORDS,
    tools=(mock_search_law, mock_search_policy, mock_search_similar_clauses),
    priority="high",
    prompt_version=PROMPT_VERSION,
)


def commercial_tools() -> list[BaseTool]:
    """专家可用的检索工具；当前为 mock，接口即真实检索的占位契约。"""
    return list(COMMERCIAL_SPEC.tools)


def build_commercial_agent(model: BaseChatModel | None = None) -> _CommercialAgent:
    """组装商务专家 Agent：prompt 从文件加载（禁止硬编码），工具走 mock 检索。"""
    return build_expert_agent(COMMERCIAL_SPEC, model)


def is_commercial_scope(text: str) -> bool:
    """条款是否属于商务范畴；范围外条款不送审（输入只喂子集，不喂全文）。"""
    return COMMERCIAL_SPEC.in_scope(text)


def build_clause_message(
    clause: Clause,
    contract_meta: dict[str, Any],
    *,
    agent_name: str = COMMERCIAL_AGENT_NAME,
    scope_note: str = COMMERCIAL_SPEC.scope_note,
) -> str:
    """构造单条款审查请求：只含该条款与合同元信息，不含合同全文（任务约束）。

    专家名写入 instruction：既便于审计「哪条请求来自哪个专家」，也让测试替身能据此
    复现按专家分化的行为。
    """
    payload = {
        "contract_meta": contract_meta,
        "clause": {
            "clause_id": clause.clause_id,
            "index": clause.index,
            "text": clause.text,
        },
        "instruction": (
            f"审查该条款（专家：{agent_name}；{scope_note}）：先调用检索工具获取候选依据，"
            "再按系统提示词的输出 JSON Schema 给出结论。"
        ),
    }
    return json.dumps(payload, ensure_ascii=False)


__all__ = [
    "COMMERCIAL_AGENT_NAME",
    "COMMERCIAL_SPEC",
    "PROMPT_EXPERT",
    "PROMPT_VERSION",
    "SCOPE_KEYWORDS",
    "AgentSettings",
    "build_clause_message",
    "build_commercial_agent",
    "commercial_tools",
    "default_chat_model",
    "is_commercial_scope",
]
