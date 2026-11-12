package com.clauseguard.review;

import com.clauseguard.api.model.ApproveResponse;
import com.clauseguard.security.SecurityConfig;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.context.annotation.Import;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder;

import java.nio.charset.StandardCharsets;
import java.time.OffsetDateTime;
import java.util.Base64;

import static org.hamcrest.Matchers.containsString;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * AdminReviewController web 切片测试：审批写入落在 /api/v1/admin/reviews 下，需 ADMIN 角色。
 *
 * 关键边界（阶段 14）：
 *  - 匿名 / USER 调 approve → 401 / 403（不再 permitAll）
 *  - ADMIN 调 approve → 200，且审批记录的 operator 来自认证主体（登录账号），不是客户端头
 *  - X-Operator 头保留接收但已废弃：仍出现时忽略其值，operator 仍是 principal
 *
 * 认证用真实 Authorization 头而不是 @WithMockUser：后者绕过 UserDetailsService / BCrypt，
 * 测出来的 401/403 不能证明我们配的规则对。手写 Basic 头走真链路，也不引入 spring-security-test。
 */
@WebMvcTest(AdminReviewController.class)
@AutoConfigureMockMvc
@Import(SecurityConfig.class)
class AdminReviewControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @MockBean
    private ReviewService reviewService;

    private static final String APPROVE_BODY =
            "[{\"clause_id\":\"c1\",\"decision\":\"approve\",\"contract_version\":\"1\",\"content_hash\":\"h1\"}]";

    private static String basic(String username, String password) {
        return "Basic " + Base64.getEncoder()
                .encodeToString((username + ":" + password).getBytes(StandardCharsets.UTF_8));
    }

    private static MockHttpServletRequestBuilder approveAs(String username, String password) {
        return post("/api/v1/admin/reviews/00000000-0000-0000-0000-0000000000e1/approve")
                .header(HttpHeaders.AUTHORIZATION, basic(username, password))
                .contentType(MediaType.APPLICATION_JSON)
                .content(APPROVE_BODY);
    }

    // ---- 鉴权边界（阶段 14 核心验收）----

    @Test
    void approveRejectsAnonymousWith401Json() throws Exception {
        mockMvc.perform(post("/api/v1/admin/reviews/00000000-0000-0000-0000-0000000000e1/approve")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(APPROVE_BODY))
                .andExpect(status().isUnauthorized())
                .andExpect(content().contentTypeCompatibleWith(MediaType.APPLICATION_JSON))
                .andExpect(jsonPath("$.code").value("UNAUTHORIZED"))
                .andExpect(header().string(HttpHeaders.WWW_AUTHENTICATE, containsString("Basic")));
    }

    @Test
    void approveRejectsNonAdminWith403Json() throws Exception {
        mockMvc.perform(approveAs("user", "user123"))
                .andExpect(status().isForbidden())
                .andExpect(content().contentTypeCompatibleWith(MediaType.APPLICATION_JSON))
                .andExpect(jsonPath("$.code").value("FORBIDDEN"));
    }

    /** ADMIN 调 approve → 200，且 operator 取认证主体（admin），不是任何客户端传入的值。 */
    @Test
    void approveUsesAuthenticatedPrincipalAsOperator() throws Exception {
        when(reviewService.approveReview(any(), any(), anyString()))
                .thenReturn(new ApproveResponse()
                        .status(ApproveResponse.StatusEnum.ACCEPTED)
                        .decidedAt(OffsetDateTime.parse("2026-10-23T10:00:00Z")));

        mockMvc.perform(approveAs("admin", "admin123"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("accepted"));

        ArgumentCaptor<String> operator = ArgumentCaptor.forClass(String.class);
        verify(reviewService).approveReview(any(), any(), operator.capture());
        assertEquals("admin", operator.getValue());
    }

    // ---- 从 ReviewController 迁移过来的既有 approve 行为（路径已变，鉴权已加）----

    @Test
    void approveReturns200WithAccepted() throws Exception {
        when(reviewService.approveReview(any(), any(), anyString()))
                .thenReturn(new ApproveResponse()
                        .status(ApproveResponse.StatusEnum.ACCEPTED)
                        .decidedAt(OffsetDateTime.parse("2026-10-23T10:00:00Z")));

        mockMvc.perform(approveAs("admin", "admin123"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("accepted"));
    }

    @Test
    void approveReturns409OnContractChanged() throws Exception {
        doThrow(new ContractChangedException("r"))
                .when(reviewService).approveReview(any(), any(), anyString());

        mockMvc.perform(approveAs("admin", "admin123"))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("CONTRACT_CHANGED"));
    }

    @Test
    void approveReturns404WhenReviewNotFound() throws Exception {
        when(reviewService.approveReview(any(), any(), anyString()))
                .thenThrow(new ReviewNotFoundException("x"));

        mockMvc.perform(approveAs("admin", "admin123"))
                .andExpect(status().isNotFound());
    }

    /**
     * X-Operator 头已废弃：即便客户端仍发百分号编码的中文名，operator 仍取认证主体（admin），
     * 头的值被忽略（仅触发 WARN，由日志侧覆盖）。断言落库的 operator 是 admin 而非「张三」。
     */
    @Test
    void deprecatedXOperatorHeaderIsIgnoredInFavorOfPrincipal() throws Exception {
        when(reviewService.approveReview(any(), any(), anyString()))
                .thenReturn(new ApproveResponse()
                        .status(ApproveResponse.StatusEnum.ACCEPTED)
                        .decidedAt(OffsetDateTime.parse("2026-10-23T10:00:00Z")));

        mockMvc.perform(approveAs("admin", "admin123")
                        .header("X-Operator", "%E5%BC%A0%E4%B8%89"))
                .andExpect(status().isOk());

        ArgumentCaptor<String> operator = ArgumentCaptor.forClass(String.class);
        verify(reviewService).approveReview(any(), any(), operator.capture());
        assertEquals("admin", operator.getValue());
    }

    /** decodeOperator 仅用于废弃头的告警解码，纯 ASCII 与「名字里带 %」都不该被误解码。 */
    @Test
    void decodeOperatorKeepsAsciiAndMalformedInput() {
        assertEquals("alice", AdminReviewController.decodeOperator("alice"));
        assertEquals("100%", AdminReviewController.decodeOperator("100%"));
    }
}
