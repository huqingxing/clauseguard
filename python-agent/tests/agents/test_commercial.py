"""Stage 6 验收：商务专家的组装、范围过滤与输入边界。

锁定三件事：create_agent 用的是 prompts/ 文件内容（非硬编码）、模型从配置来、
输入只喂条款子集 + 元信息（不喂全文）。
"""

from __future__ import annotations

import asyncio
import json

import pytest
from langchain_core.messages import SystemMessage

from app.agents.commercial import (
    PROMPT_EXPERT,
    PROMPT_VERSION,
    build_clause_message,
    build_commercial_agent,
    default_chat_model,
    is_commercial_scope,
)
from app.agents.settings import AgentSettings, get_agent_settings
from app.clients.openai_compat import OpenAICompatChatModel
from app.graph.state import Clause
from app.prompts import load_prompt
from tests.agents.mock_model import MockScriptedChatModel


def test_agent_system_prompt_comes_from_file() -> None:
    # 行为验证：跑一次 agent，模型实际收到的首条消息必须逐字等于 prompts/commercial/v1.md，
    # 即 prompt 来自版本化文件而非代码常量（红线）。
    model = MockScriptedChatModel()
    agent = build_commercial_agent(model=model)
    clause = Clause(clause_id="c-1", index=1, text="买方应在验收合格后30日内支付货款。")
    asyncio.run(
        agent.ainvoke(
            {"messages": [build_clause_message(clause, {"contract_id": "x", "version": 1})]}
        )
    )
    assert model.last_messages, "模型必须收到消息"
    system_messages = [m for m in model.last_messages if isinstance(m, SystemMessage)]
    assert system_messages, "模型调用必须携带 system prompt"
    assert system_messages[0].text == load_prompt(PROMPT_EXPERT, PROMPT_VERSION)


def test_default_model_reads_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUSEGUARD_AGENT_LLM_BASE_URL", "https://gateway.internal/v1")
    monkeypatch.setenv("CLAUSEGUARD_AGENT_LLM_MODEL", "qwen-max")
    monkeypatch.setenv("CLAUSEGUARD_AGENT_LLM_API_KEY", "sk-test")
    get_agent_settings.cache_clear()
    try:
        model = default_chat_model()
        assert isinstance(model, OpenAICompatChatModel)
        assert model.base_url == "https://gateway.internal/v1"
        assert model.model == "qwen-max"
    finally:
        get_agent_settings.cache_clear()


def test_settings_defaults_to_openai_compatible_endpoint() -> None:
    # 这条测的是「代码里的默认值」，不是「本机 .env 配了什么」——必须显式关掉 env_file，
    # 否则开发机一填 CLAUSEGUARD_AGENT_LLM_BASE_URL（dashscope），这条就红，属于测试耦合环境。
    settings = AgentSettings(_env_file=None)
    assert settings.llm_base_url == "https://api.openai.com/v1"
    assert settings.llm_temperature == 0.0


@pytest.mark.parametrize(
    "text,expected",
    [
        ("买方应在验收合格后30日内支付货款。", True),
        ("卖方应于2027年3月31日前交付全部货物。", True),
        ("验收标准按国家标准执行。", True),
        ("卖方应开具增值税专用发票。", True),
        ("双方应对商业秘密承担保密义务，未经同意不得披露。", False),
        ("因不可抗力导致的损失互不追究。", False),
    ],
)
def test_scope_filter(text: str, expected: bool) -> None:
    assert is_commercial_scope(text) is expected


def test_clause_message_contains_only_subset_and_meta() -> None:
    clause = Clause(clause_id="c-001", index=1, text="付款条款全文……")
    message = build_clause_message(clause, {"contract_id": "x", "version": 1})
    payload = json.loads(message)
    assert payload["clause"]["clause_id"] == "c-001"
    assert payload["clause"]["text"] == "付款条款全文……"
    assert payload["contract_meta"] == {"contract_id": "x", "version": 1}
    # 不喂全文：消息体只有单条款，没有其他条款字段。
    assert set(payload) == {"contract_meta", "clause", "instruction"}
