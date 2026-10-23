"""跨服务 SSE 事件桥接层 + 事件发射器（单一事实源）。

为什么存在：events.schema.json 是事件结构的唯一事实源，models.py 由 datamodel-code-generator
从 schema 生成（见 contracts/generate.sh）。本模块重导出生成模型与类型枚举，并提供 EventEmitter
作为事件组装的唯一出口——节点与编排层都只调用 EventEmitter，绝不在别处手工拼事件字段
（避免漂移，铁律 3）。运行前必须先运行 `make gen`（或 contracts/generate.sh）。
"""

import asyncio
import logging
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel

from app.contracts.models import (  # noqa: F401  生成模型，单一事实源的镜像
    Interrupt,
    MessageDelta,
    NodeFinished,
    NodeStarted,
    RunCompleted,
    RunFailed,
    RunStarted,
    ToolCall,
    ToolResult,
)

logger = logging.getLogger("app.events")

# 流结束哨兵：编排层在 run 收尾后入队，SSE 生成器据此关闭连接。
SENTINEL: Any = object()


class EventType(StrEnum):
    """点分小写事件类型；扩充必须先登记 contracts/events.schema.json 的 $defs。"""

    RUN_STARTED = "run.started"
    NODE_STARTED = "node.started"
    NODE_FINISHED = "node.finished"
    MESSAGE_DELTA = "message.delta"
    TOOL_CALL = "tool.call"
    TOOL_RESULT = "tool.result"
    INTERRUPT = "interrupt"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"


class Producer(StrEnum):
    JAVA_SERVICE = "java-service"
    PYTHON_AGENT = "python-agent"


# 事件类型 -> 生成模型类的映射；emit 据此构造强类型事件，杜绝字段拼写漂移。
MODEL_BY_TYPE: dict[EventType, type[BaseModel]] = {
    EventType.RUN_STARTED: RunStarted,
    EventType.NODE_STARTED: NodeStarted,
    EventType.NODE_FINISHED: NodeFinished,
    EventType.MESSAGE_DELTA: MessageDelta,
    EventType.TOOL_CALL: ToolCall,
    EventType.TOOL_RESULT: ToolResult,
    EventType.INTERRUPT: Interrupt,
    EventType.RUN_COMPLETED: RunCompleted,
    EventType.RUN_FAILED: RunFailed,
}


class EventEmitter:
    """统一产出 SSE 事件：自动填 seq（单调自增）/ ts（RFC3339 UTC），写入 asyncio.Queue。

    为什么集中在这里：所有事件必须经本类 assembly，节点只负责「何时发什么」，不负责「怎么拼」。
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self._seq = 0

    @property
    def queue(self) -> asyncio.Queue[Any]:
        return self._queue

    async def emit(self, event_type: EventType, **fields: Any) -> BaseModel:
        model_cls = MODEL_BY_TYPE[event_type]
        self._seq += 1
        payload: dict[str, Any] = {
            "seq": self._seq,
            "ts": datetime.now(UTC),
            "type": event_type.value,
            **fields,
        }
        event = model_cls.model_validate(payload)
        await self._queue.put(event)
        return event


def get_emitter(config: RunnableConfig) -> EventEmitter:
    """从 LangGraph config 取注入的发射器；运行态依赖走 config，不用全局变量（铁律 4）。"""
    configurable = (config or {}).get("configurable") or {}
    emitter = configurable.get("emitter")
    if not isinstance(emitter, EventEmitter):
        raise RuntimeError("EventEmitter 未注入 config.configurable，无法发射事件")
    return emitter
