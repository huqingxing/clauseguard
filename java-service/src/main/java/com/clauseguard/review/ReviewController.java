package com.clauseguard.review;

import com.clauseguard.api.model.FindingsPage;
import com.clauseguard.api.model.ReviewCreateRequest;
import com.clauseguard.api.model.ReviewCreateResponse;
import com.clauseguard.api.model.ReviewResult;
import jakarta.validation.Valid;
import org.springframework.core.io.Resource;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
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

import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;

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
     * Stage 12：批注产物下载。两种存储后端两条路径，出口都收在 Java：
     * <ul>
     *   <li>http(s)（MinIO 预签名）→ 302 到 run.completed.artifact.download_url，浏览器跳转后直连
     *       对象存储，不经 Java 转发字节；</li>
     *   <li>file:（本地存储后端，未启 MinIO）→ 直接回附件字节流。本地开发的 download_url 是 file URI，
     *       浏览器拒绝 302 跳到 {@code file://}（ERR_UNSAFE_REDIRECT），只能由 Java 代理；这条分支不是
     *       「为了本机能下载」的临时补丁，删掉下载即失效。</li>
     * </ul>
     * 未回写产物时 404。
     */
    @GetMapping("/{id}/artifact")
    public ResponseEntity<Resource> artifact(@PathVariable("id") String id) {
        return reviewService.artifactDownload(id)
                .<ResponseEntity<Resource>>map(download -> download.body() == null
                        ? ResponseEntity.status(302)
                                .header(HttpHeaders.LOCATION, download.location())
                                .<Resource>build()
                        : ResponseEntity.ok()
                                .contentType(MediaType.parseMediaType(download.contentType()))
                                .header(HttpHeaders.CONTENT_DISPOSITION,
                                        "attachment; filename*=UTF-8''" + encodeFilename(download.filename()))
                                .body(download.body()))
                .orElseGet(() -> ResponseEntity.notFound().build());
    }

    private static String encodeFilename(String filename) {
        return URLEncoder.encode(filename, StandardCharsets.UTF_8).replace("+", "%20");
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
