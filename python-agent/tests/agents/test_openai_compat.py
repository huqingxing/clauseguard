"""Stage 6 验收：OpenAI 兼容客户端（载荷构造/响应解析/usage/工具绑定）。

用 httpx.MockTransport 在本地闭环验证协议行为，不触网、不需要真实 key。
异步路径用 asyncio.run 驱动（版本表未列 pytest-asyncio，与 Stage 3 约定一致）。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatResult

from app.clients.openai_compat import OpenAICompatChatModel
from app.tools.law import mock_search_law


def _canned_response() -> dict[str, Any]:
    return {
        "model": "gpt-test",
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "mock_search_law",
                                "arguments": '{"query": "付款条款"}',
                            },
                        }
                    ],
                },
            }
        ],
        "usage": {"prompt_tokens": 11, "completion_tokens": 22, "total_tokens": 33},
    }


def _model_with_transport(
    captured: list[dict[str, Any]], status: int = 200
) -> OpenAICompatChatModel:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(status, json=_canned_response())

    transport = httpx.MockTransport(handler)
    return OpenAICompatChatModel(
        base_url="https://gateway.internal/v1",
        api_key="test-key",
        model="gpt-test",
        temperature=0.0,
        max_tokens=2048,
        http_client=httpx.AsyncClient(transport=transport),
    )


def test_agenerate_parses_tool_calls_and_usage() -> None:
    captured: list[dict[str, Any]] = []
    model = _model_with_transport(captured)
    messages: list[Any] = [
        SystemMessage("你是审查专家"),
        HumanMessage("请审查"),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "mock_search_law",
                    "args": {"query": "x"},
                    "id": "call_1",
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(content='{"candidates": []}', tool_call_id="call_1"),
    ]

    async def _run() -> ChatResult:
        return await model._agenerate(messages)

    result = asyncio.run(_run())
    message = result.generations[0].message
    assert isinstance(message, AIMessage)
    assert message.tool_calls[0]["name"] == "mock_search_law"
    assert message.tool_calls[0]["args"] == {"query": "付款条款"}
    assert message.usage_metadata == {
        "input_tokens": 11,
        "output_tokens": 22,
        "total_tokens": 33,
    }
    # 载荷：URL/鉴权/模型参数/四类消息角色与 tool_call_id 透传。
    assert len(captured) == 1
    payload = captured[0]
    assert payload["model"] == "gpt-test"
    assert payload["temperature"] == 0.0
    assert payload["max_tokens"] == 2048
    assert [m["role"] for m in payload["messages"]] == ["system", "user", "assistant", "tool"]
    assert payload["messages"][2]["tool_calls"][0]["id"] == "call_1"
    assert payload["messages"][3]["tool_call_id"] == "call_1"


def test_bind_tools_sends_openai_tool_schema() -> None:
    captured: list[dict[str, Any]] = []
    model = _model_with_transport(captured)
    bound = model.bind_tools([mock_search_law])

    async def _run() -> None:
        await bound.ainvoke([HumanMessage("请审查付款条款")])

    asyncio.run(_run())
    payload = captured[-1]
    assert payload["tools"][0]["type"] == "function"
    assert payload["tools"][0]["function"]["name"] == "mock_search_law"
    assert "query" in payload["tools"][0]["function"]["parameters"]["properties"]


def test_http_error_propagates() -> None:
    captured: list[dict[str, Any]] = []
    model = _model_with_transport(captured, status=500)

    async def _run() -> None:
        await model._agenerate([HumanMessage("hi")])

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(_run())
