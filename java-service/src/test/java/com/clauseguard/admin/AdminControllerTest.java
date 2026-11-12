package com.clauseguard.admin;

import com.clauseguard.api.model.AdminHealth;
import com.clauseguard.api.model.ApprovalQueueItem;
import com.clauseguard.api.model.ApprovalQueuePage;
import com.clauseguard.api.model.EvalSummary;
import com.clauseguard.eval.EvalController;
import com.clauseguard.eval.EvalService;
import com.clauseguard.security.SecurityConfig;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.context.annotation.Import;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder;

import java.nio.charset.StandardCharsets;
import java.util.Base64;

import static org.hamcrest.Matchers.containsString;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * AdminController web 切片测试：双栈健康聚合透传 + 管理端鉴权。
 *
 * 显式 @Import(SecurityConfig) 而不是关掉过滤器链：切片默认会套 Spring Security 的默认链
 * （全部要求认证），测出来的 401 是默认链的行为，不能证明我们配的规则对。
 *
 * 认证用真实 Authorization 头而不是 @WithMockUser：后者只是往 SecurityContext 塞一个
 * 假身份，绕过了 UserDetailsService 和 BCrypt。手写单行代码就能走真链路，也就不用为了
 * 一个注解引入 spring-security-test（版本表只允许新增 starter-security）。
 */
@WebMvcTest(controllers = {AdminController.class, EvalController.class})
@Import(SecurityConfig.class)
class AdminControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @MockBean
    private AdminHealthService adminHealthService;

    @MockBean
    private ApprovalQueueService approvalQueueService;

    @MockBean
    private EvalService evalService;

    private static String basic(String username, String password) {
        return "Basic " + Base64.getEncoder()
                .encodeToString((username + ":" + password).getBytes(StandardCharsets.UTF_8));
    }

    private static MockHttpServletRequestBuilder as(String username, String password) {
        return get("/api/v1/admin/health").header(HttpHeaders.AUTHORIZATION, basic(username, password));
    }

    @Test
    void healthReturnsBothStacksForAdmin() throws Exception {
        when(adminHealthService.health()).thenReturn(new AdminHealth()
                .java(new com.clauseguard.api.model.AdminHealthJava()
                        .status(com.clauseguard.api.model.AdminHealthJava.StatusEnum.UP))
                .python(new com.clauseguard.api.model.AdminHealthPython()
                        .status(com.clauseguard.api.model.AdminHealthPython.StatusEnum.UP)
                        .detail(java.util.Map.of("status", "ok"))));

        mockMvc.perform(as("admin", "admin123"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.java.status").value("up"))
                .andExpect(jsonPath("$.python.status").value("up"))
                .andExpect(jsonPath("$.python.detail.status").value("ok"));
    }

    @Test
    void healthMarksPythonDownWhenUnreachable() throws Exception {
        when(adminHealthService.health()).thenReturn(new AdminHealth()
                .java(new com.clauseguard.api.model.AdminHealthJava()
                        .status(com.clauseguard.api.model.AdminHealthJava.StatusEnum.UP))
                .python(new com.clauseguard.api.model.AdminHealthPython()
                        .status(com.clauseguard.api.model.AdminHealthPython.StatusEnum.DOWN)));

        mockMvc.perform(as("admin", "admin123"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.python.status").value("down"));
    }

    @Test
    void healthRejectsAnonymousWith401Json() throws Exception {
        mockMvc.perform(get("/api/v1/admin/health"))
                .andExpect(status().isUnauthorized())
                // 断言 JSON 而不是只看状态码：Spring 默认会把 401 forward 到 /error 出 HTML 页
                .andExpect(content().contentTypeCompatibleWith(MediaType.APPLICATION_JSON))
                .andExpect(jsonPath("$.code").value("UNAUTHORIZED"))
                // RFC 7235 要求 401 带挑战头，否则 curl/浏览器不知道用哪种方案重试
                .andExpect(header().string(HttpHeaders.WWW_AUTHENTICATE, containsString("Basic")));
    }

    @Test
    void wrongPasswordReturnsTheSame401JsonAsAnonymous() throws Exception {
        mockMvc.perform(as("admin", "not-the-password"))
                .andExpect(status().isUnauthorized())
                // 这条踩过坑：BasicAuthenticationFilter 用的是它自己的 entry point，不显式覆盖它，
                // 「密码错」就会被 forward 到 /error 出 Spring 默认错误体，于是同一个接口的 401
                // 有两种响应体（没凭证一种、凭证错一种），前端没法用同一段代码判断。
                .andExpect(content().contentTypeCompatibleWith(MediaType.APPLICATION_JSON))
                .andExpect(jsonPath("$.code").value("UNAUTHORIZED"))
                .andExpect(jsonPath("$.message").value("需要管理员凭证"));
    }

    @Test
    void healthRejectsNonAdminWith403Json() throws Exception {
        mockMvc.perform(as("user", "user123"))
                .andExpect(status().isForbidden())
                .andExpect(content().contentTypeCompatibleWith(MediaType.APPLICATION_JSON))
                .andExpect(jsonPath("$.code").value("FORBIDDEN"));
    }

    // ---- Stage 5：待审批队列 ----

    @Test
    void approvalsRequireAdminCredentials() throws Exception {
        mockMvc.perform(get("/api/v1/admin/approvals"))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value("UNAUTHORIZED"));

        // 带凭证才走到 service：队列是审核者的待办，匿名连「有几条」都不该看到
        mockMvc.perform(get("/api/v1/admin/approvals")
                        .header(HttpHeaders.AUTHORIZATION, basic("admin", "admin123")))
                .andExpect(status().isOk());
    }

    @Test
    void approvalsPassPagingThroughAndReturnQueue() throws Exception {
        when(approvalQueueService.list("WAITING_APPROVAL", 1, 5)).thenReturn(new ApprovalQueuePage()
                .items(java.util.List.of(new ApprovalQueueItem()
                        .reviewId(java.util.UUID.fromString("11111111-1111-4111-8111-111111111111"))
                        .contractId(java.util.UUID.fromString("22222222-2222-4222-8222-222222222222"))
                        .contractTitle("研发外包合同.docx")
                        .riskHighCount(2)
                        .createdAt(java.time.OffsetDateTime.parse("2026-10-27T06:00:00Z"))
                        .waitingSince(java.time.OffsetDateTime.parse("2026-10-27T06:05:00Z"))))
                .page(1)
                .size(5)
                .total(7));

        mockMvc.perform(get("/api/v1/admin/approvals")
                        .param("status", "WAITING_APPROVAL")
                        .param("page", "1")
                        .param("size", "5")
                        .header(HttpHeaders.AUTHORIZATION, basic("admin", "admin123")))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items[0].contract_title").value("研发外包合同.docx"))
                .andExpect(jsonPath("$.items[0].risk_high_count").value(2))
                .andExpect(jsonPath("$.items[0].waiting_since").exists())
                .andExpect(jsonPath("$.page").value(1))
                .andExpect(jsonPath("$.total").value(7));
    }

    @Test
    void unsupportedApprovalStatusReturns400Json() throws Exception {
        when(approvalQueueService.list("COMPLETED", 0, 20))
                .thenThrow(new InvalidApprovalStatusException("COMPLETED"));

        mockMvc.perform(get("/api/v1/admin/approvals")
                        .param("status", "COMPLETED")
                        .header(HttpHeaders.AUTHORIZATION, basic("admin", "admin123")))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("INVALID_APPROVAL_STATUS"));
    }

    // ---- 阶段 14：评测看板（成本结构）纳入 ADMIN 保护 ----

    @Test
    void evalSummaryRejectsAnonymousWith401Json() throws Exception {
        mockMvc.perform(get("/api/v1/admin/eval/summary"))
                .andExpect(status().isUnauthorized())
                .andExpect(content().contentTypeCompatibleWith(MediaType.APPLICATION_JSON))
                .andExpect(jsonPath("$.code").value("UNAUTHORIZED"));
    }

    @Test
    void evalSummaryRejectsNonAdminWith403Json() throws Exception {
        mockMvc.perform(get("/api/v1/admin/eval/summary")
                        .header(HttpHeaders.AUTHORIZATION, basic("user", "user123")))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.code").value("FORBIDDEN"));
    }

    @Test
    void evalSummaryReturns200ForAdmin() throws Exception {
        when(evalService.summary()).thenReturn(new EvalSummary());

        mockMvc.perform(get("/api/v1/admin/eval/summary")
                        .header(HttpHeaders.AUTHORIZATION, basic("admin", "admin123")))
                .andExpect(status().isOk());
    }
}
