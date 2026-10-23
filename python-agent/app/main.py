"""FastAPI 入口。Stage 3 新增 /v1/runs SSE 端点（假流程，但事件形状真实）+ /healthz。

验收：POST /v1/runs 依次下发 run.started → node.started(ingest) → node.finished →
node.started(echo) → message.delta ×3 → node.finished → run.completed；seq 严格递增；
客户端断开时取消图执行，日志出现 cancelled 且无异常堆栈。
"""

import asyncio
import logging
import re
import sys
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from langchain_core.runnables import RunnableConfig

from app.events import SENTINEL, EventEmitter, EventType
from app.graph.build import build_graph
from app.graph.state import Budget, ReviewState

logger = logging.getLogger("app.main")

# uvicorn 默认只为 uvicorn.* logger 挂 handler，app.* 的日志会被静默丢弃；
# 这里给 app 根 logger 挂一个 stderr handler（幂等，避免 reload 重复），保证
# 「客户端断开 cancelled」等关键信息能落到服务端日志（验收要求）。
_app_root = logging.getLogger("app")
if not _app_root.handlers:
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    _app_root.addHandler(_handler)
    _app_root.setLevel(logging.INFO)
    _app_root.propagate = False


# W3C traceparent：版本-traceid-spanid-flags，全小写十六进制。
_TRACEPARENT_RE = re.compile(r"^([0-9a-f]{2})-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$")


def parse_traceparent(header: str | None) -> str:
    """W3C traceparent -> trace_id。

    为什么不引 opentelemetry：版本表未列 OTel，铁律 5 禁止引入版本表外依赖；本阶段只把
    trace_id 透传到 run.started，故直接解析头。后续阶段若引入 opentelemetry-api，可在此升级为
    真实 SpanContext 注入，调用方接口保持稳定。
    """
    if header:
        match = _TRACEPARENT_RE.match(header.strip())
        if match:
            return match.group(2)
    return uuid.uuid4().hex


def _make_initial_state(contract_id: str) -> ReviewState:
    # 预算初值占位；真实阶段由请求参数/配置注入，此处仅保证状态结构完整以便图运行。
    budget = Budget(max_tokens=8192, max_cost_cny=5.0, deadline_ts=0)
    return {
        "contract_id": contract_id,
        "version": 0,
        "clauses": [],
        "findings": [],
        "budget": budget,
        "critic_failures": 0,
        "approved": False,
        "degraded": False,
    }


async def _run_pipeline(
    emitter: EventEmitter,
    graph: Any,
    initial_state: ReviewState,
    config: RunnableConfig,
    run_id: str,
    trace_id: str,
) -> None:
    """编排一次运行：发 run.started → 执行图 → 发 run.completed（或失败/被取消）。"""
    try:
        await emitter.emit(EventType.RUN_STARTED, run_id=run_id, trace_id=trace_id)
        await graph.ainvoke(initial_state, config)
        # Stage 3 无真实用量/发现；真实阶段由汇总节点产出 usage 与 findings_count。
        await emitter.emit(
            EventType.RUN_COMPLETED,
            usage={"input_tokens": 0, "output_tokens": 0, "cost_cny": 0.0},
            findings_count=0,
        )
    except asyncio.CancelledError:
        # 客户端断开触发取消；INFO 记录 cancelled，干净返回，不抛堆栈。
        logger.info("run %s cancelled", run_id)
        return
    except Exception as exc:  # 编排层兜底：内部异常转 run.failed，不污染事件流
        logger.exception("run %s failed", run_id)
        await emitter.emit(EventType.RUN_FAILED, code="INTERNAL", message=str(exc), retryable=False)
    finally:
        # 无论何种收尾，入队哨兵让 SSE 生成器关闭；run_task 取消时 await 会抛 CancelledError，忽略。
        await emitter.queue.put(SENTINEL)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # 为什么不在这里建连接池：Stage 3 无图常驻资源；后续阶段在此装配 postgres checkpointer
    # 与 Java 客户端连接池。
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="ClauseGuard Agent", version="0.1.0", lifespan=lifespan)

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        return JSONResponse({"status": "ok", "service": "python-agent"})

    @app.post("/v1/runs")
    async def start_run(request: Request) -> StreamingResponse:
        raw: Any = await request.json()
        contract_id = (raw or {}).get("contract_id", "")
        trace_id = parse_traceparent(request.headers.get("traceparent"))
        run_id = f"run-{uuid.uuid4().hex[:8]}"
        emitter = EventEmitter()
        graph = build_graph()
        initial_state = _make_initial_state(contract_id)
        config: RunnableConfig = {"configurable": {"emitter": emitter, "trace_id": trace_id}}

        async def event_stream() -> AsyncIterator[str]:
            run_task = asyncio.create_task(
                _run_pipeline(emitter, graph, initial_state, config, run_id, trace_id)
            )
            # 并行监听队列与客户端断开：仅当队列有事件或收到 http.disconnect 才推进，
            # 避免生成器阻塞在 queue.get 时服务端无法感知客户端已离开。
            async def _await_disconnect() -> None:
                # receive 在客户端断开时返回 http.disconnect 消息，完成即代表断开。
                await request.receive()

            receive_task: asyncio.Task[None] = asyncio.create_task(_await_disconnect())
            try:
                while True:
                    get_task = asyncio.create_task(emitter.queue.get())
                    done, _ = await asyncio.wait(
                        {get_task, receive_task}, return_when=asyncio.FIRST_COMPLETED
                    )
                    if receive_task in done:
                        # 客户端断开：取消后台图执行，记录 cancelled。
                        run_task.cancel()
                        logger.info("client disconnected; run %s graph execution cancelled", run_id)
                        break
                    item = get_task.result()
                    if item is SENTINEL:
                        break
                    # 仅用 data: 字段，禁止 sse-starlette 的默认 event: 字段（验收要求）。
                    yield f"data: {item.model_dump_json()}\n\n"
            except asyncio.CancelledError:
                # 服务端主动取消（如连接异常）：同样取消后台图执行，再向上传播。
                run_task.cancel()
                logger.info("client disconnected; run %s graph execution cancelled", run_id)
                raise
            finally:
                receive_task.cancel()
                if not run_task.done():
                    run_task.cancel()
                # 等待后台任务收尾，避免 "task destroyed but pending" 警告。
                try:
                    await run_task
                except asyncio.CancelledError:
                    pass

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


app = create_app()
