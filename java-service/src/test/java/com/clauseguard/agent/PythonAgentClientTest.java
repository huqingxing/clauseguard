package com.clauseguard.agent;

import com.clauseguard.events.AgentEvent;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.github.resilience4j.circuitbreaker.CircuitBreakerRegistry;
import org.junit.jupiter.api.Test;
import org.springframework.web.reactive.function.client.WebClient;

import java.time.Duration;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * PythonAgentClient 单元测试：校验 SSE data 解析与 run.failed 构造，均不启动容器。
 */
class PythonAgentClientTest {

    private final PythonAgentClient client = new PythonAgentClient(
            WebClient.builder(),
            new ObjectMapper(),
            CircuitBreakerRegistry.ofDefaults(),
            "http://localhost:8000",
            Duration.ofSeconds(30));

    @Test
    void parseDataExtractsContractFieldsAndKeepsRawJson() {
        String json = "{\"seq\":3,\"ts\":\"2026-10-23T10:00:00+00:00\",\"type\":\"node.started\","
                + "\"node\":\"ingest\",\"agent\":\"x\"}";

        AgentEvent event = client.parseData(json);

        assertThat(event.seq()).isEqualTo(3);
        assertThat(event.ts()).isEqualTo("2026-10-23T10:00:00+00:00");
        assertThat(event.type()).isEqualTo("node.started");
        assertThat(event.rawJson()).isEqualTo(json);
    }

    @Test
    void buildRunFailedEventIsSchemaCompliant() throws Exception {
        AgentEvent failed = client.buildRunFailedEvent(9L, "2026-10-23T10:00:05+00:00",
                "AGENT_UNAVAILABLE", "Python agent unreachable", true);

        assertThat(failed.seq()).isEqualTo(9);
        assertThat(failed.type()).isEqualTo("run.failed");

        JsonNode node = new ObjectMapper().readTree(failed.rawJson());
        assertThat(node.path("seq").asLong()).isEqualTo(9);
        assertThat(node.path("type").asText()).isEqualTo("run.failed");
        assertThat(node.path("code").asText()).isEqualTo("AGENT_UNAVAILABLE");
        assertThat(node.path("message").asText()).isEqualTo("Python agent unreachable");
        assertThat(node.path("retryable").asBoolean()).isTrue();
    }
}
