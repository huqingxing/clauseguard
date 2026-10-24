"""Stage 6 用的 scripted 模型替身（仅测试）。

TODO(Stage 7): 真实模型（app.agents.commercial.default_chat_model，OpenAI 兼容）需要 live
key 单独验收；本替身模拟「合规模型」的两段式行为（先调检索工具、再按契约 JSON 输出），
用于确定性验证管线（解析/重试/白名单/降级/指标），不代表真实模型质量。
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from tests.agents.fixtures import ClauseSpec

_CLAUSE_ID_RE = re.compile(r'"clause_id"\s*:\s*"([^"]+)"')
# 请求体是单行 JSON；重试时节点会在其后追加纠正指令，故只取第一个 JSON 对象解析。
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)
_RETRY_MARK = "上一次输出无法解析"
# Stage 8：Critic 打回重审指令的标记短语（与 prompts/critic/v1.rework.md 保持一致）。
# 替身据此切换「首轮行为/重审行为」，模拟可修复与永远失败两种模型。
_REWORK_MARK = "未通过确定性校验"

# 伪造法条注入：只存在于模型输出、绝不出现在任何检索结果里的 id（5 条，逐条轮换注入）。
FAKE_CITATIONS: list[dict[str, str]] = [
    {"source_id": "FAKE-001", "article": "《幻想法典》第一条"},
    {"source_id": "FAKE-002", "article": "《虚构规定》第九百九十九条"},
    {"source_id": "FAKE-003", "article": "《空气法》第十七条"},
    {"source_id": "FAKE-004", "article": "《虚构条例》第二条"},
    {"source_id": "FAKE-005", "article": "《幻影办法》第一百条"},
]

# 替身固定 usage，让 token 记账路径在 CI 里被真实走到。
_MOCK_USAGE: dict[str, int] = {"input_tokens": 150, "output_tokens": 60, "total_tokens": 210}
_GARBAGE_OUTPUT = "好的，我认为这个条款存在一些风险，建议您酌情处理。（非 JSON 输出）"


class MockScriptedChatModel(BaseChatModel):
    """按 fixtures 行为表应答的确定性模型替身。

    两段式行为与真实合规模型一致：无工具结果时发起三个检索调用；拿到工具结果后
    按 prompts/commercial/v1.md 的 JSON Schema 输出。行为由 specs 驱动：
    expect_finding=False 输出空 findings（含正常条款与「模型漏报」条款）。
    """

    specs: dict[str, ClauseSpec] = {}
    malformed_first: bool = False
    malformed_always: bool = False
    call_count: int = 0
    fake_rotation: int = 0
    # Stage 7：专家绑定的工具子集（只请求这些，不请求未绑定的）与每次异步调用的固定延迟。
    bound_tools: tuple[str, ...] = ()
    latency_s: float = 0.0
    # 记录最近一次收到的消息，供「system prompt 来自文件」等行为断言。
    last_messages: list[Any] = []

    @property
    def _llm_type(self) -> str:
        return "mock-scripted"

    def bind_tools(
        self,
        tools: Any,
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Any:
        # 替身不需要工具 schema：行为表固定「先调已绑工具再输出」，绑定仅作直通，
        # 但记住工具子集——每个专家只调自己的工具（Stage 7 工具子集隔离的验证点）。
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
            # 固定延迟注入：并行/串行 P95 对照的唯一变量（其他行为完全一致）。
            await asyncio.sleep(self.latency_s)
        return self._respond(messages)

    # ---- 行为实现 ----

    def _respond(self, messages: list[BaseMessage]) -> ChatResult:
        self.last_messages = list(messages)
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

    @staticmethod
    def _is_retry(messages: list[BaseMessage]) -> bool:
        return any(
            isinstance(message, HumanMessage) and _RETRY_MARK in message.text
            for message in messages
        )

    @staticmethod
    def _is_rework(messages: list[BaseMessage]) -> bool:
        """是否携带 Critic 打回指令（Stage 8 重审轮判定）。"""
        return any(
            isinstance(message, HumanMessage) and _REWORK_MARK in message.text
            for message in messages
        )

    def _tool_calls(self, clause_text: str) -> list[dict[str, Any]]:
        # 只请求专家实际绑定的工具子集（commercial 三个、subject/ip 两个……）；
        # 未记录绑定时退回商业专家的全集（Stage 6 单专家场景）。
        names = self.bound_tools or (
            "mock_search_law",
            "mock_search_policy",
            "mock_search_similar_clauses",
        )
        query = clause_text[:80]
        return [
            {"name": name, "args": {"query": query}, "id": f"call_{index}", "type": "tool_call"}
            for index, name in enumerate(names)
        ]

    def _final_answer(
        self, clause_id: str, clause_text: str, messages: list[BaseMessage]
    ) -> str:
        if self.malformed_always:
            return _GARBAGE_OUTPUT
        if self.malformed_first and not self._is_retry(messages):
            return _GARBAGE_OUTPUT
        spec = self.specs.get(clause_id)
        findings: list[dict[str, Any]] = []
        if spec is not None and spec.expect_finding:
            # 重审轮且 spec 声明了修正关键词时切换证据来源：模拟「被打回后修正」
            # （未声明则保持原输出，即永远失败场景）。
            keyword = spec.evidence_kw
            if spec.rework_evidence_kw and self._is_rework(messages):
                keyword = spec.rework_evidence_kw
            findings.append(
                {
                    "clause_id": clause_id,
                    "risk_type": spec.risk_type,
                    "risk_level": spec.risk_level,
                    "summary": spec.summary,
                    "evidence": _extract_evidence(clause_text, keyword),
                    "suggestion": spec.suggestion,
                    "citations": self._citations(spec, messages),
                }
            )
        return json.dumps({"clause_id": clause_id, "findings": findings}, ensure_ascii=False)

    def _citations(self, spec: ClauseSpec, messages: list[BaseMessage]) -> list[dict[str, str]]:
        if spec.cite_mode == "fabricated":
            # 每条伪造 citation 只注入 1 个假 id，逐条轮换，保证 5 条互不相同。
            entry = FAKE_CITATIONS[self.fake_rotation % len(FAKE_CITATIONS)]
            self.fake_rotation += 1
            return [entry]
        candidates = _tool_candidates(messages)
        valid = [
            {"source_id": str(item["id"]), "article": str(item["article"])}
            for item in candidates
            if isinstance(item, dict) and item.get("id")
        ]
        if spec.cite_mode == "mixed":
            return valid[:1] + [FAKE_CITATIONS[0]]
        return valid[:2]

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


def _tool_candidates(messages: list[BaseMessage]) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, ToolMessage):
            continue
        try:
            payload = json.loads(message.text)
        except (json.JSONDecodeError, ValueError):
            continue
        candidates = payload.get("candidates") if isinstance(payload, dict) else None
        if isinstance(candidates, list):
            collected.extend(item for item in candidates if isinstance(item, dict))
    return collected


def _extract_evidence(text: str, keyword: str) -> str:
    """从条款原文截取包含关键词的窗口，保证 evidence 是逐字 substring。"""
    index = text.find(keyword)
    if index < 0:
        return f"不存在的证据片段[{keyword}]"
    start = max(0, index - 8)
    end = min(len(text), index + len(keyword) + 8)
    return text[start:end]
