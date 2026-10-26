package com.clauseguard.observability;

import io.micrometer.tracing.Span;
import io.micrometer.tracing.TraceContext;
import io.micrometer.tracing.Tracer;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.stereotype.Component;

import java.util.Map;
import java.util.function.Supplier;

/**
 * 跨服务链路上下文：把 Java 当前 span 变成 W3C traceparent 交给 Python。
 *
 * <p>为什么需要这一层：Python 侧每个图节点都会建 span，但它们要挂到「这次审查」下面，
 * 就必须拿到 Java 侧的 trace_id + span_id。手拼 UUID（Stage 9 的做法）能让 trace_id 一致，
 * 却让 Java 自己不上报 span——Langfuse 里只有 Python 半边。此处以 Micrometer Tracer
 * 为准生成 traceparent，Java 与 Python 才是同一条 trace 的两段。
 *
 * <p>为什么用 ObjectProvider 而不是直接注入 Tracer：未启用追踪（management.tracing.enabled=false，
 * 测试与本地默认）时容器里根本没有 Tracer bean，直接注入会让应用起不来。探测不到即退化为
 * 「自己造一个合法 traceparent」，链路可见性下降但功能不受影响。
 */
@Component
public class TraceContextSupport {

    private static final Logger logger = LoggerFactory.getLogger(TraceContextSupport.class);

    private final ObjectProvider<Tracer> tracerProvider;

    public TraceContextSupport(ObjectProvider<Tracer> tracerProvider) {
        this.tracerProvider = tracerProvider;
    }

    /**
     * 当前活动 span 的 W3C traceparent；未启用追踪或没有活动 span 时返回 null。
     */
    public String currentTraceparent() {
        Tracer tracer = tracerProvider.getIfAvailable();
        if (tracer == null) {
            return null;
        }
        Span current = tracer.currentSpan();
        if (current == null) {
            return null;
        }
        TraceContext context = current.context();
        if (context.traceId() == null || context.spanId() == null) {
            return null;
        }
        // sampled 决定 Python 侧 ParentBased 采样器的跟随行为，必须如实传递。
        String flags = Boolean.FALSE.equals(context.sampled()) ? "00" : "01";
        // W3C 要求 trace-id 32 hex、span-id 16 hex：64 位 trace context（部分实现/测试替身）
        // 会给出更短的串，左补零既保持数值不变又符合规范，否则 Python 侧解析失败会另起 trace。
        return "00-" + leftPadHex(context.traceId(), 32)
                + "-" + leftPadHex(context.spanId(), 16) + "-" + flags;
    }

    /**
     * 在命名 span 内执行并返回结果；未启用追踪时直接执行（零开销路径）。
     *
     * <p>span 在这里闭合而不是跟随 SSE 订阅生命周期：订阅是异步长连接，把它塞进 span 作用域
     * 需要跨线程传递且一旦订阅异常就会泄漏未关闭的 span。业务侧这一段（落库 + 触发）是
     * Java 真正负责的工作，Python 的运行以它为父另起子 span，同一条 trace 依然成立。
     */
    public <T> T inSpan(String name, Map<String, String> tags, Supplier<T> body) {
        Tracer tracer = tracerProvider.getIfAvailable();
        if (tracer == null) {
            return body.get();
        }
        Span span = tracer.nextSpan().name(name);
        if (tags != null) {
            tags.forEach(span::tag);
        }
        span.start();
        try (Tracer.SpanInScope scope = tracer.withSpan(span)) {
            return body.get();
        } catch (RuntimeException ex) {
            span.error(ex);
            throw ex;
        } finally {
            span.end();
        }
    }

    /**
     * 兜底 traceparent（未启用追踪时使用）：保证 Python 侧仍有一条可聚合的 trace。
     */
    public String fallbackTraceparent() {
        String traceId = randomHex(32);
        String spanId = randomHex(16);
        logger.debug("tracing disabled; generated local traceparent {}", traceId);
        return "00-" + traceId + "-" + spanId + "-01";
    }

    /**
     * 优先用当前 span 的 traceparent，其次调用方传入，最后本地生成。
     *
     * <p>优先级理由：当前 span 才是「本服务此刻」的真实上下文；调用方传入的 traceparent
     * 若无对应 span（例如直接 curl 调用、没有 instrumentation）只做透传即可。
     */
    public String resolveTraceparent(String incoming) {
        String current = currentTraceparent();
        if (current != null) {
            return current;
        }
        if (incoming != null && !incoming.isBlank()) {
            return incoming;
        }
        return fallbackTraceparent();
    }

    private static String leftPadHex(String value, int length) {
        String normalized = value == null ? "" : value.toLowerCase(java.util.Locale.ROOT);
        if (normalized.length() >= length) {
            return normalized.substring(normalized.length() - length);
        }
        return "0".repeat(length - normalized.length()) + normalized;
    }

    private static String randomHex(int length) {
        StringBuilder builder = new StringBuilder(length);
        java.security.SecureRandom random = new java.security.SecureRandom();
        String alphabet = "0123456789abcdef";
        for (int i = 0; i < length; i++) {
            builder.append(alphabet.charAt(random.nextInt(alphabet.length())));
        }
        return builder.toString();
    }
}
