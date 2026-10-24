"""Stage 2 契约验收：加载 contracts/events.schema.json，校验全部 examples
既通过 jsonschema（JSON Schema 2020-12 严格）校验，也通过 datamodel-code-generator
生成的 pydantic 模型校验。两侧一致即证明生成物与契约语义对齐，无手写漂移。
"""

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from app.contracts import models

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "contracts" / "events.schema.json"
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

# 生成模型类名与 $defs 键一一对应（datamodel-code-generator 默认 PascalCase）。
MODEL_BY_DEF: dict[str, Any] = {
    "RunStarted": models.RunStarted,
    "NodeStarted": models.NodeStarted,
    "NodeFinished": models.NodeFinished,
    "NodeFailed": models.NodeFailed,
    "MessageDelta": models.MessageDelta,
    "ToolCall": models.ToolCall,
    "ToolResult": models.ToolResult,
    "Interrupt": models.Interrupt,
    "RunCompleted": models.RunCompleted,
    "RunFailed": models.RunFailed,
}

DEF_NAMES = list(SCHEMA["$defs"].keys())


@pytest.mark.parametrize("name", DEF_NAMES)
def test_schema_defs_have_examples(name: str) -> None:
    definition = SCHEMA["$defs"][name]
    assert definition.get("examples"), f"$defs.{name} 必须至少一条 example（验收要求）"


@pytest.mark.parametrize("name", DEF_NAMES)
def test_examples_pass_jsonschema(name: str) -> None:
    definition = SCHEMA["$defs"][name]
    validator = Draft202012Validator(definition)
    for example in definition.get("examples", []):
        # 严格 2020-12 校验，examples 必须完全符合该事件 schema（含 additionalProperties=false）。
        validator.validate(example)


@pytest.mark.parametrize("name", DEF_NAMES)
def test_examples_pass_pydantic(name: str) -> None:
    definition = SCHEMA["$defs"][name]
    model_cls = MODEL_BY_DEF[name]
    for example in definition.get("examples", []):
        # 通过生成的 pydantic 模型再次校验，确保生成物与 schema 语义一致。
        model_cls.model_validate(example)
