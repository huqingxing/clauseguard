package com.clauseguard.observability;

import io.micrometer.tracing.Span;
import io.micrometer.tracing.Tracer;
import io.micrometer.tracing.test.simple.SimpleSpan;
import io.micrometer.tracing.test.simple.SimpleTracer;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.ObjectProvider;

import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

/**
 * Stage 14：Java 侧 traceparent 生成。
 *
 * <p>为什么单独测这个类：它是「一条 trace 串起 Java 与 6 个 agent span」的起点——
 * 如果 traceparent 的 trace/span id 取自自己造的 UUID 而不是当前 span，
 * Python 的父上下文就会指向一个不存在的 span，链路在 Langfuse 里是断的。
 */
class TraceContextSupportTest {

    @SuppressWarnings("unchecked")
    private static ObjectProvider<Tracer> providerOf(Tracer tracer) {
        ObjectProvider<Tracer> provider = mock(ObjectProvider.class);
        when(provider.getIfAvailable()).thenReturn(tracer);
        return provider;
    }

    @SuppressWarnings("unchecked")
    private static ObjectProvider<Tracer> emptyProvider() {
        ObjectProvider<Tracer> provider = mock(ObjectProvider.class);
        when(provider.getIfAvailable()).thenReturn(null);
        return provider;
    }

    @Test
    void currentTraceparentCarriesActiveSpanIds() {
        SimpleTracer tracer = new SimpleTracer();
        TraceContextSupport support = new TraceContextSupport(providerOf(tracer));
        Span span = tracer.nextSpan().name("review.create").start();

        try (Tracer.SpanInScope scope = tracer.withSpan(span)) {
            String traceparent = support.currentTraceparent();
            assertThat(traceparent)
                    .as("W3C traceparent 必须是 00-<32hex>-<16hex>-<flags>")
                    .matches("^00-[0-9a-f]{32}-[0-9a-f]{16}-0[01]$");
            assertThat(traceparent)
                    .as("trace_id 必须取自当前 span，不能自己编一个")
                    .contains(span.context().traceId());
            assertThat(traceparent).contains(span.context().spanId());
        } finally {
            span.end();
        }
    }

    @Test
    void noActiveSpanReturnsNull() {
        SimpleTracer tracer = new SimpleTracer();
        TraceContextSupport support = new TraceContextSupport(providerOf(tracer));
        assertThat(support.currentTraceparent()).isNull();
    }

    @Test
    void tracingDisabledFallsBackToValidTraceparent() {
        TraceContextSupport support = new TraceContextSupport(emptyProvider());
        assertThat(support.currentTraceparent()).isNull();
        assertThat(support.resolveTraceparent(null)).matches("^00-[0-9a-f]{32}-[0-9a-f]{16}-01$");
        // 未启用追踪时保留调用方传入的头（透传语义）。
        String incoming = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01";
        assertThat(support.resolveTraceparent(incoming)).isEqualTo(incoming);
    }

    @Test
    void inSpanCreatesNamedSpanWithTags() {
        SimpleTracer tracer = new SimpleTracer();
        TraceContextSupport support = new TraceContextSupport(providerOf(tracer));

        String result = support.inSpan(
                "review.create", Map.of("clauseguard.review.mode", "full"), () -> "ok");

        assertThat(result).isEqualTo("ok");
        SimpleSpan created = tracer.getSpans().stream().findFirst().orElseThrow();
        assertThat(created.getName()).isEqualTo("review.create");
        assertThat(created.getTags()).containsEntry("clauseguard.review.mode", "full");
    }

    @Test
    void inSpanClosesSpanOnException() {
        SimpleTracer tracer = new SimpleTracer();
        TraceContextSupport support = new TraceContextSupport(providerOf(tracer));

        try {
            support.inSpan(
                    "review.create",
                    Map.of(),
                    () -> {
                        throw new IllegalStateException("boom");
                    });
        } catch (IllegalStateException expected) {
            // 异常必须原样上抛：追踪层不能改变业务失败语义。
        }
        assertThat(tracer.getSpans()).hasSize(1);
        assertThat(tracer.getSpans().iterator().next().getError()).isNotNull();
        assertThat(tracer.currentSpan()).as("span 必须已关闭，不能泄漏在作用域里").isNull();
    }
}
