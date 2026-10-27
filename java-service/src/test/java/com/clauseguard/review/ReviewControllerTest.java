package com.clauseguard.review;

import com.clauseguard.api.model.ApproveResponse;
import com.clauseguard.api.model.ReviewCreateResponse;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.context.annotation.Import;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.net.URI;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * ReviewController web 切片测试：验证 202 创建、503（下游熔断）、404（资源不存在）。
 * 全局异常映射一并覆盖（@RestControllerAdvice 在 web 切片内生效）。
 */
@WebMvcTest(ReviewController.class)
@AutoConfigureMockMvc
@Import(com.clauseguard.security.SecurityConfig.class)
class ReviewControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @MockBean
    private ReviewService reviewService;

    @Test
    void createReturns202WithReviewIdAndStreamUrl() throws Exception {
        UUID id = UUID.fromString("00000000-0000-0000-0000-0000000000b1");
        when(reviewService.createReview(any(), any()))
                .thenReturn(new ReviewCreateResponse()
                        .reviewId(id)
                        .streamUrl(URI.create("/api/v1/reviews/" + id + "/stream")));

        mockMvc.perform(post("/api/v1/reviews")
                        .contentType("application/json")
                        .content("{\"contract_id\":\"00000000-0000-0000-0000-0000000000a1\"}"))
                .andExpect(status().isAccepted())
                .andExpect(jsonPath("$.review_id").value(id.toString()))
                .andExpect(jsonPath("$.stream_url").value("/api/v1/reviews/" + id + "/stream"));
    }

    @Test
    void streamReturns503WhenAgentUnavailable() throws Exception {
        when(reviewService.isAgentUnavailable()).thenReturn(true);

        mockMvc.perform(get("/api/v1/reviews/00000000-0000-0000-0000-0000000000c1/stream"))
                .andExpect(status().isServiceUnavailable());
    }

    @Test
    void streamReturns404WhenReviewNotFound() throws Exception {
        when(reviewService.isAgentUnavailable()).thenReturn(false);
        when(reviewService.stream(any(), anyLong())).thenThrow(new ReviewNotFoundException("x"));

        mockMvc.perform(get("/api/v1/reviews/00000000-0000-0000-0000-0000000000d1/stream"))
                .andExpect(status().isNotFound());
    }

    // ---- 重连断点：?since= 与 Last-Event-ID 折成同一个 afterSeq ----

    @Test
    void streamPassesSinceAsAfterSeq() throws Exception {
        when(reviewService.isAgentUnavailable()).thenReturn(false);
        when(reviewService.stream(any(), anyLong())).thenReturn(new SseEmitter());

        mockMvc.perform(get("/api/v1/reviews/00000000-0000-0000-0000-0000000000e1/stream?since=7"))
                .andExpect(status().isOk());

        verify(reviewService).stream("00000000-0000-0000-0000-0000000000e1", 7L);
    }

    @Test
    void streamFallsBackToLastEventIdWhenSinceMissing() throws Exception {
        when(reviewService.isAgentUnavailable()).thenReturn(false);
        when(reviewService.stream(any(), anyLong())).thenReturn(new SseEmitter());

        mockMvc.perform(get("/api/v1/reviews/00000000-0000-0000-0000-0000000000e2/stream")
                        .header("Last-Event-ID", "3"))
                .andExpect(status().isOk());

        verify(reviewService).stream("00000000-0000-0000-0000-0000000000e2", 3L);
    }

    @Test
    void resumeAfterPrefersQueryOverHeader() {
        assertEquals(7L, ReviewController.resumeAfter(7L, "3"));
    }

    @Test
    void resumeAfterFallsBackToStart() {
        assertEquals(3L, ReviewController.resumeAfter(null, "3"));
        assertEquals(-1L, ReviewController.resumeAfter(null, null));
        assertEquals(-1L, ReviewController.resumeAfter(null, "not-a-seq"));
        // 负数不是合法断点：夹到 -1（从头回放），而不是把 -5 传给重放存储
        assertEquals(-1L, ReviewController.resumeAfter(-5L, "3"));
    }

    // ---- Stage 9：approve 端点（数组 + X-Operator）----

    @Test
    void approveReturns200WithAccepted() throws Exception {
        when(reviewService.approveReview(any(), any(), any()))
                .thenReturn(new ApproveResponse()
                        .status(ApproveResponse.StatusEnum.ACCEPTED)
                        .decidedAt(OffsetDateTime.parse("2026-10-23T10:00:00Z")));

        mockMvc.perform(post("/api/v1/reviews/00000000-0000-0000-0000-0000000000e1/approve")
                        .header("X-Operator", "alice")
                        .contentType("application/json")
                        .content("[{\"clause_id\":\"c1\",\"decision\":\"approve\","
                                + "\"contract_version\":\"1\",\"content_hash\":\"h1\"}]"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("accepted"));
    }

    @Test
    void approveReturns409OnContractChanged() throws Exception {
        doThrow(new ContractChangedException("r"))
                .when(reviewService).approveReview(any(), any(), any());

        mockMvc.perform(post("/api/v1/reviews/00000000-0000-0000-0000-0000000000e2/approve")
                        .header("X-Operator", "alice")
                        .contentType("application/json")
                        .content("[{\"clause_id\":\"c1\",\"decision\":\"approve\","
                                + "\"contract_version\":\"1\",\"content_hash\":\"h1\"}]"))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("CONTRACT_CHANGED"));
    }

    @Test
    void approveReturns404WhenReviewNotFound() throws Exception {
        when(reviewService.approveReview(any(), any(), any()))
                .thenThrow(new ReviewNotFoundException("x"));

        mockMvc.perform(post("/api/v1/reviews/00000000-0000-0000-0000-0000000000e3/approve")
                        .header("X-Operator", "alice")
                        .contentType("application/json")
                        .content("[{\"clause_id\":\"c1\",\"decision\":\"approve\","
                                + "\"contract_version\":\"1\",\"content_hash\":\"h1\"}]"))
                .andExpect(status().isNotFound());
    }

    /**
     * 中文审批人：HTTP 头只能装 ISO-8859-1，前端按 UTF-8 百分号编码后放入头，
     * 服务端必须还原成「张三」再落库，否则审计记录里会是一串 %E5%BC%A0。
     */
    @Test
    void approveDecodesPercentEncodedOperator() throws Exception {
        when(reviewService.approveReview(any(), any(), any()))
                .thenReturn(new ApproveResponse()
                        .status(ApproveResponse.StatusEnum.ACCEPTED)
                        .decidedAt(OffsetDateTime.parse("2026-10-23T10:00:00Z")));

        mockMvc.perform(post("/api/v1/reviews/00000000-0000-0000-0000-0000000000e4/approve")
                        .header("X-Operator", "%E5%BC%A0%E4%B8%89")
                        .contentType("application/json")
                        .content("[{\"clause_id\":\"c1\",\"decision\":\"approve\","
                                + "\"contract_version\":\"1\",\"content_hash\":\"h1\"}]"))
                .andExpect(status().isOk());

        ArgumentCaptor<String> operator = ArgumentCaptor.forClass(String.class);
        verify(reviewService).approveReview(any(), any(), operator.capture());
        assertEquals("张三", operator.getValue());
    }

    /** 纯 ASCII 与「名字里带 %」都不该被误解码：前者的原值是语义，后者是非法序列。 */
    @Test
    void decodeOperatorKeepsAsciiAndMalformedInput() {
        assertEquals("alice", ReviewController.decodeOperator("alice"));
        assertEquals("100%", ReviewController.decodeOperator("100%"));
    }
}
