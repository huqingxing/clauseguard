"""Stage 6/7 验收：prompt 加载器与 6 个专家 v1.md 的完整性。

prompt 是审查行为的唯一事实源（红线：禁止硬编码在 py 里），这里锁定：
每个专家的文件可加载、范畴声明在位、JSON Schema 说明在位、工具名与该专家注册的
tools 一致、risk_type 去重键在位、版本缺失显式失败。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.agents.registry import EXPERTS, get_expert
from app.prompts import load_prompt

PROMPTS_ROOT = Path(__file__).resolve().parents[2] / "app" / "prompts"

EXPECTED_EXPERTS: tuple[str, ...] = (
    "commercial",
    "subject",
    "liability",
    "ip",
    "data",
    "compliance",
)


def test_registry_has_six_experts_in_priority_order() -> None:
    assert tuple(spec.name for spec in EXPERTS) == EXPECTED_EXPERTS
    # high 在前：预算耗尽时先保必审专家（任务约定的跳过顺序）。
    priorities = [spec.priority for spec in EXPERTS]
    assert priorities == sorted(priorities, key=lambda p: 0 if p == "high" else 1)


@pytest.mark.parametrize("name", EXPECTED_EXPERTS)
def test_prompt_file_loads_and_is_versioned(name: str) -> None:
    spec = get_expert(name)
    prompt = spec.load_system_prompt()
    path = PROMPTS_ROOT / name / f"v{spec.prompt_version}.md"
    assert prompt == path.read_text(encoding="utf-8").strip()
    assert len(prompt) > 500


@pytest.mark.parametrize("name", EXPECTED_EXPERTS)
def test_prompt_declares_scope_and_schema(name: str) -> None:
    prompt = get_expert(name).load_system_prompt()
    assert "输出 JSON Schema" in prompt
    assert '"source_id"' in prompt and '"evidence"' in prompt
    assert '"risk_type"' in prompt, "跨专家合并的去重键必须在 Schema 中声明"
    assert "禁止 Markdown 代码围栏" in prompt


@pytest.mark.parametrize("name", EXPECTED_EXPERTS)
def test_prompt_json_schema_block_is_valid_json(name: str) -> None:
    prompt = get_expert(name).load_system_prompt()
    blocks = re.findall(r"```json\n(.*?)```", prompt, re.DOTALL)
    assert blocks, "prompt 必须包含 JSON Schema 代码块"
    schema = json.loads(blocks[0])
    assert schema["required"] == ["clause_id", "findings"]
    assert schema["additionalProperties"] is False


@pytest.mark.parametrize("name", EXPECTED_EXPERTS)
def test_prompt_tool_names_match_registered_tools(name: str) -> None:
    spec = get_expert(name)
    prompt = spec.load_system_prompt()
    registered = {tool.name for tool in spec.tools}
    for tool_name in registered:
        assert tool_name in prompt, f"prompt 未引用已注册工具 {tool_name}"
    # 工具子集隔离：prompt 不得引用未注册给本专家的工具（防范围外检索）。
    for other in EXPERTS:
        if other.name == name:
            continue
        for tool in other.tools:
            if tool.name not in registered:
                assert tool.name not in prompt, (
                    f"{name} 的 prompt 引用了未绑定工具 {tool.name}"
                )


def test_missing_prompt_version_fails_loudly() -> None:
    with pytest.raises(FileNotFoundError, match="prompt 文件不存在"):
        load_prompt("commercial", 99)


def test_retry_fragment_loads_from_prompts_dir() -> None:
    # 重试指令也是 prompt：必须来自 prompts/ 目录，不允许硬编码在节点 py 里（红线）。
    for name in EXPECTED_EXPERTS:
        hint = get_expert(name).load_retry_hint()
        assert "上一次输出无法解析" in hint
        assert "source_id" in hint


def test_every_expert_has_retry_fragment_file() -> None:
    for name in EXPECTED_EXPERTS:
        assert (PROMPTS_ROOT / name / "v1.retry.md").is_file(), f"{name} 缺重试指令文件"
