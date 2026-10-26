package com.clauseguard.agent;

import com.clauseguard.events.AgentEvent;
import com.clauseguard.api.model.ApproveRequest;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.github.resilience4j.circuitbreaker.CircuitBreaker;
import io.github.resilience4j.circuitbreaker.CircuitBreakerRegistry;
import io.github.resilience4j.reactor.circuitbreaker.operator.CircuitBreakerOperator;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.core.ParameterizedTypeReference;
import org.springframework.http.MediaType;
import org.springframework.http.client.reactive.ReactorClientHttpConnector;
import org.springframework.http.codec.ServerSentEvent;
import org.springframework.stereotype.Component;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Flux;
import reactor.netty.http.client.HttpClient;

import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

/**
 * Python 编排服务的 SSE 客户端。仅消费事件流并原样转发，不在 Java 侧解释事件语义。
 * 下游 SSE 调用受 30s 超时约束，并由 Resilience4j 熔断保护。
 */
@Component
public class PythonAgentClient {

    private static final Logger logger = LoggerFactory.getLogger(PythonAgentClient.class);

    private final WebClient webClient;
    private final ObjectMapper mapper;
    private final CircuitBreaker circuitBreaker;

    public PythonAgentClient(
            WebClient.Builder webClientBuilder,
            ObjectMapper mapper,
            CircuitBreakerRegistry circuitBreakerRegistry,
            @Value("${clauseguard.python.base-url}") String baseUrl,
            @Value("${clauseguard.python.response-timeout}") Duration responseTimeout) {
        this.mapper = mapper;
        this.circuitBreaker = circuitBreakerRegistry.circuitBreaker("pythonAgent");
        // 下游 30s 超时：WebClient.Builder 在 Spring Framework 6.2 的基线接口不含
        // responseTimeout(...)，改为在响应式连接器层面设置（reactor-netty HttpClient）。
        this.webClient = webClientBuilder
                .baseUrl(baseUrl)
                .clientConnector(new ReactorClientHttpConnector(
                        HttpClient.create().responseTimeout(responseTimeout)))
                .build();
    }

    /**
     * 触发一次审查运行并流式消费 Python 事件。熔断打开时本方法会立即以
     * CallNotPermittedException 结束（由调用方转为 run.failed）。
     */
    public Flux<AgentEvent> streamRun(String reviewId, String contractId, String traceparent) {
        return streamRun(reviewId, contractId, traceparent, 0, "full", 0);
    }

    /**
     * Stage 11：带版本号/模式的触发入口。version/mode/base_version 原样透传给 Python，
     * Java 不解释其语义（增量 diff 与缓存命中全在编排层），只负责把业务侧的版本事实传下去。
     */
    public Flux<AgentEvent> streamRun(
            String reviewId,
            String contractId,
            String traceparent,
            int version,
            String mode,
            int baseVersion) {
        return streamRun(reviewId, contractId, traceparent, version, mode, baseVersion, null);
    }

    /**
     * Stage 12：带合同源信息的触发入口。source 描述「文件在哪、怎么取」：
     * download_url 指向 Java 自己的内容端点（Python 经 REST 拉文件，铁律 1），
     * content_type/version 供 ingest 选择解析器与落基线。Java 不存也不解释文件内容。
     */
    public Flux<AgentEvent> streamRun(
            String reviewId,
            String contractId,
            String traceparent,
            int version,
            String mode,
            int baseVersion,
            Map<String, Object> source) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("contract_id", contractId);
        body.put("version", version);
        body.put("mode", mode == null || mode.isBlank() ? "full" : mode);
        body.put("base_version", baseVersion);
        if (source != null && !source.isEmpty()) {
            body.put("source", source);
        }
        return webClient.post()
                .uri("/v1/runs")
                .header("traceparent", traceparent)
                .accept(MediaType.TEXT_EVENT_STREAM)
                .bodyValue(body)
                .retrieve()
                .bodyToFlux(new ParameterizedTypeReference<ServerSentEvent<String>>() {
                })
                .filter(sse -> sse.data() != null && !sse.data().isBlank())
                .map(sse -> parseData(sse.data()))
                .transform(CircuitBreakerOperator.of(circuitBreaker));
    }

    /**
     * Stage 12：探测 Python /healthz。供 admin 端点聚合双栈健康；
     * 前端不直连 Python（禁止项），由本方法代理探测。不可达返回 empty（调用方记 down）。
     */
    public Optional<String> probeHealth() {
        try {
            return Optional.ofNullable(
                    webClient.get()
                            .uri("/healthz")
                            .retrieve()
                            .bodyToMono(String.class)
                            .block(Duration.ofSeconds(3)));
        } catch (Exception ex) {
            logger.warn("python health probe failed: {}", ex.getMessage());
            return Optional.empty();
        }
    }

    /**
     * 解析单个 SSE data 行。只抽取契约公共字段，原始 JSON 原样保留用于落库与转发。
     */
    AgentEvent parseData(String json) {
        try {
            JsonNode node = mapper.readTree(json);
            long seq = node.path("seq").asLong();
            String ts = node.path("ts").asText();
            String type = node.path("type").asText();
            return new AgentEvent(seq, ts, type, json);
        } catch (JsonProcessingException e) {
            throw new IllegalArgumentException("malformed agent event json: " + json, e);
        }
    }

    /**
     * 构造合规的 run.failed 事件（严格符合 events.schema.json）。seq/ts 由调用方填充以保证单调。
     */
    public AgentEvent buildRunFailedEvent(long seq, String ts, String code, String message, boolean retryable) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("seq", seq);
        body.put("ts", ts);
        body.put("type", "run.failed");
        body.put("code", code);
        body.put("message", message);
        body.put("retryable", retryable);
        return new AgentEvent(seq, ts, "run.failed", writeJson(body));
    }

    private String writeJson(Map<String, Object> body) {
        try {
            return mapper.writeValueAsString(body);
        } catch (JsonProcessingException e) {
            throw new IllegalStateException("serialize run.failed failed", e);
        }
    }

    /**
     * 恢复被 human_approval 中断的运行：把审批结果数组作为 interrupt 的返回值 POST 给 Python，
     * 并以 SSE 订阅续跑事件（NODE_FINISHED(human_approval) + RUN_COMPLETED），由调用方
     * handleAgentEvent 续接到前端 SSE。熔断打开时本方法立即以 CallNotPermittedException 结束。
     */
    public Flux<AgentEvent> streamResume(String runId, List<ApproveRequest> approvals, String traceparent) {
        return webClient.post()
                .uri("/v1/runs/{runId}/resume", runId)
                .header("traceparent", traceparent)
                .accept(MediaType.TEXT_EVENT_STREAM)
                .bodyValue(approvals)
                .retrieve()
                .bodyToFlux(new ParameterizedTypeReference<ServerSentEvent<String>>() {
                })
                .filter(sse -> sse.data() != null && !sse.data().isBlank())
                .map(sse -> parseData(sse.data()))
                .transform(CircuitBreakerOperator.of(circuitBreaker));
    }
}
