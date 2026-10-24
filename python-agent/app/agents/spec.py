"""专家 Agent 的统一定义与组装（6 个专家共用）。

为什么抽公共层：每个专家只有三处差异——prompts/<name>/v1.md、检索工具子集、范畴关键词；
组装逻辑（create_agent + 文件 prompt + 命名）完全同构，收敛到此处避免 6 份复制漂移。
专家优先级用于预算控制：high 为必审专家，low（ip/data/compliance）在预算超限时跳过。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal

from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.graph.state import CompiledStateGraph

from app.agents.settings import AgentSettings, get_agent_settings
from app.clients.openai_compat import OpenAICompatChatModel
from app.prompts import load_prompt

logger = logging.getLogger("app.agents.spec")

Priority = Literal["high", "low"]

_Agent = CompiledStateGraph[Any, Any, Any, Any]


@dataclass(frozen=True)
class ExpertSpec:
    """单个专家的全部静态定义；运行态不进来，保证可缓存、可复现。"""

    name: str
    agent_name: str
    scope_note: str
    scope_keywords: tuple[str, ...]
    tools: tuple[BaseTool, ...] = field(default_factory=tuple)
    priority: Priority = "high"
    prompt_version: int = 1

    def in_scope(self, text: str) -> bool:
        """条款是否属于本专家范畴；范围外条款不送审（输入只喂子集，不喂全文）。"""
        lowered = text.lower()
        return any(
            keyword in text or keyword in lowered for keyword in self.scope_keywords
        )

    def load_system_prompt(self) -> str:
        """系统提示词只从版本化文件读取（红线：禁止硬编码在 py 里）。"""
        return load_prompt(self.name, self.prompt_version)

    def load_retry_hint(self) -> str:
        """重试纠正指令，与主 prompt 同目录同版本管理。"""
        return load_prompt(self.name, self.prompt_version, suffix="retry")


def default_chat_model(settings: AgentSettings | None = None) -> OpenAICompatChatModel:
    """按配置构造 OpenAI 兼容模型；模型来源唯一入口，节点不直接 new。"""
    resolved = settings or get_agent_settings()
    return OpenAICompatChatModel(
        base_url=resolved.llm_base_url,
        api_key=resolved.llm_api_key,
        model=resolved.llm_model,
        temperature=resolved.llm_temperature,
        max_tokens=resolved.llm_max_tokens,
        timeout_s=resolved.llm_timeout_s,
    )


def build_expert_agent(
    spec: ExpertSpec, model: BaseChatModel | None = None
) -> _Agent:
    """按专家定义组装单体 ReAct Agent：文件 prompt + 该专家的工具子集。"""
    return create_agent(
        model or default_chat_model(),
        tools=list(spec.tools),
        system_prompt=spec.load_system_prompt(),
        name=spec.agent_name,
    )
