"""Stage 13 评测用确定性 examiner：模拟「读了 prompt 的合规模型」的两段式行为。

为什么不用真模型跑基线：验收要求「同一 commit 跑两次指标差异 < 1%」，真模型
采样非确定且烧钱；基线用本替身锁定管线的确定性指标（召回/误报/引用/定位），
真模型跑法保留在 agent_factory 接口上（换工厂即可，管线零改动）。

与生产链路的真实耦合点（不是纯 mock 的关键）：
1. 系统提示词来自真实 prompts/<expert>/v1.md —— examiner 只检测「prompt 词表中
   出现的风险类型标签」。改 prompt（删/加一个标签）→ examiner 行为随之变化 →
   报告指标涨跌。这是「改一处 prompt 报告能体现涨跌」的机制面。
2. 两段式行为与合规模型一致：先对已绑定工具发起检索调用，拿到 ToolMessage 后
   按输出 JSON Schema 给结论；citation 的 source_id 取自工具返回候选。
3. 确定性「缺陷」是刻意注入的评测信号：
   - 陷阱条款（关键词命中但语义安全）会被误报 —— 误报率指标非平凡；
   - fragile 风险类型引用「相邻但错误」的来源（仍在检索池内，能过白名单校验，
     但与金标不符）—— 引用准确率指标 < 100%。

TODO(Stage 14+): 接真实模型跑分（CLAUSEGUARD_EVAL_LIVE=1 + default_chat_model 工厂），
本替身仅用于 CI 可复现基线。
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from eval.dataset.risk_catalog import (
    SOURCES,
    RiskDef,
    risk_defs_for_expert,
    source_for,
)

_CLAUSE_ID_RE = re.compile(r'"clause_id"\s*:\s*"([^"]+)"')
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)

# 与 mock_model 相同的固定 usage：token 记账路径在评测里被真实走到且完全确定。
_MOCK_USAGE: dict[str, int] = {"input_tokens": 150, "output_tokens": 60, "total_tokens": 210}

# evidence 截取窗口：关键词前后各 8 字符，保证是条款原文的逐字 substring。
_EVIDENCE_WINDOW = 8


def mock_search_eval_law(query: str) -> str:
    """评测检索工具（mock）：返回风险目录的固定候选池。

    接口与生产 mock_search_law 完全一致（(query) -> {"candidates": [...]}），
    候选字段 id/article/title/text/url 同构，引用白名单校验无需感知差异。
    TODO(Stage 14+): 替换为 pgvector 真实检索后的同一接口。
    """
    items = []
    for source_id, meta in SOURCES.items():
        items.append(
            {
                "id": source_id,
                "article": meta["article"],
                "title": meta["title"],
                "text": meta["excerpt"],
                "url": meta["url"],
            }
        )
    return json.dumps({"candidates": items}, ensure_ascii=False)


class EvalExaminer(BaseChatModel):
    """按风险目录 + prompt 词表确定性应答的模型替身。

    字段 risks 为该专家的全部启用风险定义（keyed by risk_type）；实际生效子集
    由系统提示词文本决定 —— prompt 词表里没有的标签不产出。
    """

    risks: dict[str, RiskDef] = {}
    latency_s: float = 0.0
    call_count: int = 0
    bound_tools: tuple[str, ...] = ()
    last_system_prompt: str = ""

    @property
    def _llm_type(self) -> str:
        return "eval-examiner"

    def bind_tools(
        self,
        tools: Any,
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Any:
        # 替身不需要工具 schema：记住绑定子集即可（两段式先调已绑工具）。
        self.bound_tools = tuple(
            getattr(tool, "name", str(tool)) for tool in (tools or ())
        )
        return self.bind()

    # ---- langchain 入口 ----

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.call_count += 1
        return self._respond(messages)

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.call_count += 1
        if self.latency_s > 0:
            # 固定延迟占主导：P95 时延可复现（调度噪声占比压到 <1%）。
            await asyncio.sleep(self.latency_s)
        return self._respond(messages)

    # ---- 行为实现 ----

    def _respond(self, messages: list[BaseMessage]) -> ChatResult:
        request = self._parse_request(messages)
        if request is None:
            return self._result(AIMessage(content='{"clause_id":"","findings":[]}'))
        clause_id, clause_text = request
        if self._has_tool_result(messages):
            content = self._final_answer(clause_id, clause_text, messages)
            return self._result(AIMessage(content=content))
        return self._result(AIMessage(content="", tool_calls=self._tool_calls(clause_text)))

    @staticmethod
    def _parse_request(messages: list[BaseMessage]) -> tuple[str, str] | None:
        for message in messages:
            if not isinstance(message, HumanMessage):
                continue
            payload = _first_json_object(message.text)
            clause = payload.get("clause") if isinstance(payload, dict) else None
            if isinstance(clause, dict) and isinstance(clause.get("clause_id"), str):
                return str(clause["clause_id"]), str(clause.get("text") or "")
            match = _CLAUSE_ID_RE.search(message.text)
            return (match.group(1), "") if match else None
        return None

    @staticmethod
    def _has_tool_result(messages: list[BaseMessage]) -> bool:
        return any(isinstance(message, ToolMessage) for message in messages)

    def _tool_calls(self, clause_text: str) -> list[dict[str, Any]]:
        names = self.bound_tools or ("mock_search_eval_law",)
        query = clause_text[:80]
        return [
            {"name": name, "args": {"query": query}, "id": f"call_{index}", "type": "tool_call"}
            for index, name in enumerate(names)
        ]

    def _enabled_risks(self, messages: list[BaseMessage]) -> list[RiskDef]:
        """prompt 驱动的风险启用：系统提示词词表里出现的 risk_type 才产出。

        这不是装饰 —— 验收「改一处 prompt → 报告体现涨跌」完全依赖这条链路：
        prompt 删除标签 → 该风险不再被检测 → 召回/误报同向变化。
        """
        system_text = ""
        for message in messages:
            if isinstance(message, SystemMessage):
                system_text = message.text
                break
        self.last_system_prompt = system_text[:200]
        if not system_text:
            # 系统提示词缺失 = 模型不知道词表：宁可不产出，不凭空检测。
            return []
        return [risk for risk in self.risks.values() if risk.risk_type in system_text]

    def _final_answer(
        self, clause_id: str, clause_text: str, messages: list[BaseMessage]
    ) -> str:
        findings: list[dict[str, Any]] = []
        for risk in self._enabled_risks(messages):
            if risk.keyword not in clause_text:
                continue
            # fragile 风险类型刻意引用「相邻但错误」的来源：模拟真模型引用相关
            # 法条张冠李戴（仍在检索池内，过白名单校验，但与金标不符）。
            source_id = source_for(risk.risk_type, correct=False)
            meta = SOURCES[source_id]
            findings.append(
                {
                    "clause_id": clause_id,
                    "risk_type": risk.risk_type,
                    "risk_level": risk.default_level,
                    "summary": risk.summary,
                    "evidence": _evidence_window(clause_text, risk.keyword),
                    "suggestion": risk.suggestion,
                    "citations": [
                        {"source_id": source_id, "article": meta["article"]}
                    ],
                }
            )
        return json.dumps({"clause_id": clause_id, "findings": findings}, ensure_ascii=False)

    @staticmethod
    def _result(message: AIMessage) -> ChatResult:
        message.usage_metadata = dict(_MOCK_USAGE)
        return ChatResult(generations=[ChatGeneration(message=message)])


def _first_json_object(text: str) -> dict[str, Any] | None:
    """从消息文本中解析第一个 JSON 对象（容忍尾部追加的重试指令）。"""
    match = _JSON_OBJECT_RE.search(text)
    if match is None:
        return None
    try:
        payload = json.loads(match.group(0))
    except (json.JSONDecodeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _evidence_window(text: str, keyword: str) -> str:
    """从条款原文截取包含关键词的窗口，保证 evidence 是逐字 substring。"""
    index = text.find(keyword)
    if index < 0:
        return f"不存在的证据片段[{keyword}]"
    start = max(0, index - _EVIDENCE_WINDOW)
    end = min(len(text), index + len(keyword) + _EVIDENCE_WINDOW)
    return text[start:end]


def build_eval_agent_factory(latency_s: float = 0.08) -> Any:
    """构造评测 agent 工厂：每专家一个 examiner 实例（缓存），工具换成评测检索。

    spec 来自注册表（真实范畴关键词与 prompt 版本），仅 tools 替换为评测检索池：
    生产 mock 语料与风险目录 source_id 不同，直接沿用会把全部引用判为白名单外。
    """
    import dataclasses

    from langchain_core.tools import tool

    from app.agents.spec import build_expert_agent

    search_tool = tool(mock_search_eval_law)

    cache: dict[str, Any] = {}

    def factory(spec: Any) -> Any:
        if spec.name not in cache:
            eval_spec = dataclasses.replace(spec, tools=(search_tool,))
            risks = {risk.risk_type: risk for risk in risk_defs_for_expert(spec.name)}
            cache[spec.name] = build_expert_agent(
                eval_spec, EvalExaminer(risks=risks, latency_s=latency_s)
            )
        return cache[spec.name]

    return factory
