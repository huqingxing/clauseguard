"""知识产权审查专家：权属、侵权风险、保密与许可限制。

范畴与 prompts/ip/v1.md 的范围声明一致（两侧由 test_experts.py 保证不漂移）。
"""

from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel

from app.agents.spec import ExpertSpec, build_expert_agent
from app.tools.law import mock_search_law
from app.tools.policy import mock_search_policy

PROMPT_EXPERT = "ip"
PROMPT_VERSION = 1
IP_AGENT_NAME = "ip_expert"

# 知识产权范畴：权属/侵权/保密/许可（低优先级专家：预算超限时跳过）。
SCOPE_KEYWORDS: tuple[str, ...] = (
    "知识产权",
    "专利",
    "商标",
    "著作权",
    "版权",
    "侵权",
    "商业秘密",
    "技术秘密",
    "know-how",
    "Know-How",
    "许可",
    "权属",
    "归属",
    "改进",
    "职务成果",
    "开源",
    "源代码",
    "技术资料",
    "保密义务",
    "保密条款",
)

_IpAgent = object


IP_SPEC = ExpertSpec(
    name=PROMPT_EXPERT,
    agent_name=IP_AGENT_NAME,
    scope_note="仅知识产权/保密/许可范畴",
    scope_keywords=SCOPE_KEYWORDS,
    tools=(mock_search_law, mock_search_policy),
    priority="low",
    prompt_version=PROMPT_VERSION,
)


def ip_tools() -> list[object]:
    """专家可用的检索工具子集：法规 + 企业内部 IP 政策。"""
    return list(IP_SPEC.tools)


def build_ip_agent(model: BaseChatModel | None = None) -> object:
    """组装知识产权专家 Agent：prompt 从文件加载，工具走 mock 检索。"""
    return build_expert_agent(IP_SPEC, model)


def is_ip_scope(text: str) -> bool:
    """条款是否属于知识产权范畴；范围外条款不送审。"""
    return IP_SPEC.in_scope(text)
