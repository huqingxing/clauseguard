"""主体资质审查专家：签约主体资格、授权与签署要素。

范畴与 prompts/subject/v1.md 的范围声明一致（两侧由 test_experts.py 保证不漂移）。
"""

from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel

from app.agents.spec import ExpertSpec, build_expert_agent
from app.tools.law import mock_search_law
from app.tools.similar import mock_search_similar_clauses

PROMPT_EXPERT = "subject"
PROMPT_VERSION = 1
SUBJECT_AGENT_NAME = "subject_expert"

# 主体资质范畴：资格/授权/签署要素/登记备案（不含具体商事权利义务）。
SCOPE_KEYWORDS: tuple[str, ...] = (
    "主体",
    "资格",
    "资质",
    "营业执照",
    "许可",
    "授权",
    "法定代表",
    "代表人",
    "代理",
    "签章",
    "盖章",
    "名称",
    "地址",
    "联系方式",
    "登记",
    "备案",
    "经营范围",
    "分立",
    "合并",
    "承继",
    "生效",
)

_SubjectAgent = object  # create_agent 返回 CompiledStateGraph，泛型参数在调用处标注

SUBJECT_SPEC = ExpertSpec(
    name=PROMPT_EXPERT,
    agent_name=SUBJECT_AGENT_NAME,
    scope_note="仅主体资格/资质/授权/签署要素范畴",
    scope_keywords=SCOPE_KEYWORDS,
    tools=(mock_search_law, mock_search_similar_clauses),
    priority="high",
    prompt_version=PROMPT_VERSION,
)


def subject_tools() -> list[object]:
    """专家可用的检索工具子集：法规 + 历史相似合同（资质核查看先例）。"""
    return list(SUBJECT_SPEC.tools)


def build_subject_agent(model: BaseChatModel | None = None) -> object:
    """组装主体资质专家 Agent：prompt 从文件加载，工具走 mock 检索。"""
    return build_expert_agent(SUBJECT_SPEC, model)


def is_subject_scope(text: str) -> bool:
    """条款是否属于主体资质范畴；范围外条款不送审。"""
    return SUBJECT_SPEC.in_scope(text)
