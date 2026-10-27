package com.clauseguard.review;

import com.clauseguard.agent.PythonAgentClient;
import com.clauseguard.contract.ContractRepository;
import com.clauseguard.events.InMemoryEventReplayStore;
import com.clauseguard.review.domain.Review;
import io.github.resilience4j.circuitbreaker.CircuitBreakerRegistry;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Import;
import org.springframework.test.web.servlet.MockMvc;

import java.util.Optional;

import static org.hamcrest.Matchers.containsString;
import static org.hamcrest.Matchers.not;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * SSE 断线续传集成测试：通过真实 HTTP SSE 端点验证「带 Last-Event-ID 重连 → 补齐缺失事件」。
 * 不启动 MySQL/Redis/MCP；重放存储用内存实现，事件序列在测试内预置。
 */
@WebMvcTest(ReviewController.class)
@AutoConfigureMockMvc
@Import({ReviewStreamReplayIntegrationTest.Beans.class,
        com.clauseguard.observability.TraceContextSupport.class,
        com.clauseguard.security.SecurityConfig.class})
class ReviewStreamReplayIntegrationTest {

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private InMemoryEventReplayStore replayStore;

    @MockBean
    private ReviewRepository reviewRepository;
    @MockBean
    private ReviewEventRepository reviewEventRepository;
    @MockBean
    private PythonAgentClient agentClient;
    @MockBean
    private ApprovalRepository approvalRepository;
    @MockBean
    private ContractRepository contractRepository;

    @Test
    void reconnectWithLastEventIdReplaysMissingEvents() throws Exception {
        String reviewId = "11111111-1111-1111-1111-111111111111";
        // 预置 3 条事件（seq 1/2/3），模拟断线前已收到 seq 1
        replayStore.append(reviewId, 1, "node.started", "{\"seq\":1,\"type\":\"node.started\"}");
        replayStore.append(reviewId, 2, "node.progress", "{\"seq\":2,\"type\":\"node.progress\"}");
        replayStore.append(reviewId, 3, "run.completed", "{\"seq\":3,\"type\":\"run.completed\"}");

        Review review = new Review();
        review.setId(reviewId);
        when(reviewRepository.findById(reviewId)).thenReturn(Optional.of(review));

        // 断线 30s 后带 Last-Event-ID=1 重连：应补齐 seq 2、3，且不重发已收到的 seq 1
        mockMvc.perform(get("/api/v1/reviews/{id}/stream", reviewId)
                        .header("Last-Event-ID", "1"))
                .andExpect(status().isOk())
                .andExpect(content().string(containsString("node.progress")))
                .andExpect(content().string(containsString("run.completed")))
                .andExpect(content().string(not(containsString("node.started"))));
    }

    @Test
    void reconnectWithSinceQueryReplaysMissingEvents() throws Exception {
        String reviewId = "33333333-3333-3333-3333-333333333333";
        replayStore.append(reviewId, 1, "node.started", "{\"seq\":1,\"type\":\"node.started\"}");
        replayStore.append(reviewId, 2, "node.progress", "{\"seq\":2,\"type\":\"node.progress\"}");
        replayStore.append(reviewId, 3, "run.completed", "{\"seq\":3,\"type\":\"run.completed\"}");

        Review review = new Review();
        review.setId(reviewId);
        when(reviewRepository.findById(reviewId)).thenReturn(Optional.of(review));

        // 前端手动重建 EventSource，新实例不会带 Last-Event-ID，断点只能走 ?since=
        mockMvc.perform(get("/api/v1/reviews/{id}/stream", reviewId).param("since", "1"))
                .andExpect(status().isOk())
                .andExpect(content().string(containsString("node.progress")))
                .andExpect(content().string(containsString("run.completed")))
                .andExpect(content().string(not(containsString("node.started"))));
    }

    @Test
    void sinceQueryWinsOverLastEventIdHeader() throws Exception {
        String reviewId = "44444444-4444-4444-4444-444444444444";
        replayStore.append(reviewId, 1, "node.started", "{\"seq\":1,\"type\":\"node.started\"}");
        replayStore.append(reviewId, 2, "node.progress", "{\"seq\":2,\"type\":\"node.progress\"}");
        replayStore.append(reviewId, 3, "run.completed", "{\"seq\":3,\"type\":\"run.completed\"}");

        Review review = new Review();
        review.setId(reviewId);
        when(reviewRepository.findById(reviewId)).thenReturn(Optional.of(review));

        // 两个断点同时出现时以 since 为准：否则浏览器原生重连的旧头会盖掉前端刚推进的 lastSeq
        mockMvc.perform(get("/api/v1/reviews/{id}/stream", reviewId)
                        .param("since", "2")
                        .header("Last-Event-ID", "1"))
                .andExpect(status().isOk())
                .andExpect(content().string(containsString("run.completed")))
                .andExpect(content().string(not(containsString("node.progress"))));
    }

    @Test
    void reconnectWithLastEventIdReplaysAllWhenNoPriorEvents() throws Exception {
        String reviewId = "22222222-2222-2222-2222-222222222222";
        replayStore.append(reviewId, 1, "node.started", "{\"seq\":1,\"type\":\"node.started\"}");
        replayStore.append(reviewId, 2, "run.completed", "{\"seq\":2,\"type\":\"run.completed\"}");

        Review review = new Review();
        review.setId(reviewId);
        when(reviewRepository.findById(reviewId)).thenReturn(Optional.of(review));

        // 首次连接（无 Last-Event-ID）应回放全量事件
        mockMvc.perform(get("/api/v1/reviews/{id}/stream", reviewId))
                .andExpect(status().isOk())
                .andExpect(content().string(containsString("node.started")))
                .andExpect(content().string(containsString("run.completed")));
    }

    @TestConfiguration
    static class Beans {
        @Bean
        CircuitBreakerRegistry circuitBreakerRegistry() {
            return CircuitBreakerRegistry.ofDefaults();
        }

        @Bean
        InMemoryEventReplayStore replayStore() {
            return new InMemoryEventReplayStore();
        }

        @Bean
        ReviewService reviewService(
                ReviewRepository reviewRepository,
                ReviewEventRepository reviewEventRepository,
                ApprovalRepository approvalRepository,
                InMemoryEventReplayStore replayStore,
                PythonAgentClient agentClient,
                CircuitBreakerRegistry circuitBreakerRegistry,
                com.fasterxml.jackson.databind.ObjectMapper mapper,
                com.clauseguard.contract.ContractRepository contractRepository,
                com.clauseguard.observability.TraceContextSupport traceSupport) {
            return new ReviewService(
                    reviewRepository, reviewEventRepository, approvalRepository,
                    replayStore, agentClient, circuitBreakerRegistry, mapper, contractRepository,
                    traceSupport);
        }
    }
}
