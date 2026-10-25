"""ingest 节点：读取合同元信息，填 contract_id/version。

为什么 mock：Stage 3 只跑通事件流形状，不接真实 MinIO/解析；mock 显式命名并留 TODO，
杜绝「看起来能跑」的假实现（红线）。
"""

import asyncio
import logging
import time
from typing import Any

from langchain_core.runnables import RunnableConfig

from app.events import EventType, get_emitter
from app.graph.state import ReviewState

logger = logging.getLogger("app.graph.ingest")


def mock_fetch_minio_meta(contract_id: str) -> dict[str, Any]:
    """TODO(Stage 4): 替换为真实 MinIO 客户端（app/clients/minio_client.py）拉取对象元信息。"""
    return {"contract_id": contract_id, "version": 1}


async def ingest(state: ReviewState, config: RunnableConfig) -> dict[str, object]:
    emitter = get_emitter(config)
    await emitter.emit(EventType.NODE_STARTED, node="ingest", agent="ingest")
    start = time.monotonic()
    # 模拟元信息拉取耗时；真实阶段替换为 MinIO 调用。
    await asyncio.sleep(0.02)
    meta = mock_fetch_minio_meta(state["contract_id"])
    # 调用方传入的合同版本优先（Stage 11）：增量重审按版本号定位基线快照，
    # 被 mock 常量 1 覆盖会让 diff 永远拿 v1 当基线、也永远把产物写成 v1。
    # mock 元信息只在未传版本时兜底（version<=0）。
    version = int(state.get("version") or 0) or int(meta["version"])
    duration_ms = int((time.monotonic() - start) * 1000)
    await emitter.emit(EventType.NODE_FINISHED, node="ingest", duration_ms=duration_ms, tokens=0)
    return {"contract_id": meta["contract_id"], "version": version}
