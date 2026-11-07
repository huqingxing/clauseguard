package com.clauseguard.mcp;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Java → Python 检索端点的单向转发器。检索能力全部留在 Python（含切分、向量、BM25、
 * 融合、rerank），Java 只负责「把 query 送过去、把契约字段取回来」。
 *
 * 为什么用 JDK HttpClient 而不是 WebClient：这里要的是「每次尝试一个独立 3s 硬截止 +
 * 同步重试一次」，JDK HttpRequest.timeout 直接表达这个语义；响应式客户端的 timeout
 * 从订阅起算，重试时容易把两次尝试算进同一段预算，超时反而形同虚设。
 */
@Component
public class PythonRetrievalClient {

    private static final Logger logger = LoggerFactory.getLogger(PythonRetrievalClient.class);

    /** 首次 + 重试 1 次；再多只会让审查链路多等一个 3s，收益为零。 */
    static final int MAX_ATTEMPTS = 2;
    private static final String SEARCH_PATH = "/v1/retrieval/search";

    private final HttpClient httpClient;
    private final ObjectMapper mapper;
    private final String searchUrl;
    private final Duration timeout;

    @Autowired
    public PythonRetrievalClient(
            ObjectMapper mapper,
            @Value("${clauseguard.python.base-url:http://localhost:8000}") String baseUrl,
            @Value("${clauseguard.retrieval.timeout:3s}") Duration timeout) {
        // 必须钉死 HTTP/1.1：默认 HTTP_2 会在明文请求里带 Upgrade: h2c +
        // Connection: Upgrade, HTTP2-Settings，uvicorn/h11 把它当协议升级请求，
        // body 不会交给 ASGI 应用，FastAPI 直接回 422 {"loc":["body"],"type":"missing"}。
        this(mapper, baseUrl, timeout, HttpClient.newBuilder()
                .connectTimeout(timeout)
                .version(HttpClient.Version.HTTP_1_1)
                .build());
    }

    /** 测试注入：可换成打到本地 stub 的 HttpClient，不需要起 Spring 容器。 */
    PythonRetrievalClient(
            ObjectMapper mapper, String baseUrl, Duration timeout, HttpClient httpClient) {
        this.mapper = mapper;
        this.timeout = timeout;
        this.httpClient = httpClient;
        this.searchUrl = baseUrl.replaceAll("/+$", "") + SEARCH_PATH;
    }

    /**
     * 检索失败一律返回空列表并打 WARN，绝不抛异常：MCP 工具的下游是审查链路，
     * 检索不可用时应降级为空引用 + unverified（ADR-0003），而不是让整轮 run 变 500。
     *
     * @param topK 小于等于 0 表示不传 k，让 Python 用 retrieval.yaml 的 top_k；
     *             写死在 Java 会让「改 YAML 重启生效」被悄悄覆盖。
     */
    public List<LawHit> search(String query, int topK) {
        if (query == null || query.isBlank()) {
            return List.of();
        }
        String payload;
        try {
            Map<String, Object> body = new LinkedHashMap<>();
            body.put("query", query);
            if (topK > 0) {
                body.put("k", topK);
            }
            payload = mapper.writeValueAsString(body);
        } catch (JsonProcessingException ex) {
            logger.warn("serialize retrieval request failed: {}", ex.toString());
            return List.of();
        }

        for (int attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
            try {
                HttpRequest request = HttpRequest.newBuilder(URI.create(searchUrl))
                        .timeout(timeout)
                        .header("Content-Type", "application/json; charset=utf-8")
                        .POST(HttpRequest.BodyPublishers.ofString(payload, StandardCharsets.UTF_8))
                        .build();
                HttpResponse<String> response = httpClient.send(
                        request, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
                if (response.statusCode() < 200 || response.statusCode() >= 300) {
                    // 带上响应体：FastAPI 的 422 会把具体字段错误写在 detail 里，
                    // 只记状态码会让「Java 与 Python 请求体口径漂移」无从定位。
                    String detail = response.body();
                    if (detail.length() > 300) {
                        detail = detail.substring(0, 300);
                    }
                    throw new IllegalStateException(
                            "HTTP " + response.statusCode() + " body=" + detail);
                }
                return parseHits(response.body());
            } catch (Exception ex) {
                logger.warn("python retrieval attempt {}/{} failed: {}",
                        attempt, MAX_ATTEMPTS, ex.toString());
            }
        }
        logger.warn("python retrieval unavailable after {} attempts; "
                + "law.search returns empty hits, review continues", MAX_ATTEMPTS);
        return List.of();
    }

    /** 只投影契约四字段；Python 回显的 config/elapsed/dense_score 一律不透传。 */
    private List<LawHit> parseHits(String body) throws JsonProcessingException {
        JsonNode hits = mapper.readTree(body).path("hits");
        if (!hits.isArray()) {
            return List.of();
        }
        List<LawHit> result = new ArrayList<>();
        for (JsonNode hit : hits) {
            String id = hit.path("chunk_id").asText("");
            if (id.isBlank()) {
                continue;
            }
            String law = hit.path("law").asText("");
            String article = hit.path("article").asText("");
            String label = law.isBlank() ? article : "《" + law + "》" + article;
            result.add(new LawHit(
                    id,
                    label,
                    hit.path("text").asText(""),
                    hit.path("score").asDouble(0.0)));
        }
        return result;
    }
}
