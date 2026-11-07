package com.clauseguard.mcp;

import ch.qos.logback.classic.Level;
import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.slf4j.LoggerFactory;
import org.springframework.ai.tool.ToolCallback;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.http.HttpClient;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 阶段 6 验收：law.search 的 HTTP 转发、契约投影、3s 超时、重试 1 次、失败降级不抛异常。
 * 用 JDK 内置 HttpServer 起本地 stub，不依赖真实 Python 或 Spring 容器。
 */
class LiveLawSearchToolTest {

    private static final String RESPONSE = """
            {"hits":[
              {"chunk_id":"civil_code:第五百八十五条:0:S5_context","law":"民法典",
               "article":"第五百八十五条","text":"当事人可以约定一方违约时支付违约金。",
               "score":0.03278,"dense_score":0.79,"sparse_score":48.3},
              {"chunk_id":"civil_code:第五百八十八条:0:S5_context","law":"民法典",
               "article":"第五百八十八条","text":"当事人既约定违约金，又约定定金的。",
               "score":0.03225,"dense_score":0.76,"sparse_score":23.2}
            ],"elapsed_ms":13.6,"config":{"default_strategy":"S5_context"}}
            """;

    private final ObjectMapper mapper = new ObjectMapper();
    private final List<HttpServer> servers = new ArrayList<>();
    // 单线程 executor 会让「超时后立刻重试」被上一个仍在 sleep 的 handler 堵住，
    // 测出来的就不是客户端行为；给 stub 一个池才能真实复现两次独立尝试。
    private final ExecutorService stubExecutor = Executors.newCachedThreadPool();
    // HttpServer 在独立线程里处理请求，ThreadLocal 读不到；用共享引用承接请求体。
    private final AtomicReference<String> lastRequestBody = new AtomicReference<>();

    @AfterEach
    void stopServers() {
        servers.forEach(server -> server.stop(0));
        stubExecutor.shutdownNow();
    }

    private String startStub(AtomicInteger calls, int failFirstN, long sleepMs) throws IOException {
        HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.setExecutor(stubExecutor);
        server.createContext("/v1/retrieval/search", exchange -> {
            int n = calls.incrementAndGet();
            byte[] body = exchange.getRequestBody().readAllBytes();
            lastRequestBody.set(new String(body, StandardCharsets.UTF_8));
            if (sleepMs > 0) {
                try {
                    Thread.sleep(sleepMs);
                } catch (InterruptedException ignored) {
                    Thread.currentThread().interrupt();
                }
            }
            byte[] out;
            if (n <= failFirstN) {
                out = "{\"detail\":\"backend down\"}".getBytes(StandardCharsets.UTF_8);
                exchange.sendResponseHeaders(503, out.length);
            } else {
                out = RESPONSE.getBytes(StandardCharsets.UTF_8);
                exchange.sendResponseHeaders(200, out.length);
            }
            exchange.getResponseBody().write(out);
            exchange.close();
        });
        server.start();
        servers.add(server);
        return "http://127.0.0.1:" + server.getAddress().getPort();
    }

    private PythonRetrievalClient client(String baseUrl, Duration timeout) {
        return new PythonRetrievalClient(
                mapper, baseUrl, timeout,
                HttpClient.newBuilder().version(HttpClient.Version.HTTP_1_1).build());
    }

    @Test
    void returnsRealLawHitsWithOriginalContractAndNoHardcodedTopK() throws Exception {
        AtomicInteger calls = new AtomicInteger();
        String base = startStub(calls, 0, 0);
        ToolCallback tool = new LiveLawSearchTool(client(base, Duration.ofSeconds(3)));

        assertThat(tool.getToolDefinition().name()).isEqualTo("law.search");
        String json = tool.call("{\"query\":\"违约金过分高于损失\"}");
        var hits = mapper.readTree(json);

        assertThat(hits.isArray()).isTrue();
        assertThat(hits.size()).isEqualTo(2);
        assertThat(hits.get(0).get("id").asText())
                .isEqualTo("civil_code:第五百八十五条:0:S5_context");
        assertThat(hits.get(0).get("article").asText()).isEqualTo("《民法典》第五百八十五条");
        assertThat(hits.get(0).get("score").asDouble()).isEqualTo(0.03278);
        assertThat(hits.get(0).has("dense_score")).isFalse();
        assertThat(hits.get(0).has("elapsed_ms")).isFalse();
        // k 不由 Java 决定：省略 k，Python 才会用 retrieval.yaml 的 top_k。
        assertThat(lastRequestBody.get()).contains("query").doesNotContain("\"k\"");
        assertThat(lastRequestBody.get()).doesNotContain("strategy");
        assertThat(calls.get()).isEqualTo(1);
    }

    @Test
    void retriesOnceAfterFailure() throws Exception {
        AtomicInteger calls = new AtomicInteger();
        String base = startStub(calls, 1, 0);
        ToolCallback tool = new LiveLawSearchTool(client(base, Duration.ofSeconds(3)));

        String json = tool.call("{\"query\":\"违约金\"}");
        assertThat(mapper.readTree(json).size()).isEqualTo(2);
        assertThat(calls.get()).isEqualTo(2);
    }

    @Test
    void pythonDownReturnsEmptyAndWarnsInsteadOfThrowing() throws Exception {
        AtomicInteger calls = new AtomicInteger();
        String base = startStub(calls, 99, 0);
        Logger logger = (Logger) LoggerFactory.getLogger(PythonRetrievalClient.class);
        ListAppender<ILoggingEvent> appender = new ListAppender<>();
        appender.start();
        logger.addAppender(appender);
        try {
            ToolCallback tool = new LiveLawSearchTool(client(base, Duration.ofSeconds(3)));
            String json = tool.call("{\"query\":\"违约金\"}");
            assertThat(mapper.readTree(json).isArray()).isTrue();
            assertThat(mapper.readTree(json).size()).isZero();
            assertThat(calls.get()).isEqualTo(2);
            assertThat(appender.list)
                    .anySatisfy(event -> {
                        assertThat(event.getLevel()).isEqualTo(Level.WARN);
                        assertThat(event.getFormattedMessage()).contains("unavailable");
                    });
        } finally {
            logger.detachAppender(appender);
            appender.stop();
        }
    }

    @Test
    void timeoutIsPerAttemptAndDegradesToEmpty() throws Exception {
        AtomicInteger calls = new AtomicInteger();
        String base = startStub(calls, 0, 400);
        ToolCallback tool = new LiveLawSearchTool(client(base, Duration.ofMillis(50)));
        assertThat(mapper.readTree(tool.call("{\"query\":\"违约金\"}")).size()).isZero();
        assertThat(calls.get()).isEqualTo(2);
    }

    /**
     * 真机联调：启动真实 python-agent 后用
     * CLAUSEGUARD_RETRIEVAL_E2E_URL=http://127.0.0.1:8010 跑本类，
     * 由 Java 客户端真的发一次 POST，而不是只测 stub。未设环境变量时自动跳过。
     */
    @Test
    @EnabledIfEnvironmentVariable(named = "CLAUSEGUARD_RETRIEVAL_E2E_URL", matches = ".+")
    void hitsRealPythonRetrievalEndpoint() {
        String base = System.getenv("CLAUSEGUARD_RETRIEVAL_E2E_URL");
        List<LawHit> hits = client(base, Duration.ofSeconds(3)).search(
                "当事人约定的违约金过分高于造成的损失，可以请求人民法院予以适当减少吗", -1);
        assertThat(hits).isNotEmpty();
        assertThat(hits.get(0).id()).contains("civil_code:");
        assertThat(hits.get(0).article()).contains("民法典");
        assertThat(hits.get(0).text()).isNotBlank();
        System.out.println("JAVA_TO_PYTHON_LIVE_HITS=" + hits.size()
                + " TOP1=" + hits.get(0).id() + " ARTICLE=" + hits.get(0).article());
    }

    /** 停掉真实 python-agent 后跑：验证连接失败也只返回空集合，不抛异常。 */
    @Test
    @EnabledIfEnvironmentVariable(named = "CLAUSEGUARD_RETRIEVAL_DOWN_URL", matches = ".+")
    void degradesWhenRealPythonIsDown() {
        String base = System.getenv("CLAUSEGUARD_RETRIEVAL_DOWN_URL");
        List<LawHit> hits = client(base, Duration.ofSeconds(3)).search("违约金", -1);
        assertThat(hits).isEmpty();
        System.out.println("JAVA_TO_PYTHON_DOWN_HITS=0 (review continues)");
    }

    @Test
    void modeSwitchSelectsLiveByDefaultAndMockOnDemand() {
        PythonRetrievalClient unused = client("http://127.0.0.1:1", Duration.ofSeconds(3));
        assertThat(new McpToolsConfig("live").lawSearchCallback(unused))
                .isInstanceOf(LiveLawSearchTool.class);
        assertThat(new McpToolsConfig("mock").lawSearchCallback(unused))
                .isInstanceOf(MockLawSearchTool.class);
        assertThat(new McpToolsConfig("  ").lawSearchCallback(unused))
                .isInstanceOf(LiveLawSearchTool.class);
    }
}
