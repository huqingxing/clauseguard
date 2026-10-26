"""可观测性接入层：W3C 父上下文提取、run/节点 span、OTLP 上报（Langfuse 兼容）。

为什么需要真实 OTel 而不是继续手拼 traceparent：Stage 9 起 Java 只把 trace_id 透传下来，
Python 侧的事件虽然带 trace_id，但两边没有任何 span 层级关系，Langfuse 里是两条互不相干
的 trace——「一条 trace 串起 Java 与 6 个专家 Agent」根本无从验证。本层把
「解析父上下文 → 建 span → 标注属性 → 上报」收敛为唯一入口，图节点只声明
「我是哪个节点、哪个专家、花了多少 token」，不碰 exporter 与采样配置。

依赖边界：本模块是 app 内唯一 import opentelemetry 的地方，关闭追踪时（默认）不建
provider，模块导入仍成功——测试与离线场景不需要 collector 存在。
"""

from __future__ import annotations

import contextlib
import logging
import re
import uuid
from collections.abc import Iterator, Mapping
from typing import Any

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.trace import (
    NonRecordingSpan,
    Span,
    SpanContext,
    SpanKind,
    Status,
    StatusCode,
    TraceFlags,
)

from app.config import Settings, get_settings

logger = logging.getLogger("app.observability")

# W3C traceparent：版本-traceid-spanid-flags，全小写十六进制。
_TRACEPARENT_RE = re.compile(r"^([0-9a-f]{2})-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$")

# span 属性名（OTel GenAI 语义约定 + Langfuse 专用扩展）。
# 为什么用 gen_ai.*：Langfuse 按该约定识别模型用量，自造字段在 UI 里不会归到 tokens 面板。
ATTR_NODE = "clauseguard.node"
ATTR_AGENT = "gen_ai.agent.name"
ATTR_INPUT_TOKENS = "gen_ai.usage.input_tokens"
ATTR_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
ATTR_COST = "clauseguard.cost_cny"
ATTR_OBSERVATION_TYPE = "langfuse.observation.type"
ATTR_TRACE_NAME = "langfuse.trace.name"
ATTR_SESSION_ID = "langfuse.session.id"

_configured = False


def parse_traceparent(header: str | None) -> tuple[str, str] | None:
    """W3C traceparent → (trace_id, parent_span_id)；非法头返回 None（不做猜测）。"""
    if not header:
        return None
    match = _TRACEPARENT_RE.match(header.strip())
    if match is None:
        return None
    trace_id, span_id = match.group(2), match.group(3)
    # 全零的 trace_id/span_id 是 W3C 明确定义的非法值，必须丢弃而不是当父上下文。
    if set(trace_id) == {"0"} or set(span_id) == {"0"}:
        return None
    return trace_id, span_id


def trace_id_from(header: str | None) -> str:
    """事件里的 trace_id：能解析就沿用上游（一条 trace 跨服务），否则本地新生成。"""
    parsed = parse_traceparent(header)
    return parsed[0] if parsed is not None else uuid.uuid4().hex


def extract_parent_context(header: str | None) -> Context:
    """把 Java 传来的 traceparent 变成 OTel 父上下文。

    用 NonRecordingSpan 而非 start_span：父 span 的生命周期在 Java 进程里，Python
    只引用它的 id 建立父子关系，不能伪造一个自己负责收尾的远端 span。
    is_remote=True 让后端知道该父 span 来自另一个服务。
    """
    parsed = parse_traceparent(header)
    if parsed is None:
        return Context()
    trace_id, span_id = parsed
    span_context = SpanContext(
        trace_id=int(trace_id, 16),
        span_id=int(span_id, 16),
        is_remote=True,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
    )
    return trace.set_span_in_context(NonRecordingSpan(span_context))


def _parse_headers(raw: str) -> dict[str, str]:
    """OTEL_EXPORTER_OTLP_HEADERS 的 `k=v,k2=v2` 形式 → dict。"""
    headers: dict[str, str] = {}
    for item in raw.split(","):
        key, sep, value = item.partition("=")
        if sep and key.strip():
            headers[key.strip()] = value.strip()
    return headers


def _normalize_endpoint(endpoint: str) -> str:
    """允许只填 Langfuse 基地址（.../api/public/otel），自动补 /v1/traces。

    为什么做这一步：Langfuse 文档给的是 base + `/v1/traces` 两段，填漏一段会 404
    且 exporter 只在后台日志里报错，排查成本高——在这里容错比让人踩坑便宜。
    """
    trimmed = endpoint.rstrip("/")
    if trimmed.endswith("/v1/traces"):
        return trimmed
    return f"{trimmed}/v1/traces"


def setup_tracing(settings: Settings | None = None, exporter: Any = None) -> bool:
    """初始化 TracerProvider；已初始化过则幂等返回。

    exporter 可注入（测试用 InMemorySpanExporter）；未注入且配置了 endpoint 时用
    OTLP/HTTP 上报（Langfuse 兼容）。未启用追踪时直接返回 False，不建 provider，
    保证默认路径零开销、无 collector 也能跑。
    """
    global _configured
    resolved = settings or get_settings()
    if _configured:
        return True
    if exporter is None and not resolved.otel_enabled:
        return False

    resource = Resource.create(
        {
            "service.name": resolved.otel_service_name,
            "service.namespace": "clauseguard",
            "deployment.environment": resolved.otel_environment,
        }
    )
    provider = TracerProvider(
        resource=resource,
        # ParentBased：上游（Java）已采样就跟随，避免跨服务采样决策打架。
        sampler=ParentBased(root=TraceIdRatioBased(resolved.otel_sample_ratio)),
    )
    if exporter is not None:
        provider.add_span_processor(SimpleSpanProcessor(exporter))
    elif resolved.otel_endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(
                    endpoint=_normalize_endpoint(resolved.otel_endpoint),
                    headers=_parse_headers(resolved.otel_headers),
                    timeout=resolved.otel_export_timeout_s,
                )
            )
        )
    trace.set_tracer_provider(provider)
    _configured = True
    logger.info(
        "tracing enabled: service=%s endpoint=%s sample_ratio=%.2f",
        resolved.otel_service_name,
        resolved.otel_endpoint or "(none: spans kept in-process)",
        resolved.otel_sample_ratio,
    )
    return True


def shutdown_tracing() -> None:
    """进程退出前冲刷 BatchSpanProcessor，避免最后一批 span 丢失。"""
    provider = trace.get_tracer_provider()
    if hasattr(provider, "shutdown"):
        with contextlib.suppress(Exception):
            provider.shutdown()


def tracer() -> trace.Tracer:
    return trace.get_tracer("clauseguard.python")


@contextlib.contextmanager
def run_span(
    traceparent: str | None,
    attributes: Mapping[str, Any] | None = None,
) -> Iterator[Span]:
    """一次审查运行的根 span（父 = Java 传来的 traceparent）。

    根 span 名字固定 `run.contract-review`：Langfuse 的 trace 列表按根 span 名聚合，
    名字随合同变化会让同一个功能散成几十条 trace 名，看板无法聚合。
    """
    attrs: dict[str, Any] = {
        ATTR_TRACE_NAME: "contract-review",
        ATTR_OBSERVATION_TYPE: "span",
    }
    if attributes:
        attrs.update({key: value for key, value in attributes.items() if value is not None})
    parent = extract_parent_context(traceparent)
    with tracer().start_as_current_span(
        "run.contract-review", context=parent, kind=SpanKind.SERVER, attributes=attrs
    ) as span:
        yield span


@contextlib.contextmanager
def node_span(name: str, agent: str | None = None) -> Iterator[Span]:
    """图节点 span：子节点自动挂在当前 run span 下（contextvar 传播）。

    agent 非空时标成 Langfuse 的 agent 观测类型，专家分片才会在 UI 里显示为
    「Agent」而非普通 span——6 个专家并行时这是唯一能一眼看出谁在跑的视图。
    """
    attributes: dict[str, Any] = {ATTR_NODE: name}
    if agent:
        attributes[ATTR_AGENT] = agent
        attributes[ATTR_OBSERVATION_TYPE] = "agent"
    with tracer().start_as_current_span(
        f"node.{name}", kind=SpanKind.INTERNAL, attributes=attributes
    ) as span:
        yield span


def record_usage(
    span: Span,
    *,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cost_cny: float | None = None,
) -> None:
    """把节点消耗写到 span 上；None 表示该节点没有模型调用，不写零值污染看板。"""
    if input_tokens is not None:
        span.set_attribute(ATTR_INPUT_TOKENS, input_tokens)
    if output_tokens is not None:
        span.set_attribute(ATTR_OUTPUT_TOKENS, output_tokens)
    if cost_cny is not None:
        span.set_attribute(ATTR_COST, cost_cny)


def mark_suspended(span: Span) -> None:
    """把「被 interrupt 挂起」标为正常结束。

    为什么需要：human_approval 的 interrupt() 在 LangGraph 内部是以异常形式抛出的控制流，
    若按异常上报，审批断点在链路图里永远是红色错误——那不是故障，是该阶段的正常终点。
    """
    span.set_status(Status(StatusCode.OK))
    span.set_attribute("clauseguard.suspended", True)


def reset_tracing() -> None:
    """仅供测试：清掉全局 provider 以便用 InMemorySpanExporter 重建。

    OTel 的 set_tracer_provider 一个进程只生效一次，测试要多次重建只能复位这两个
    模块级标记（非公开 API，仅测试路径使用，生产不调用）。
    """
    global _configured
    trace._TRACER_PROVIDER = None
    trace._TRACER_PROVIDER_SET_ONCE._done = False
    _configured = False
