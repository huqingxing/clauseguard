"""离线/压测用确定性模型替身（`mock_` 前缀 + TODO，遵循红线）。

为什么允许它进生产目录：bench 与「无 LLM 密钥的本地演示」都必须能跑通整条链路
（ingest → 6 专家 fan-out → critic → 审批 → 回写），而真实模型需要密钥与配额。
本模块只替换「模型怎么回答」这一层，图拓扑、并发限流、引用白名单校验、预算记账、
事件流全部走真实代码——压测测的是编排层，不是这段假回答。

行为（两段式，与合规模型的 ReAct 形状一致）：
1. 首次调用：对已绑定工具发起检索（query = 条款开头 80 字）；
2. 拿到工具结果后：产出 1 条 finding，引用检索候选里的第一条，evidence 取条款逐字片段。

启用条件：`CLAUSEGUARD_AGENT_MODEL_BACKEND=mock`（默认 live，绝不会误用）。

TODO(Stage 15): 接真实模型跑压测与演示；届时应删除本模块或改为显式测试替身。
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

# 固定用量：让预算记账/成本折算走真实公式且完全确定（压测的「平均成本」才有意义）。
_MOCK_USAGE = {"input_tokens": 180, "output_tokens": 80, "total_tokens": 260}

# 每个专家输出一个与其范畴相符的风险标签；仅用于让工作台/报告可读，不参与判定。
_RISK_TYPE_BY_EXPERT = {
    "commercial": "payment_risk",
    "subject": "qualification_risk",
    "liability": "breach_risk",
    "ip": "ip_ownership_risk",
    "data": "data_processing_risk",
    "compliance": "jurisdiction_risk",
}

_CLAUSE_ID_RE = re.compile(r'"clause_id"\s*:\s*"([^"]+)"')
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)

_EVIDENCE_CHARS = 40

# 高风险判定：与真实审查中最常见的「无上限/不退还/单方」类条款一致。
# 替身为什么要分级而不是恒 medium：演示与压测都要走到 human_approval 断点，
# 全部 medium 会让审批链路在 mock 模式下永远不被触发。
_HIGH_RISK_MARKERS = ("不设上限", "不予退还", "无限期", "单方解除", "全部免除")


class MockChatModel(BaseChatModel):
    """确定性聊天模型替身；每次调用固定延迟，使压测时延分布可复现。"""

    expert: str = "commercial"
    latency_s: float = 0.05
    bound_tools: tuple[str, ...] = ()

    @property
    def _llm_type(self) -> str:
        return "mock-chat-model"

    def bind_tools(self, tools: Any, *, tool_choice: str | None = None, **kwargs: Any) -> Any:
        # 替身不需要工具 schema：只记住绑定子集，用于第一段发起 tool_calls。
        self.bound_tools = tuple(getattr(tool, "name", str(tool)) for tool in (tools or ()))
        return self.bind()

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        return self._respond(messages)

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        if self.latency_s > 0:
            # 固定延迟代表「模型思考时间」；压测的 P50/P95/P99 由它主导，可复现。
            await asyncio.sleep(self.latency_s)
        return self._respond(messages)

    def _respond(self, messages: list[BaseMessage]) -> ChatResult:
        request = _parse_request(messages)
        if request is None:
            return _result(AIMessage(content='{"clause_id":"","findings":[]}'))
        clause_id, clause_text = request
        if _has_tool_result(messages):
            return _result(AIMessage(content=self._final_answer(clause_id, clause_text, messages)))
        return _result(AIMessage(content="", tool_calls=self._tool_calls(clause_text)))

    def _tool_calls(self, clause_text: str) -> list[dict[str, Any]]:
        names = self.bound_tools or ("mock_search_law",)
        query = clause_text[:80]
        return [
            {"name": name, "args": {"query": query}, "id": f"call_{index}", "type": "tool_call"}
            for index, name in enumerate(names)
        ]

    def _final_answer(
        self, clause_id: str, clause_text: str, messages: list[BaseMessage]
    ) -> str:
        candidate = _first_candidate(messages)
        citations: list[dict[str, str]] = []
        if candidate is not None:
            citations.append(
                {
                    "source_id": str(candidate.get("id") or ""),
                    "article": str(candidate.get("article") or ""),
                }
            )
        findings = [
            {
                "clause_id": clause_id,
                "risk_type": _RISK_TYPE_BY_EXPERT.get(self.expert, "payment_risk"),
                "risk_level": _risk_level(clause_text),
                "summary": f"[mock] {self.expert} 专家在条款中识别到需关注的约定",
                "evidence": clause_text[:_EVIDENCE_CHARS],
                "suggestion": "建议补充明确的履约期限与责任上限。",
                "citations": citations,
            }
        ]
        return json.dumps({"clause_id": clause_id, "findings": findings}, ensure_ascii=False)


def mock_chat_model(expert: str = "commercial", latency_s: float = 0.05) -> MockChatModel:
    """构造替身模型；仅供 bench 与无密钥演示（调用方须显式选择 mock 后端）。"""
    return MockChatModel(expert=expert, latency_s=latency_s)


def _parse_request(messages: list[BaseMessage]) -> tuple[str, str] | None:
    """从 HumanMessage 里取 (clause_id, 条款原文)；取不到视为无需审查。"""
    for message in messages:
        if not isinstance(message, HumanMessage):
            continue
        match = _JSON_OBJECT_RE.search(message.text)
        if match is not None:
            try:
                payload = json.loads(match.group(0))
            except (json.JSONDecodeError, ValueError):
                payload = None
            clause = payload.get("clause") if isinstance(payload, dict) else None
            if isinstance(clause, dict) and isinstance(clause.get("clause_id"), str):
                return str(clause["clause_id"]), str(clause.get("text") or "")
        hit = _CLAUSE_ID_RE.search(message.text)
        if hit is not None:
            return hit.group(1), ""
    return None


def _has_tool_result(messages: list[BaseMessage]) -> bool:
    return any(isinstance(message, ToolMessage) for message in messages)


def _risk_level(clause_text: str) -> str:
    """按高风险标记词分级，供审批断点走真实路径（见 _HIGH_RISK_MARKERS 注释）。"""
    return "high" if any(marker in clause_text for marker in _HIGH_RISK_MARKERS) else "medium"


def _first_candidate(messages: list[BaseMessage]) -> dict[str, Any] | None:
    """取最后一次检索结果里的第一条候选，作为引用来源（保证过白名单校验）。"""
    for message in reversed(messages):
        if not isinstance(message, ToolMessage):
            continue
        try:
            payload = json.loads(message.text)
        except (json.JSONDecodeError, ValueError):
            continue
        candidates = payload.get("candidates") if isinstance(payload, dict) else None
        if isinstance(candidates, list) and candidates and isinstance(candidates[0], dict):
            return candidates[0]
    return None


def _result(message: AIMessage) -> ChatResult:
    # usage_metadata 是 LangChain 的 UsageMetadata（TypedDict），直接赋值等价形状即可，
    # 记账链路（review.py 读 input/output_tokens）与真实模型完全一致。
    message.usage_metadata = {
        "input_tokens": _MOCK_USAGE["input_tokens"],
        "output_tokens": _MOCK_USAGE["output_tokens"],
        "total_tokens": _MOCK_USAGE["total_tokens"],
    }
    return ChatResult(generations=[ChatGeneration(message=message)])
