"""Stage 1 空测试基线：验证 /healthz 可达与事件模型严格性。"""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.events import EventType, Producer
from app.main import app


def test_healthz_returns_ok() -> None:
    client = TestClient(app)
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "python-agent"}


def test_event_type_enum_matches_schema() -> None:
    # 枚举值必须与 events.schema.json 的 $defs 类型一致，否则就是漂移。
    assert {e.value for e in EventType} == {
        "run.started",
        "node.started",
        "node.finished",
        "message.delta",
        "tool.call",
        "tool.result",
        "interrupt",
        "run.completed",
        "run.failed",
    }


def test_generated_model_forbids_extra_fields() -> None:
    # 运行态事件必须严格符合 events.schema.json（additionalProperties=false -> extra forbid）。
    from app.contracts.models import RunStarted

    with pytest.raises(ValidationError):
        RunStarted(
            seq=1,
            ts="2026-10-22T10:00:00Z",
            type="run.started",
            run_id="run-1",
            trace_id="t-1",
            unexpected_field="leak",  # 私加字段应被拒绝
        )


def test_producer_enum() -> None:
    assert Producer.JAVA_SERVICE.value == "java-service"
    assert Producer.PYTHON_AGENT.value == "python-agent"
