"""echo 节点：产出 3 条 message.delta，用于验证 SSE 流式增量形状。

为什么叫 echo：本阶段无真实 LLM，仅把三段固定文本作为流式增量下发，验证前端实时渲染链路。
"""

import asyncio
import logging
import time

from langchain_core.runnables import RunnableConfig

from app.events import EventType, get_emitter
from app.graph.state import ReviewState

logger = logging.getLogger("app.graph.echo")

_ECHO_CHUNKS: list[str] = [
    "收到合同，开始逐条审查。",
    "识别到多处高风险条款，正在汇总。",
    "初步发现已生成，等待人工审批高风险的条款。",
]


async def echo(state: ReviewState, config: RunnableConfig) -> dict[str, object]:
    emitter = get_emitter(config)
    await emitter.emit(EventType.NODE_STARTED, node="echo", agent="echo")
    start = time.monotonic()
    for text in _ECHO_CHUNKS:
        await emitter.emit(EventType.MESSAGE_DELTA, agent="echo", text=text)
        await asyncio.sleep(0.05)
    duration_ms = int((time.monotonic() - start) * 1000)
    await emitter.emit(EventType.NODE_FINISHED, node="echo", duration_ms=duration_ms, tokens=0)
    return {}
