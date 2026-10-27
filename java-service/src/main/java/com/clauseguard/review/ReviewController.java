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

import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
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
        ApproveResponse response = reviewService.approveReview(id, approvals, decodeOperator(operator));
        return ResponseEntity.ok(response);
    }

    /**
     * 解出操作人姓名。
     *
     * HTTP 头按 RFC 7230 只能承载 ISO-8859-1，浏览器遇到中文直接拒绝发请求
     * （"String contains non ISO-8859-1 code point"），所以前端按 UTF-8 百分号编码后放头里，
     * 这里还原。不含 '%' 的值原样返回：纯 ASCII 调用方（脚本/测试）不受影响。
     * 放在 Controller 是因为这属于传输层编解码，不是业务规则。
     */
    static String decodeOperator(String raw) {
        if (raw == null || !raw.contains("%")) {
            return raw;
        }
        try {
            return URLDecoder.decode(raw, StandardCharsets.UTF_8);
        } catch (IllegalArgumentException ex) {
            // 非法百分号序列（如用户名字里真有 '%'）就当原文，不因编码猜测丢掉审批人。
            return raw;
        }
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
            @RequestParam(value = "since", required = false) Long since,
            @RequestHeader(value = "Last-Event-ID", required = false) String lastEventId) {
        // 下游熔断打开：直接返回 503，前端可重试（同时事件流本身会补 run.failed）。
        if (reviewService.isAgentUnavailable()) {
            throw new com.clauseguard.agent.AgentUnavailableException("python agent circuit is open");
        }
        return reviewService.stream(id, resumeAfter(since, lastEventId));
    }

    /**
     * 断点从哪续：query `since` 优先，其次 `Last-Event-ID`，都没有就从头回放（-1）。
     *
     * <p>为什么两条通道都要：浏览器原生 EventSource 自动重连会带 Last-Event-ID，但前端为了
     * 控制退避与状态提示是自己 new EventSource 的，新实例不继承这个头，只能把 lastSeq 放进 URL。
     * 两种输入必须在这里折成同一个 afterSeq——分头解析就会长出两套「我看到第几条」的语义。
     */
    static long resumeAfter(Long since, String lastEventId) {
        if (since != null) {
            // 负数不是合法断点（最小是 -1 = 从头），夹回来避免把「全量回放」写成别的意思。
            return Math.max(-1L, since);
        }
        if (lastEventId == null || lastEventId.isBlank()) {
            return -1L;
        }
        try {
            return Long.parseLong(lastEventId.trim());
        } catch (NumberFormatException e) {
            return -1L;
        }
    }
}
