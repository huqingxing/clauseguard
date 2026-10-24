"""OpenAI 兼容 /chat/completions 客户端（BaseChatModel 实现，httpx 驱动）。

为什么不引 langchain-openai：版本表未列该包，铁律 5 禁止引入表外依赖；而 DashScope
compatible-mode、vLLM、One-API 等内网网关暴露的都是 OpenAI 兼容协议，故自实现本阶段
用到的最小子集：chat + function calling + usage 统计。接口与 BaseChatModel 对齐，
后续接入官方包时可整体替换而不动上层代码。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import httpx
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool


class OpenAICompatChatModel(BaseChatModel):
    """最小 OpenAI 兼容聊天模型。

    bind_tools 后 tools 随请求下发；响应里的 tool_calls 与 usage 原样回传，
    供 create_agent 的工具循环与成本统计使用。
    """

    base_url: str
    api_key: str = ""
    model: str
    temperature: float = 0.0
    max_tokens: int | None = None
    timeout_s: float = 60.0
    # 仅测试注入 MockTransport 用；生产为 None，按请求新建 client（审查调用低频，可接受）。
    http_client: httpx.AsyncClient | None = None

    @property
    def _llm_type(self) -> str:
        return "openai-compat"

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Any],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Any:
        # 复用 langchain-core 的 OpenAI 工具格式转换，保证与 create_agent 的下发一致。
        formatted = [convert_to_openai_tool(t) for t in tools]
        return self.bind(tools=formatted, tool_choice=tool_choice, **kwargs)

    def _endpoint(self) -> str:
        return f"{self.base_url.rstrip('/')}/chat/completions"

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    @staticmethod
    def _to_openai_messages(messages: Sequence[BaseMessage]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for index, message in enumerate(messages):
            if isinstance(message, SystemMessage):
                out.append({"role": "system", "content": message.text})
            elif isinstance(message, HumanMessage):
                out.append({"role": "user", "content": message.text})
            elif isinstance(message, AIMessage):
                payload: dict[str, Any] = {"role": "assistant", "content": message.text}
                if message.tool_calls:
                    payload["tool_calls"] = [
                        {
                            "id": call.get("id") or f"call_{index}_{i}",
                            "type": "function",
                            "function": {
                                "name": call["name"],
                                "arguments": json.dumps(call.get("args") or {}, ensure_ascii=False),
                            },
                        }
                        for i, call in enumerate(message.tool_calls)
                    ]
                out.append(payload)
            elif isinstance(message, ToolMessage):
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": message.tool_call_id,
                        "content": message.text,
                    }
                )
            else:  # pragma: no cover - 本阶段只会出现上述四类消息
                raise ValueError(f"不支持的消息类型: {type(message).__name__}")
        return out

    def _build_payload(
        self, messages: Sequence[BaseMessage], stop: Sequence[str] | None, **kwargs: Any
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": self._to_openai_messages(messages),
            "temperature": self.temperature,
        }
        if self.max_tokens is not None:
            payload["max_tokens"] = self.max_tokens
        if stop:
            payload["stop"] = list(stop)
        if kwargs.get("tools"):
            payload["tools"] = kwargs["tools"]
        if kwargs.get("tool_choice") is not None:
            payload["tool_choice"] = kwargs["tool_choice"]
        if kwargs.get("response_format"):
            payload["response_format"] = kwargs["response_format"]
        return payload

    @staticmethod
    def _to_chat_result(data: dict[str, Any]) -> ChatResult:
        choice = data["choices"][0]
        message = choice.get("message") or {}
        tool_calls: list[dict[str, Any]] = []
        for index, raw_call in enumerate(message.get("tool_calls") or []):
            function = raw_call.get("function") or {}
            raw_args = function.get("arguments") or "{}"
            try:
                args: dict[str, Any] = json.loads(raw_args)
            except json.JSONDecodeError:
                # 参数不是合法 JSON 时原样保留，让上层工具层报错，不静默吞掉。
                args = {"_raw_arguments": raw_args}
            tool_calls.append(
                {
                    "name": function.get("name", ""),
                    "args": args,
                    "id": raw_call.get("id") or f"call_{index}",
                    "type": "tool_call",
                }
            )
        usage = data.get("usage") or {}
        usage_metadata: dict[str, int] = {
            "input_tokens": int(usage.get("prompt_tokens") or 0),
            "output_tokens": int(usage.get("completion_tokens") or 0),
            "total_tokens": int(usage.get("total_tokens") or 0),
        }
        ai_message = AIMessage(
            content=message.get("content") or "",
            tool_calls=tool_calls,
            usage_metadata=usage_metadata,
            response_metadata={
                "model": data.get("model"),
                "finish_reason": choice.get("finish_reason"),
                "token_usage": usage_metadata,
            },
        )
        return ChatResult(
            generations=[ChatGeneration(message=ai_message)],
            llm_output={"token_usage": usage, "model_name": data.get("model")},
        )

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: Sequence[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        payload = self._build_payload(messages, stop, **kwargs)
        with httpx.Client(timeout=self.timeout_s) as client:
            response = client.post(self._endpoint(), json=payload, headers=self._headers())
            response.raise_for_status()
            return self._to_chat_result(response.json())

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: Sequence[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        payload = self._build_payload(messages, stop, **kwargs)
        if self.http_client is not None:
            response = await self.http_client.post(
                self._endpoint(), json=payload, headers=self._headers()
            )
            response.raise_for_status()
            return self._to_chat_result(response.json())
        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            response = await client.post(self._endpoint(), json=payload, headers=self._headers())
            response.raise_for_status()
            return self._to_chat_result(response.json())
