"""Stage 3/7 验收：SSE 事件流的序列、seq 单调性、schema 合规、断开取消。

为什么用 asyncio.run 而非 pytest-asyncio：版本表未列该插件，铁律 5 禁止引入版本表外依赖；
同步测试内直接驱动事件循环即可覆盖异步路径。

Stage 7 起拓扑为 ingest -> dispatch -> (Send fan-out)，空合同（无条款）时 dispatch
不分发任何分片直接收尾，故事件序列为 run.started → ingest 起止 → dispatch 起止 →
run.completed（真实 usage/findings_count/degraded 来自图终态）。
"""

import asyncio
import json
from pathlib import Path

import httpx
from jsonschema import Draft202012Validator
from langchain_core.runnables import RunnableConfig

from app.events import EventEmitter, EventType
from app.graph.build import build_graph
from app.graph.state import ReviewState
from app.main import _make_initial_state, _run_pipeline, create_app

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "contracts" / "events.schema.json"
VALIDATOR = Draft202012Validator(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))

# 期望事件顺序（与任务验收一致）。
EXPECTED_ORDER: list[EventType] = [
    EventType.RUN_STARTED,
    EventType.NODE_STARTED,
    EventType.NODE_FINISHED,
    EventType.NODE_STARTED,
    EventType.NODE_FINISHED,
    EventType.RUN_COMPLETED,
]


def _parse_sse(text: str) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for line in text.splitlines():
        if line.startswith("data:"):
            events.append(json.loads(line[len("data:") :].strip()))
    return events


async def _sse_check() -> None:
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/v1/runs", json={"contract_id": "c1"})
        assert resp.status_code == 200
        events = _parse_sse(resp.text)

    types = [EventType(e["type"]) for e in events]
    assert types == EXPECTED_ORDER, f"事件顺序不符: {types}"

    seqs = [int(e["seq"]) for e in events]
    assert seqs == list(range(1, len(seqs) + 1)), f"seq 必须严格从 1 递增、无跳号: {seqs}"

    for event in events:
        # 每个事件严格符合 events.schema.json（铁律 3）。
        VALIDATOR.validate(event)


def test_run_sequence_and_seq_monotonic() -> None:
    asyncio.run(_sse_check())


async def _disconnect_check() -> None:
    emitter = EventEmitter()
    graph = build_graph()
    state: ReviewState = _make_initial_state("c1")
    config: RunnableConfig = {"configurable": {"emitter": emitter}}
    task = asyncio.create_task(
        _run_pipeline(emitter, graph, state, config, "run-test", "trace-test")
    )
    await asyncio.sleep(0.06)  # 让运行跑一会儿再断开
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert task.done(), "断开后后台图任务应干净结束，无 pending"


def test_client_disconnect_cancels_cleanly() -> None:
    asyncio.run(_disconnect_check())
