package com.clauseguard.review;

import com.clauseguard.api.model.ApproveRequest;
import com.clauseguard.api.model.ApproveResponse;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.util.List;

/**
 * 审批写入入口：落在 /api/v1/admin/reviews 下，由 SecurityConfig 的 hasRole("ADMIN") 统一保护。
 *
 * <p>操作人来源改造（阶段 14）：不再信任客户端自称的 X-Operator 头，而是取认证主体
 * （Basic 登录账号 = authentication.getName()）。理由：X-Operator 是客户端自己填的，
 * 等于谁都能以别人的名义签字，审计表不可信。
 *
 * <p>X-Operator 头保留接收但已废弃：仍出现的旧调用方会收到 WARN，值被忽略，不会写入审批记录。
 * 一旦所有客户端升级完毕，可彻底删除该头与 decodeOperator。
 */
@RestController
@RequestMapping("/api/v1/admin/reviews")
public class AdminReviewController {

    private static final Logger log = LoggerFactory.getLogger(AdminReviewController.class);

    private final ReviewService reviewService;

    public AdminReviewController(ReviewService reviewService) {
        this.reviewService = reviewService;
    }

    @PostMapping("/{id}/approve")
    public ResponseEntity<ApproveResponse> approve(
            @PathVariable("id") String id,
            @RequestBody List<ApproveRequest> approvals,
            Authentication authentication,
            @RequestHeader(value = "X-Operator", required = false) String operatorHeader) {
        // 操作人以认证主体为准，不取客户端头。
        String operator = authentication.getName();
        if (operatorHeader != null && !operatorHeader.isBlank()) {
            // 旧调用方仍在发 X-Operator：解码后仅用于告警，值不参与任何持久化。
            log.warn("X-Operator 头已废弃：审批操作人现以认证主体[{}]为准，忽略该头的值[{}]",
                    operator, decodeOperator(operatorHeader));
        }
        ApproveResponse response = reviewService.approveReview(id, approvals, operator);
        return ResponseEntity.ok(response);
    }

    /**
     * 解出操作人姓名（仅用于废弃头的解码告警）。
     *
     * <p>HTTP 头按 RFC 7230 只能承载 ISO-8859-1，浏览器遇到中文直接拒绝发请求，
     * 所以前端曾按 UTF-8 百分号编码后放头里，这里还原。不含 '%' 的值原样返回。
     */
    static String decodeOperator(String raw) {
        if (raw == null || !raw.contains("%")) {
            return raw;
        }
        try {
            return URLDecoder.decode(raw, StandardCharsets.UTF_8);
        } catch (IllegalArgumentException ex) {
            // 非法百分号序列就当原文，不因编码猜测丢掉信息。
            return raw;
        }
    }
}
