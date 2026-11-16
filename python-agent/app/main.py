"""FastAPI 入口。Stage 3 新增 /v1/runs SSE 端点（假流程，但事件形状真实）+ /healthz。

验收：POST /v1/runs 依次下发 run.started → node.started(ingest) → node.finished →
node.started(dispatch) → node.finished → run.completed；seq 严格递增；
客户端断开时取消图执行，日志出现 cancelled 且无异常堆栈。
Stage 7 起 run.completed 携带真实 usage（budget 累加值）/findings_count/degraded，
每个请求独立创建 EventEmitter 与并发信号量——20 并发下事件流不串（seq 各自从 1 起）。
Stage 8 起 run.completed 追加 quality（verified_rate/rework_count/unverified_count，
由图终态 findings 与 critic_failures 计算）。
Stage 9 起：图编译注入 PostgresSaver checkpointer；新增 human_approval 断点——存在 high
风险即 interrupt() 挂起，运行态持久化；新增 POST /v1/runs/{run_id}/resume 供 Java 转发
审批结果恢复运行；事件序号随 checkpoint 落盘，重启后 resume 续接而非重置。
ADR-0006：thread_id 由 app.graph.threads.review_thread_id(run_id) 唯一构造（一次审查 =
一个 thread，f"run:{run_id}"），不再按 contract_id:version 复用断点命名空间；resume 由
run_id 直接派生 thread，不读旧格式的落盘值。
Stage 10 起：render 节点真实回写带批注的 Word（app/render/docx_writer.py，lxml 直接
操作 OOXML 四件套）并上传 MinIO；run.completed 追加可选 artifact 字段
（object_key/download_url/comments_count/size_bytes），前端据此下载产物。
"""

import asyncio
import hashlib
import json
import logging
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
from app.contracts.models import Citation as EventCitation
from app.contracts.models import Finding as EventFinding
from app.contracts.models import RiskLevel
from app.db import DbPool, InMemoryRunRegistry, RunRegistry
from app.events import SENTINEL, EventEmitter, EventType
from app.graph.build import build_graph
from app.graph.nodes.model_gate import ModelUnavailableException
from app.graph.state import (
    FULL_MODE,
    INCREMENTAL_MODE,
    Budget,
    IncrementalInfo,
    ReviewState,
    ReviewTask,
    active_findings,
)
from app.graph.threads import THREAD_PREFIX, review_thread_id
from app.incremental.cache import (
    STORE_CONFIG_KEY,
    IncrementalStore,
    default_incremental_store,
)
from app.observability import (
    record_usage,
    run_span,
    setup_tracing,
    shutdown_tracing,
    trace_id_from,
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


def parse_traceparent(header: str | None) -> str:
    """W3C traceparent -> trace_id。

    Stage 14：解析与校验下沉到 app/observability（与 OTel 父上下文提取同一套正则与
    全零校验），此处保留函数名供既有调用方与测试使用，避免两处解析口径漂移。
    无法解析时本地新生成——保证事件流的 trace_id 永远非空，前端按它聚合可见。
    """
    return trace_id_from(header)


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
    source: dict[str, Any] | None = None,
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
        # ---- Stage 12 合同源（Java 上传时透传；ingest 据此下载解析）----
        "source": source or {},
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
            # 传 expert 名：mock 后端据此产出与专家范畴相符的风险标签（Stage 14）。
            agent = build_expert_agent(spec, default_chat_model(settings, expert=spec.name))
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


def _degraded_detail_of(result: ReviewState) -> dict[str, int] | None:
    """部分条款没审成时的降级明细（Stage 15），无未审条款时返回 None。

    为什么单独给一个字段而不是把 degraded 改成对象：degraded 已是布尔量，被
    「预算超限跳过」「分片失败」「ingest 降级」共用，前端 RunSummary 也读它；
    改类型要同时动 schema、pydantic 契约、TS 类型和 Java 侧补丁，收益只是少一个字段名。
    新增可选字段只影响一处渲染，契约上等价。

    unreviewed_clauses 按「条款」而不是「条款×专家」计：同一条款被两位专家审、
    一位成功一位失败时，它有结论，不该算未审。llm_errors 保留单元数，用于区分
    「模型调不通」与「模型输出不可解析」。
    """
    counters = result.get("review_counters")
    if counters is None:
        return None
    unreviewed = len(counters.unreviewed_clause_ids)
    if unreviewed == 0:
        return None
    return {"unreviewed_clauses": unreviewed, "llm_errors": counters.llm_errors}


def _findings_of(result: ReviewState) -> list[dict[str, Any]]:
    """图终态 findings → run.completed.findings 明细（Stage 12 工作台数据源）。

    为什么随终态事件下发明细：Web 工作台的风险清单需要 clause_id/agent/risk_level/
    summary/suggestion/citations 全字段，若只给 findings_count，前端就得从事件流里
    逐条拼装（message.delta 只有 summary），丢字段且与 Java 重放存储双源不一致。
    明细恒剔除 stale（Stage 11 口径）：删除条款的历史结论只在 state 里追溯。

    为什么显式投影而不是 model_dump 直出：内部 Finding 带 citation_whitelist
    （Critic 引擎校验快照，铁律 3 之外的内部字段），事件契约 Finding 是
    additionalProperties=false，整体 dump 会被 pydantic 拒收。经契约模型
    构造一次，契约漂移在此 fail-fast，而不是等 SSE 消费者炸。
    """
    findings = result.get("findings") or []
    payload: list[dict[str, Any]] = []
    for finding in active_findings(list(findings)):
        event_finding = EventFinding(
            clause_id=finding.clause_id,
            agent=finding.agent,
            risk_type=finding.risk_type,
            # 事件契约把 risk_level 收成枚举：内部值越界（不在 low/medium/high/
            # unverified）时在此 fail-fast，而不是带病下发让前端按未知档位渲染。
            risk_level=RiskLevel(finding.risk_level),
            summary=finding.summary,
            suggestion=finding.suggestion,
            evidence=finding.evidence,
            # active_findings 已剔除 stale，明细里恒为 false；显式写出口径。
            stale=False,
            citations=[
                EventCitation(
                    source_id=citation.source_id,
                    title=citation.title,
                    excerpt=citation.excerpt,
                    url=citation.url,
                )
                for citation in finding.citations
            ],
        )
        payload.append(event_finding.model_dump(mode="json"))
    return payload


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
    traceparent: str | None = None,
    session: str = "",
    mode: str = FULL_MODE,
) -> bool:
    """一次运行/恢复的外层：开根 span（父 = Java 的 traceparent）后交给 _execute_run。

    为什么把 span 开在编排层而不是节点里：节点只声明自己是谁，跨服务父子关系属于
    「这次运行」的属性。根 span 覆盖整轮图执行，token 总量在收尾时回填，
    Langfuse 的 trace 汇总（成本/tokens）就读根 span，不需要前端另算。
    """
    # ADR-0006：观测维度拆开——session 仍是「合同:版本」的聚合维度，
    # review/thread/run 是「单次审查」维度，供按审查 ID 精确过滤，两者正交不绑定。
    configurable = (config or {}).get("configurable") or {}
    thread_id = str(configurable.get("thread_id") or "")
    span_attrs: dict[str, Any] = {
        "clauseguard.run.id": run_id,
        # 本系统一次审查 = 一次 run（Java reviewId 未透传到编排层），
        # 故 review.id 由 run_id 承载；两份别名便于按任一维度检索。
        "clauseguard.review.id": run_id,
        "clauseguard.thread.id": thread_id,
        "clauseguard.review.mode": mode,
        "clauseguard.trace.id": trace_id,
    }
    if session:
        span_attrs["langfuse.session.id"] = session
    logger.info(
        "run tags: review.id=%s run.id=%s thread.id=%s session=%s mode=%s resumed=%s",
        run_id,
        run_id,
        thread_id,
        session or "-",
        mode,
        resume is not None,
    )
    if resume is not None:
        # 恢复是同一笔业务的第二段：单独一个根 span，但打上标记便于在链路上区分
        # 「首次运行」与「审批后续跑」，不必靠时间戳猜。
        span_attrs["clauseguard.resumed"] = True
    with run_span(traceparent, span_attrs) as span:

        def report(result: ReviewState) -> None:
            budget = result.get("budget")
            if isinstance(budget, Budget):
                record_usage(
                    span,
                    input_tokens=budget.spent_input_tokens,
                    output_tokens=budget.spent_output_tokens,
                    cost_cny=budget.spent_cost_cny,
                )
            span.set_attribute(
                "clauseguard.findings.count",
                len(active_findings(list(result.get("findings") or []))),
            )
            span.set_attribute("clauseguard.degraded", bool(result.get("degraded")))

        return await _execute_run(
            emitter,
            graph,
            initial_state,
            config,
            run_id,
            trace_id,
            emit_started=emit_started,
            resume=resume,
            registry=registry,
            report=report,
        )


async def _execute_run(
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
    report: Any = None,
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
        except ModelUnavailableException as exc:
            # 全部条款都没审成（model_gate 拦截）：这是「结论不可用」，不是内部错误。
            # 单独映射错误码，前端与运维才能一眼看出该去查配额/密钥而不是查代码。
            logger.error("run %s 全部条款未审成（%s）：%s", run_id, exc.code, exc)
            await emitter.emit(
                EventType.RUN_FAILED,
                code=exc.code,
                message=str(exc)[:500],
                retryable=True,
            )
            return False
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
        if report is not None:
            # 回填根 span 的整轮用量；挂起（interrupt）不回调——那轮还没跑完，
            # 把半程用量写进根 span 会让 Langfuse 的成本统计偏小且不可解释。
            report(result)
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
        # 部分条款没审成（模型不可用/输出不可解析）时的降级明细；全成功时不下发该字段，
        # 让「没有这个字段」本身就是「本次没有未审条款」的明确信号。
        degraded_detail = _degraded_detail_of(result)
        detail_field = {"degraded_detail": degraded_detail} if degraded_detail else {}
        await emitter.emit(
            EventType.RUN_COMPLETED,
            usage={
                "input_tokens": int(getattr(budget, "spent_input_tokens", 0)),
                "output_tokens": int(getattr(budget, "spent_output_tokens", 0)),
                "cost_cny": float(getattr(budget, "spent_cost_cny", 0.0)),
            },
            findings_count=len(findings),
            # 有未审条款就必然降级：结论不完整这件事不能只体现在一个可选明细字段里。
            degraded=bool(result.get("degraded")) or degraded_detail is not None,
            quality=_quality_of(result),
            incremental=_incremental_of(result),
            # Stage 12：明细随终态下发（已剔除 stale），Java 重放存储与工作台共用。
            findings=_findings_of(result),
            **artifact_field,
            **detail_field,
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
    # Stage 14：追踪初始化放在 lifespan——建 provider/exporter 属于进程级资源，
    # 不应在读事件时才懒初始化（首个请求会丢 span）。关闭时（默认）什么都不做。
    setup_tracing()
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
        # 冲刷最后一批 span，否则进程退出时 BatchSpanProcessor 里的会丢。
        shutdown_tracing()


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
        # Stage 12：合同源由 Java 上传时透传（download_url 指向 Java 内容端点）；
        # ingest 据此经 REST 下载并真实解析出条款。缺省即无源，ingest 显式降级。
        source_raw = (raw or {}).get("source")
        source: dict[str, Any] | None = (
            {str(k): v for k, v in source_raw.items()} if isinstance(source_raw, dict) else None
        )
        trace_id = parse_traceparent(request.headers.get("traceparent"))
        run_id = f"run-{uuid.uuid4().hex[:8]}"
        emitter = EventEmitter()
        settings = get_agent_settings()
        graph = build_graph(settings, app.state.checkpointer)
        # ADR-0006：一次审查 = 一个 thread。run_id 在本次请求内生成且全局唯一，
        # 由它派生 thread 后，同合同同版本的两笔审查不再共用 checkpoint；
        # resume 用同一 run_id 派生回同一个 thread，断点续接语义保持不变。
        thread_id = review_thread_id(run_id)
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
            source=source,
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
                # 恢复时用同一 session 归组（trace 里「同一合同同一版本的两次调用」）。
                "session": f"{contract_id}:v{version}",
            },
        )

        async def event_stream() -> AsyncIterator[str]:
            run_task = asyncio.create_task(
                _run_pipeline(
                    emitter, graph, initial_state, config, run_id, trace_id,
                    registry=app.state.registry,
                    # Stage 14：Java 的 traceparent 作为父上下文，Python 所有节点 span
                    # 落在同一条 trace 下；session 用「合同:版本」便于按合同回看。
                    traceparent=request.headers.get("traceparent"),
                    session=f"{contract_id}:v{version}",
                    mode=mode,
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

        ADR-0006：thread_id 由 run_id 经 review_thread_id 直接派生（与首跑同一个
        run_id → 同一个 thread），**不再读取注册表里的 thread_id 落盘值**——旧格式
        （contract_id:version）记录只读保留，命中只 WARN 不报错，也绝不拿它当
        checkpoint 命名空间；切换前需排空在跑的旧审查。
        从 run_registry 取 event_seq/trace_id/session → 用同一 checkpointer 续跑；
        事件序号从注册表续接，故重启用 resume 后 seq 继续递增不重置。不从头重跑
        整个图（铁律 4：断点恢复）。
        恢复也以 SSE 流式下发（NODE_FINISHED(human_approval) + render + RUN_COMPLETED），
        Java 据此把完成事件续接到前端订阅的 SSE，前端无需重新建立连接即收到终态。
        """
        info = await app.state.registry.get(run_id)
        if info is None:
            return JSONResponse(
                status_code=404,
                content={"code": "RUN_NOT_FOUND", "message": f"run {run_id} 不存在或已过期"},
            )
        legacy_thread = str(info.get("thread_id") or "")
        if legacy_thread and not legacy_thread.startswith(THREAD_PREFIX):
            # ADR-0006「部署时排空」：旧审查的断点挂在 contract:version thread 上，
            # 新代码既不读它、也不自动迁移。给明确业务错误（409）而不是去跑一个
            # 没有 checkpoint 的空图，更不是 500。前端据此提示「请重新发起审查」。
            logger.warning(
                "resume 命中旧格式 thread 记录 run=%s legacy_thread=%s；按 ADR-0006 拒绝续跑，"
                "旧审查须重新发起（切换前应在跑审查到达终态）",
                run_id, legacy_thread,
            )
            return JSONResponse(
                status_code=409,
                content={
                    "code": "LEGACY_THREAD_SCOPE",
                    "message": "该审查使用旧的 thread 作用域，升级后无法续跑，请重新发起审查",
                },
            )
        # 恒等映射：thread_id == review_thread_id(run_id)，与首跑写入的值逐字相同。
        # 不读 info["thread_id"]，resume 因此完全不依赖 contract_id/version。
        thread_id = review_thread_id(run_id)
        trace_id = str(info.get("trace_id") or uuid.uuid4().hex)
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
                    # 恢复是同一笔业务操作的续跑：沿用 Java 带来的 traceparent，
                    # 使「首轮审查 → 审批 → 回写」在链路里仍是同一条 trace。
                    traceparent=request.headers.get("traceparent"),
                    session=str(info.get("session") or thread_id),
                )
            )
            async for chunk in stream_graph_events(request, emitter, run_task, run_id):
                yield chunk

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # 检索实验台端点独立于审查图：懒加载，未配置检索库时不影响 /v1/runs 链路。
    from retrieval.api import router as retrieval_router

    app.include_router(retrieval_router)
    return app


app = create_app()
