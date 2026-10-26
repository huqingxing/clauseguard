package com.clauseguard.review;

import com.clauseguard.api.model.ApproveRequest;
import com.clauseguard.api.model.ApproveResponse;
import com.clauseguard.api.model.FindingsPage;
import com.clauseguard.api.model.ReviewCreateRequest;
import com.clauseguard.api.model.ReviewCreateResponse;
import com.clauseguard.api.model.ReviewResult;
import jakarta.validation.Valid;
import org.springframework.http.HttpHeaders;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.util.List;

/**
 * 审查任务 REST 入口。浏览器只认 Java：创建由 Java 受理并异步编排，事件流经 Java 透传。
 * 不在此处拼装任何 prompt 或解释事件语义。审批是图的一部分（见 Java approveReview 转发到
 * Python resume），本入口只负责受理审批数组 + 操作人并委托 Service。
 */
@RestController
@RequestMapping("/api/v1/reviews")
public class ReviewController {

    private final ReviewService reviewService;

    public ReviewController(ReviewService reviewService) {
        this.reviewService = reviewService;
    }

    @PostMapping
    public ResponseEntity<ReviewCreateResponse> create(
            @Valid @RequestBody ReviewCreateRequest request,
            @RequestHeader(value = "traceparent", required = false) String traceparent) {
        ReviewCreateResponse response = reviewService.createReview(request, traceparent);
        return ResponseEntity.status(202).body(response);
    }

    @PostMapping("/{id}/approve")
    public ResponseEntity<ApproveResponse> approve(
            @PathVariable("id") String id,
            @RequestHeader("X-Operator") String operator,
            @RequestBody List<ApproveRequest> approvals) {
        ApproveResponse response = reviewService.approveReview(id, approvals, operator);
        return ResponseEntity.ok(response);
    }

    /**
     * Stage 12：审查结果（含 findings 明细）。明细来自 run.completed.findings
     * （events.schema.json 单一事实源），运行未完成时为空数组。
     */
    @GetMapping("/{id}")
    public ResponseEntity<ReviewResult> get(@PathVariable("id") String id) {
        return ResponseEntity.ok(reviewService.getReviewResult(id));
    }

    /** Stage 12：分页 findings（同样来自 run.completed，Java 侧只做切片）。 */
    @GetMapping("/{id}/findings")
    public ResponseEntity<FindingsPage> findings(
            @PathVariable("id") String id,
            @RequestParam(value = "page", required = false, defaultValue = "0") int page,
            @RequestParam(value = "size", required = false, defaultValue = "20") int size) {
        return ResponseEntity.ok(reviewService.findingsPage(id, Math.max(0, page), Math.min(100, Math.max(1, size))));
    }

    /**
     * Stage 12：批注产物下载。302 到 run.completed.artifact.download_url（MinIO 预签名，
     * 限时有效）——浏览器经 Java 跳转，不直连对象存储。未回写时 404。
     */
    @GetMapping("/{id}/artifact")
    public ResponseEntity<Void> artifact(@PathVariable("id") String id) {
        return reviewService.artifactDownloadUrl(id)
                .map(url -> ResponseEntity.status(302)
                        .header(HttpHeaders.LOCATION, url)
                        .<Void>build())
                .orElseGet(() -> ResponseEntity.notFound().build());
    }

    @GetMapping("/{id}/stream")
    public SseEmitter stream(
            @PathVariable("id") String id,
            @RequestHeader(value = "Last-Event-ID", required = false) String lastEventId) {
        // 下游熔断打开：直接返回 503，前端可重试（同时事件流本身会补 run.failed）。
        if (reviewService.isAgentUnavailable()) {
            throw new com.clauseguard.agent.AgentUnavailableException("python agent circuit is open");
        }
        return reviewService.stream(id, lastEventId);
    }
}
