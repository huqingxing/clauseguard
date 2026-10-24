"""争议解决与合规审查专家：争议解决机制、适用法律与合规红线。

范畴与 prompts/compliance/v1.md 的范围声明一致（两侧由 test_experts.py 保证不漂移）。
"""

from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel

from app.agents.spec import ExpertSpec, build_expert_agent
from app.tools.law import mock_search_law
from app.tools.policy import mock_search_policy
from app.tools.similar import mock_search_similar_clauses

PROMPT_EXPERT = "compliance"
PROMPT_VERSION = 1
COMPLIANCE_AGENT_NAME = "compliance_expert"

# 争议解决/合规范畴：争议解决机制/适用法律/合规红线（低优先级专家：预算超限时跳过）。
SCOPE_KEYWORDS: tuple[str, ...] = (
    "争议",
    "仲裁",
    "诉讼",
    "管辖",
    "法院",
    "适用法律",
    "法律适用",
    "合规",
    "反腐败",
    "反垄断",
    "不正当竞争",
    "制裁",
    "出口管制",
    "廉洁",
    "阳光",
    "审计",
    "监管",
    "行政处罚",
    "行政复议",
)

_ComplianceAgent = object


COMPLIANCE_SPEC = ExpertSpec(
    name=PROMPT_EXPERT,
    agent_name=COMPLIANCE_AGENT_NAME,
    scope_note="仅争议解决/适用法律/合规红线范畴",
    scope_keywords=SCOPE_KEYWORDS,
    tools=(mock_search_law, mock_search_policy, mock_search_similar_clauses),
    priority="low",
    prompt_version=PROMPT_VERSION,
)


def compliance_tools() -> list[object]:
    """专家可用的检索工具子集：法规 + 合规政策 + 历史相似合同。"""
    return list(COMPLIANCE_SPEC.tools)


def build_compliance_agent(model: BaseChatModel | None = None) -> object:
    """组装争议解决/合规专家 Agent：prompt 从文件加载，工具走 mock 检索。"""
    return build_expert_agent(COMPLIANCE_SPEC, model)


def is_compliance_scope(text: str) -> bool:
    """条款是否属于争议解决/合规范畴；范围外条款不送审。"""
    return COMPLIANCE_SPEC.in_scope(text)
