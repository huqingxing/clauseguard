"""FastAPI 入口。Stage 3 新增 /v1/runs SSE 端点（假流程，但事件形状真实）+ /healthz。

验收：POST /v1/runs 依次下发 run.started → node.started(ingest) → node.finished →
node.started(dispatch) → node.finished → run.completed；seq 严格递增；
客户端断开时取消图执行，日志出现 cancelled 且无异常堆栈。
Stage 7 起 run.completed 携带真实 usage（budget 累加值）/findings_count/degraded，
每个请求独立创建 EventEmitter 与并发信号量——20 并发下事件流不串（seq 各自从 1 起）。
Stage 8 起 run.completed 追加 quality（verified_rate/rework_count/unverified_count，
由图终态 findings 与 critic_failures 计算）。
Stage 9 起：图编译注入 PostgresSaver checkpointer（thread_id=contract_id:version，
连接池由 lifespan 管理）；新增 human_approval 断点——存在 high 风险即 interrupt()
挂起，运行态持久化；新增 POST /v1/runs/{run_id}/resume 供 Java 转发审批结果恢复运行；
事件序号随 checkpoint 落盘，重启后 resume 续接而非重置。
Stage 10 起：render 节点真实回写带批注的 Word（app/render/docx_writer.py，lxml 直接
操作 OOXML 四件套）并上传 MinIO；run.completed 追加可选 artifact 字段
（object_key/download_url/comments_count/size_bytes），前端据此下载产物。
"""

import asyncio
import hashlib
import json
import logging
import re
import sys
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app.agents.settings import AgentSettings, get_agent_settings
from app.agents.spec import ExpertSpec, build_expert_agent, default_chat_model
from app.db import DbPool, InMemoryRunRegistry, RunRegistry, thread_id_for
from app.events import SENTINEL, EventEmitter, EventType
from app.graph.build import build_graph
from app.graph.state import (
    FULL_MODE,
    INCREMENTAL_MODE,
    Budget,
    IncrementalInfo,
    ReviewState,
    ReviewTask,
    active_findings,
)
from app.incremental.cache import (
    STORE_CONFIG_KEY,
    IncrementalStore,
    default_incremental_store,
)

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


def _mock_content_hash(contract_id: str) -> str:
    """TODO(Stage 4): 替换为真实合同内容（MinIO 对象字节）sha256。

    本阶段合同内容未接入，用 contract_id 派生确定性哈希作为乐观锁基线，保证
    interrupt 事件与 Java 校验口径一致即可。
    """
    return hashlib.sha256(f"mock-content:{contract_id}".encode()).hexdigest()


def _make_initial_state(
    contract_id: str,
    settings: AgentSettings | None = None,
    content_hash: str = "",
    *,
    version: int = 0,
    mode: str = FULL_MODE,
    base_version: int = 0,
) -> ReviewState:
    # 预算来自编排配置（token/成本/时限三闸）；deadline<=0 表示不设时限。
    resolved = settings if settings is not None else get_agent_settings()
    deadline_ts = (
        int(time.time()) + int(resolved.run_deadline_s) if resolved.run_deadline_s > 0 else 0
    )
    budget = Budget(
        max_tokens=resolved.max_run_tokens,
        max_cost_cny=resolved.max_run_cost_cny,
        deadline_ts=deadline_ts,
    )
    return {
        "contract_id": contract_id,
        "version": version,
        "clauses": [],
        "findings": [],
        "budget": budget,
        "critic_failures": 0,
        "approved": False,
        "degraded": False,
        # 占位空载荷：expert_review 只可能被 Send 派发后执行（写入真实载荷），
        # 初始值只为满足状态结构完整性。
        "review_task": ReviewTask(expert="", priority="high", shard_index=0, shard_total=0),
        # 当前轮 Critic 打回指令（Stage 8）；无打回时为空列表。
        "rework": [],
        # 事件序号随 checkpoint 落盘，断点恢复续接而非重置（Stage 9）。
        "event_seq": 0,
        # 合同内容哈希：ingest 阶段算定，作为人工审批乐观锁基线（Java 校验防篡改）。
        "content_hash": content_hash or _mock_content_hash(contract_id),
        # 回写渲染结果占位（Stage 9 仅占位）。
        "render_result": "",
        # ---- Stage 11 增量重审入参 ----
        # mode=incremental 时 diff 节点按 base_version 取基线；缺省 base_version=0 表示
        # 由 diff 节点按 version-1 推断（Java 可显式指定基线版本）。
        "review_mode": mode,
        "base_version": base_version,
    }


def _agent_factory(settings: AgentSettings) -> Any:
    """按 run 缓存的 agent 工厂：每个专家只组装一次（prompt 文件加载 + 工具绑定）。

    为什么按 run 创建而非进程级缓存：模型实例与图编译产物属于运行态，随配置
    注入走 config（铁律 4），避免跨 run 共享可变状态。
    """
    cache: dict[str, Any] = {}

    def factory(spec: ExpertSpec) -> Any:
        agent = cache.get(spec.name)
        if agent is None:
            agent = build_expert_agent(spec, default_chat_model(settings))
            cache[spec.name] = agent
        return agent

    return factory


def _quality_of(result: ReviewState) -> dict[str, Any]:
    """从图终态计算 Critic 质量指标（Stage 8），原样进 run.completed.quality。

    口径：
    - verified_rate：非 unverified 的 finding 占比；无 finding 时定义为 1.0
      （无审查对象即无未通过项，避免除零）；
    - rework_count：Critic 打回重审的轮数（= 终态 critic_failures，上限
      MAX_CRITIC_RETRIES）；
    - unverified_count：终态 unverified 档 finding 条数（含引擎层引用丢空降级
      与 Critic 打回超限降级）。

    Stage 11：口径只算「本版本有效」的 findings（剔除 stale）——stale 是历史追溯记录，
    计入质量指标会让「本轮审查质量」被上版本已删条款的旧结论稀释。
    """
    findings = active_findings(list(result.get("findings") or []))
    total = len(findings)
    unverified = sum(1 for finding in findings if finding.risk_level == "unverified")
    verified_rate = (total - unverified) / total if total else 1.0
    return {
        "verified_rate": round(verified_rate, 4),
        "rework_count": int(result.get("critic_failures") or 0),
        "unverified_count": unverified,
    }


def _incremental_of(result: ReviewState) -> dict[str, Any]:
    """从图终态取增量账本（Stage 11），原样进 run.completed.incremental。

    缺省（非增量/无 diff 节点结果）时补一份 mode=full 的账本而不是省略字段：
    报告必须写明「本次是 full 还是 incremental」，缺省会让前端无法区分
    「全量」与「契约没下发」，只能猜。
    """
    info = result.get("incremental")
    if not isinstance(info, IncrementalInfo):
        return {"mode": FULL_MODE, "reused_findings": 0, "cache_hit_rate": 0.0}
    return info.model_dump(mode="json")


async def _persist_event_seq(registry: Any, run_id: str, emitter: EventEmitter) -> None:
    """把当前发射器序号落盘到 run_registry（Stage 9 断点续接的关键）。

    为什么不写图 checkpoint：实测 langgraph 的 aupdate_state 在中断态会清掉 pending
    interrupt（next 从 ('human_approval',) 变 ()），后续 Command(resume=...) 无处可
    恢复——resume 变成空跑，render 永远不执行。run_registry 本就是断点元信息
    （thread_id/version/content_hash）的持久层，seq 随它落盘既不碰图状态，
    重启后 resume 端点同一处读取，语义更直白。
    """
    if registry is None:
        return
    try:
        info = await registry.get(run_id) or {}
        info["event_seq"] = emitter._seq
        await registry.put(run_id, info)
    except Exception as exc:  # 落盘失败不应炸掉事件流；记日志即可。
        logger.warning("persist event_seq failed for %s: %s", run_id, exc)


def _artifact_of(result: ReviewState) -> dict[str, Any] | None:
    """从图终态的 render_result 提取 artifact（Stage 10 回写产物）。

    render_result 是 render 节点写入的 JSON 字符串（rendered 时含 object_key/
    download_url/comments_count/size_bytes）；占位结果以 PLACEHOLDER_RENDER 开头，
    无产物时返回 None，run.completed 的 artifact 缺省（null）。
    """
    raw = result.get("render_result") or ""
    if not raw.startswith("{"):
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("render_result 不是合法 JSON，artifact 缺省")
        return None
    if not isinstance(payload, dict) or not payload.get("download_url"):
        return None
    return {
        "object_key": str(payload.get("object_key", "")),
        "download_url": str(payload["download_url"]),
        "comments_count": int(payload.get("comments_count", 0)),
        "size_bytes": int(payload.get("size_bytes", 0)),
    }


async def _run_pipeline(
    emitter: EventEmitter,
    graph: Any,
    initial_state: ReviewState | None,
    config: RunnableConfig,
    run_id: str,
    trace_id: str,
    *,
    emit_started: bool = True,
    resume: Any = None,
    registry: Any = None,
) -> bool:
    """编排一次运行或一次恢复：执行图 → 下发 run.completed（完成）或暂停（中断）。

    返回 True 表示运行已完成，False 表示已中断（需前端审批后 resume）。
    中断检测：langgraph 1.x 的 ainvoke 在命中 interrupt 时正常返回，结果含 __interrupt__
    键（不抛 GraphInterrupt）；据此判断是否暂停，而非依赖异常。
    """
    try:
        if emit_started:
            await emitter.emit(EventType.RUN_STARTED, run_id=run_id, trace_id=trace_id)
        # 恢复时用 Command(resume=value) 作为本次输入；首次运行用初始状态。
        try:
            if resume is not None:
                result = await graph.ainvoke(Command(resume=resume), config)
            else:
                result = await graph.ainvoke(initial_state, config)
        except Exception as exc:  # 图执行异常转 run.failed，不污染事件流
            logger.exception("run %s graph failed", run_id)
            await emitter.emit(
                EventType.RUN_FAILED, code="INTERNAL", message=str(exc), retryable=False
            )
            return False
        # ainvoke 命中 interrupt 时返回结果含 __interrupt__ 键（不抛异常）。
        interrupted = bool(result.get("__interrupt__")) if isinstance(result, dict) else False
        if interrupted:
            # 暂停：不下发 run.completed，前端收到 interrupt 事件后停住等待审批。
            # human_approval 节点因 resume 会重跑、自身不发射事件，故 NODE_STARTED/
            # INTERRUPT 由编排层在此发射（每次调用只跑一次，不会重复）。
            interrupts = result.get("__interrupt__") or []
            payload = interrupts[0].value if interrupts else {}
            await emitter.emit(
                EventType.NODE_STARTED, node="human_approval", agent="human_approval"
            )
            await emitter.emit(
                EventType.INTERRUPT,
                reason=payload.get("reason", "high_risk_pending_approval"),
                payload=payload,
            )
            logger.info("run %s 中断，等待人工审批（resume 续跑）", run_id)
            await _persist_event_seq(registry, run_id, emitter)
            return False
        # 完成：用量/发现数/降级标记/质量指标全部来自图终态。
        budget = result.get("budget")
        findings = active_findings(list(result.get("findings") or []))
        # resume 调用完成的是曾被 human_approval 中断的运行，补发该节点 NODE_FINISHED
        # （节点自身在 resume 重跑时不发射，详见 human_approval 模块 docstring）。
        if resume is not None:
            await emitter.emit(
                EventType.NODE_FINISHED, node="human_approval", duration_ms=0, tokens=0
            )
        # Stage 10：回写产物（带签名下载 URL）随 run.completed 下发；占位时缺省。
        artifact = _artifact_of(result)
        artifact_field = {"artifact": artifact} if artifact is not None else {}
        await emitter.emit(
            EventType.RUN_COMPLETED,
            usage={
                "input_tokens": int(getattr(budget, "spent_input_tokens", 0)),
                "output_tokens": int(getattr(budget, "spent_output_tokens", 0)),
                "cost_cny": float(getattr(budget, "spent_cost_cny", 0.0)),
            },
            findings_count=len(findings),
            degraded=bool(result.get("degraded")),
            quality=_quality_of(result),
            incremental=_incremental_of(result),
            **artifact_field,
        )
        await _persist_event_seq(registry, run_id, emitter)
        return True
    except asyncio.CancelledError:
        logger.info("run %s cancelled", run_id)
        return False
    finally:
        # 无论何种收尾，入队哨兵让 SSE 生成器关闭；run_task 取消时 await 会抛 CancelledError，忽略。
        await emitter.queue.put(SENTINEL)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Stage 9：连接池与 checkpointer 由 lifespan 创建/关闭；pg_dsn 为空则回落 MemorySaver。
    # 仅当 create_app 未注入 checkpointer（生产/无注入）且配置了 pg_dsn 时才升级为 Postgres；
    # 注入场景（测试模拟「重启后持久化层仍在」）或空 pg_dsn 沿用 create_app 已设的内存默认值。
    # 注意：create_app 已保证 app.state.checkpointer/registry 始终存在（即便 lifespan 未触发），
    # 故 httpx.ASGITransport 不跑 lifespan 的测试也能取到 checkpointer。
    if not getattr(app.state, "_checkpointer_injected", False):
        settings = get_agent_settings()
        if settings.pg_dsn:
            db_pool = DbPool(settings.pg_dsn)
            await db_pool.startup()
            app.state.checkpointer = db_pool.checkpointer()
            app.state.registry = db_pool.registry()
            app.state.db_pool = db_pool
    try:
        yield
    finally:
        pool = getattr(app.state, "db_pool", None)
        if pool is not None:
            await pool.shutdown()


def create_app(
    registry: RunRegistry | None = None,
    checkpointer: Any = None,
    incremental_store: IncrementalStore | None = None,
) -> FastAPI:
    """创建 FastAPI 应用。

    registry/checkpointer 可注入（测试）：传入同一实例即可模拟「进程重启后持久化存储仍在」，
    从而验证断点恢复与 seq 续接，而不依赖外部 Postgres。
    incremental_store 可注入（测试）：替换缓存后端，验证命中率/一致性而不依赖真实目录。

    无论是否注入，app.state.checkpointer/registry 在此处即落默认值（内存实现），保证即使
    运行环境不触发 lifespan（如 httpx.ASGITransport 直连测试）也能取到，不会 KeyError。
    """
    app = FastAPI(title="ClauseGuard Agent", version="0.1.0", lifespan=lifespan)
    injected = checkpointer is not None
    app.state._checkpointer_injected = injected
    # 注入共享 checkpointer + 注册表：测试用，模拟重启后仍在的持久化层；否则默认内存实现。
    app.state.checkpointer = checkpointer if injected else MemorySaver()
    app.state.registry = registry if registry is not None else InMemoryRunRegistry()
    app.state.db_pool = None
    app.state.incremental_store = (
        incremental_store if incremental_store is not None else default_incremental_store()
    )

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        return JSONResponse({"status": "ok", "service": "python-agent"})

    def _build_run_config(
        emitter: EventEmitter,
        thread_id: str,
        trace_id: str,
        settings: AgentSettings,
        store: IncrementalStore | None = None,
    ) -> RunnableConfig:
        # 并发信号量按 run 创建：请求间相互隔离；emitter/agent_factory 同样按 run 新建，
        # 运行态依赖走 config（铁律 4），不跨 run 共享可变状态。
        limiter = asyncio.Semaphore(settings.concurrency_limit)
        return {
            "configurable": {
                "emitter": emitter,
                "trace_id": trace_id,
                "thread_id": thread_id,
                "limiter": limiter,
                "agent_factory": _agent_factory(settings),
                # Stage 11：缓存后端按 run 注入（可测试替换）；未注入时节点回落进程默认。
                STORE_CONFIG_KEY: store if store is not None else default_incremental_store(),
            }
        }

    async def stream_graph_events(
        request: Request, emitter: EventEmitter, run_task: asyncio.Task[bool], rid: str
    ) -> AsyncIterator[str]:
        """SSE 封包：从事件队列取出事件 → `data: <json>` 行；同时监听客户端断开取消图执行。

        首次运行与 resume 恢复共用本生成器，保证两条路径的事件流口径一致（含 seq 续接）。
        """
        async def _await_disconnect() -> None:
            await request.receive()

        receive_task: asyncio.Task[None] = asyncio.create_task(_await_disconnect())
        try:
            while True:
                get_task = asyncio.create_task(emitter.queue.get())
                done, _ = await asyncio.wait(
                    {get_task, receive_task}, return_when=asyncio.FIRST_COMPLETED
                )
                if receive_task in done:
                    run_task.cancel()
                    logger.info("client disconnected; run %s graph execution cancelled", rid)
                    break
                item = get_task.result()
                if item is SENTINEL:
                    break
                yield f"data: {item.model_dump_json()}\n\n"
        except asyncio.CancelledError:
            run_task.cancel()
            logger.info("client disconnected; run %s graph execution cancelled", rid)
            raise
        finally:
            receive_task.cancel()
            if not run_task.done():
                run_task.cancel()
            try:
                await run_task
            except asyncio.CancelledError:
                pass

    @app.post("/v1/runs")
    async def start_run(request: Request) -> StreamingResponse:
        raw: Any = await request.json()
        contract_id = (raw or {}).get("contract_id", "")
        content_hash = (raw or {}).get("content_hash", "")
        # Stage 11：version/mode/base_version 由 Java 透传（前端「仅重审变更」按钮即
        # mode=incremental）。未传即 version=0 + full，与既有调用方完全兼容。
        version = int((raw or {}).get("version") or 0)
        mode_raw = str((raw or {}).get("mode") or FULL_MODE).lower()
        mode = INCREMENTAL_MODE if mode_raw == INCREMENTAL_MODE else FULL_MODE
        base_version = int((raw or {}).get("base_version") or 0)
        trace_id = parse_traceparent(request.headers.get("traceparent"))
        run_id = f"run-{uuid.uuid4().hex[:8]}"
        emitter = EventEmitter()
        settings = get_agent_settings()
        graph = build_graph(settings, app.state.checkpointer)
        thread_id = thread_id_for(contract_id, version)
        config = _build_run_config(
            emitter, thread_id, trace_id, settings, app.state.incremental_store
        )
        initial_state = _make_initial_state(
            contract_id,
            settings,
            content_hash,
            version=version,
            mode=mode,
            base_version=base_version,
        )
        # run_id → 断点元信息落库（Postgres）或内存（测试），重启后仍可定位 thread_id。
        # event_seq 随中断/完成时更新（_persist_event_seq），resume 端点据此续接事件序号。
        await app.state.registry.put(
            run_id,
            {
                "thread_id": thread_id,
                "contract_id": contract_id,
                "version": version,
                "content_hash": initial_state["content_hash"],
                "trace_id": trace_id,
                "event_seq": 0,
            },
        )

        async def event_stream() -> AsyncIterator[str]:
            run_task = asyncio.create_task(
                _run_pipeline(
                    emitter, graph, initial_state, config, run_id, trace_id,
                    registry=app.state.registry,
                )
            )
            async for chunk in stream_graph_events(request, emitter, run_task, run_id):
                yield chunk

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/v1/runs/{run_id}/resume", response_model=None)
    async def resume_run(run_id: str, request: Request) -> Response:
        """恢复被 human_approval 中断的运行（由 Java approveReview 转发审批结果调用）。

        从 run_registry 取 thread_id 与 event_seq → 用同一 checkpointer 续跑；事件序号
        从注册表续接，故重启用 resume 后 seq 继续递增不重置。不从头重跑整个图
        （铁律 4：断点恢复）。
        恢复也以 SSE 流式下发（NODE_FINISHED(human_approval) + render + RUN_COMPLETED），
        Java 据此把完成事件续接到前端订阅的 SSE，前端无需重新建立连接即收到终态。
        """
        info = await app.state.registry.get(run_id)
        if info is None:
            return JSONResponse(
                status_code=404,
                content={"code": "RUN_NOT_FOUND", "message": f"run {run_id} 不存在或已过期"},
            )
        thread_id = info["thread_id"]
        trace_id = info.get("trace_id") or uuid.uuid4().hex
        body: Any = await request.json()
        settings = get_agent_settings()
        graph = build_graph(settings, app.state.checkpointer)
        # seq 从注册表续接（中断时由 _persist_event_seq 落盘），不读图状态——
        # 图状态写入会清掉断点（见 _persist_event_seq  docstring）。
        emitter = EventEmitter()
        emitter._seq = int(info.get("event_seq") or 0)
        config = _build_run_config(
            emitter, thread_id, trace_id, settings, app.state.incremental_store
        )

        async def event_stream() -> AsyncIterator[str]:
            run_task = asyncio.create_task(
                _run_pipeline(
                    emitter, graph, None, config, run_id, trace_id,
                    emit_started=False, resume=body,
                    registry=app.state.registry,
                )
            )
            async for chunk in stream_graph_events(request, emitter, run_task, run_id):
                yield chunk

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


app = create_app()
