"""数据合规审查专家：个人信息保护、数据安全与跨境传输。

范畴与 prompts/data/v1.md 的范围声明一致（两侧由 test_experts.py 保证不漂移）。
"""

from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel

from app.agents.spec import ExpertSpec, build_expert_agent
from app.tools.law import mock_search_law
from app.tools.policy import mock_search_policy

PROMPT_EXPERT = "data"
PROMPT_VERSION = 1
DATA_AGENT_NAME = "data_expert"

# 数据合规范畴：个人信息/数据安全/跨境（低优先级专家：预算超限时跳过）。
SCOPE_KEYWORDS: tuple[str, ...] = (
    "数据",
    "个人信息",
    "隐私",
    "信息保护",
    "网络安全",
    "数据安全",
    "跨境",
    "收集",
    "存储",
    "处理",
    "泄露",
    "脱敏",
    "匿名化",
    "告知",
    "同意",
    "账号",
    "密码",
    "日志",
    "备份",
    "等保",
)

_DataAgent = object


DATA_SPEC = ExpertSpec(
    name=PROMPT_EXPERT,
    agent_name=DATA_AGENT_NAME,
    scope_note="仅数据合规/个人信息保护范畴",
    scope_keywords=SCOPE_KEYWORDS,
    tools=(mock_search_law, mock_search_policy),
    priority="low",
    prompt_version=PROMPT_VERSION,
)


def data_tools() -> list[object]:
    """专家可用的检索工具子集：法规 + 企业数据合规政策。"""
    return list(DATA_SPEC.tools)


def build_data_agent(model: BaseChatModel | None = None) -> object:
    """组装数据合规专家 Agent：prompt 从文件加载，工具走 mock 检索。"""
    return build_expert_agent(DATA_SPEC, model)


def is_data_scope(text: str) -> bool:
    """条款是否属于数据合规范畴；范围外条款不送审。"""
    return DATA_SPEC.in_scope(text)
