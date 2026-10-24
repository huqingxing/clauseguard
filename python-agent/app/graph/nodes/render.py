"""render 节点：把已审批的发现渲染为带批注的 Word 并上传，返回签名下载 URL。

Stage 10 起为真实回写（lxml 挂批注，见 app/render/docx_writer）；以下三种情况
显式占位（不假装修写成功）：
- 源合同不可用（未配置 MinIO/本地目录，或对象不存在）；
- 源合同非 docx（PDF 回写属后续阶段）；
- 未配置存储后端（无 MinIO 也无 local_dir）。
占位结果以 PLACEHOLDER_RENDER 前缀 + reason 返回，run.completed 不带 artifact。

RenderError（坐标系漂移/损坏的 docx）不捕获：批注挂错位置比不挂更糟，直接冒泡
到编排层转 run.failed。
"""

from __future__ import annotations

import logging
import time

from langchain_core.runnables import RunnableConfig

from app.events import EventType, get_emitter
from app.graph.state import ReviewState
from app.render.service import render_for_state

logger = logging.getLogger("app.graph.render")

NODE_NAME = "render"


async def render(state: ReviewState, config: RunnableConfig) -> dict[str, object]:
    """图节点：回写带批注的 Word 并上传，render_result 携带产物信息。"""
    emitter = get_emitter(config)
    await emitter.emit(EventType.NODE_STARTED, node=NODE_NAME, agent=NODE_NAME)
    started = time.monotonic()

    outcome = await render_for_state(state, config)
    if outcome.status == "placeholder":
        logger.warning("%s 占位：%s", NODE_NAME, outcome.render_result)

    duration_ms = int((time.monotonic() - started) * 1000)
    await emitter.emit(EventType.NODE_FINISHED, node=NODE_NAME, duration_ms=duration_ms, tokens=0)
    return {"render_result": outcome.render_result}
