"""违约责任审查专家：违约救济、赔偿计算、免责与合同解除。

范畴与 prompts/liability/v1.md 的范围声明一致（两侧由 test_experts.py 保证不漂移）。
"""

from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel

from app.agents.spec import ExpertSpec, build_expert_agent
from app.tools.law import mock_search_law
from app.tools.similar import mock_search_similar_clauses

PROMPT_EXPERT = "liability"
PROMPT_VERSION = 1
LIABILITY_AGENT_NAME = "liability_expert"

# 违约责任的范畴：违约/赔偿/免责/解除/不可抗力（不含商务履行细节，那归商务专家）。
SCOPE_KEYWORDS: tuple[str, ...] = (
    "违约",
    "赔偿责任",
    "赔偿损失",
    "损失",
    "罚款",
    "滞纳",
    "定金",
    "罚则",
    "责任上限",
    "不可抗力",
    "免责",
    "解除合同",
    "终止合同",
    "继续履行",
    "补救措施",
    "没收",
)

_LiabilityAgent = object


LIABILITY_SPEC = ExpertSpec(
    name=PROMPT_EXPERT,
    agent_name=LIABILITY_AGENT_NAME,
    scope_note="仅违约责任/赔偿/免责/解除范畴",
    scope_keywords=SCOPE_KEYWORDS,
    tools=(mock_search_law, mock_search_similar_clauses),
    priority="high",
    prompt_version=PROMPT_VERSION,
)


def liability_tools() -> list[object]:
    """专家可用的检索工具子集：法规 + 历史相似合同（违约金水平看先例）。"""
    return list(LIABILITY_SPEC.tools)


def build_liability_agent(model: BaseChatModel | None = None) -> object:
    """组装违约责任专家 Agent：prompt 从文件加载，工具走 mock 检索。"""
    return build_expert_agent(LIABILITY_SPEC, model)


def is_liability_scope(text: str) -> bool:
    """条款是否属于违约责任范畴；范围外条款不送审。"""
    return LIABILITY_SPEC.in_scope(text)
