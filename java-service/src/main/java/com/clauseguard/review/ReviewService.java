package com.clauseguard.review;

import com.clauseguard.agent.AgentUnavailableException;
import com.clauseguard.agent.PythonAgentClient;
import com.clauseguard.api.model.ApproveRequest;
import com.clauseguard.api.model.ApproveResponse;
import com.clauseguard.api.model.ReviewCreateRequest;
import com.clauseguard.api.model.ReviewCreateResponse;
import com.clauseguard.events.AgentEvent;
import com.clauseguard.events.EventReplayStore;
import com.clauseguard.events.StoredEvent;
import com.clauseguard.review.ContractChangedException;
import com.clauseguard.review.domain.Approval;
import com.clauseguard.review.domain.Review;
import com.clauseguard.review.domain.ReviewEvent;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.github.resilience4j.circuitbreaker.CircuitBreaker;
import io.github.resilience4j.circuitbreaker.CircuitBreakerRegistry;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.net.URI;
import java.time.Duration;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArraySet;

/**
 * 审查任务编排：落库、异步触发 Python、把事件流原样透传给前端并落库/可断线续传。
 * 运行态只在此处的实例字段里（无全局可变状态）；事件语义完全由 Python 决定。
 */
@Service
public class ReviewService {

    private static final Logger logger = LoggerFactory.getLogger(ReviewService.class);
    private static final Set<String> TERMINAL_TYPES = Set.of("run.completed", "run.failed");
    private static final String TYPE_RUN_STARTED = "run.started";
    private static final String TYPE_INTERRUPT = "interrupt";
    private static final String TYPE_RUN_COMPLETED = "run.completed";
    private static final String TYPE_RUN_FAILED = "run.failed";

    private final ReviewRepository reviewRepository;
    private final ReviewEventRepository eventRepository;
    private final ApprovalRepository approvalRepository;
    private final EventReplayStore replayStore;
    private final PythonAgentClient agentClient;
    private final CircuitBreaker circuitBreaker;
    private final ObjectMapper mapper;

    private final Map<String, Set<LiveSubscription>> liveEmitters = new ConcurrentHashMap<>();
    private final Map<String, Boolean> subscribing = new ConcurrentHashMap<>();

    public ReviewService(
            ReviewRepository reviewRepository,
            ReviewEventRepository eventRepository,
            ApprovalRepository approvalRepository,
            EventReplayStore replayStore,
            PythonAgentClient agentClient,
            CircuitBreakerRegistry circuitBreakerRegistry,
            ObjectMapper mapper) {
        this.reviewRepository = reviewRepository;
        this.eventRepository = eventRepository;
        this.approvalRepository = approvalRepository;
        this.replayStore = replayStore;
        this.agentClient = agentClient;
        this.circuitBreaker = circuitBreakerRegistry.circuitBreaker("pythonAgent");
        this.mapper = mapper;
    }

    /**
     * 创建审查任务：落库 → 生成 review_id → 异步触发 Python（不阻塞返回）。
     * traceparent 来自调用方请求头，缺失时由网关生成，随后透传给 Python。
     */
    public ReviewCreateResponse createReview(ReviewCreateRequest request, String incomingTraceparent) {
        String reviewId = UUID.randomUUID().toString();
        Review review = new Review();
        review.setId(reviewId);
        review.setContractId(request.getContractId().toString());
        // Stage 11：合同版本来自请求（前端「仅重审变更」会带上真实版本号）。
        // 落库版本与透传给 Python 的版本必须是同一个——人工审批的乐观锁与增量 diff
        // 都以它为基准，两处漂移会让审批基线对不上、diff 拿错基线。
        int version = request.getVersion() == null ? 0 : Math.max(0, request.getVersion());
        review.setVersion(version == 0 ? 1 : version);
        review.setStatus(ReviewStatus.RUNNING);
        review.setCreatedAt(Instant.now());
        reviewRepository.save(review);

        String streamUrl = "/api/v1/reviews/" + reviewId + "/stream";
        String traceparent = (incomingTraceparent != null && !incomingTraceparent.isBlank())
                ? incomingTraceparent : generateTraceparent();
        String mode = request.getMode() == null ? "full" : request.getMode().getValue();
        int baseVersion = request.getBaseVersion() == null ? 0 : Math.max(0, request.getBaseVersion());
        subscribeToAgent(reviewId, request.getContractId().toString(), traceparent, version, mode, baseVersion);
        return new ReviewCreateResponse()
                .reviewId(UUID.fromString(reviewId))
                .streamUrl(URI.create(streamUrl));
    }

    /**
     * 订阅 SSE 事件流。仅当该 review 尚未在订阅时才真正发起调用，防止重复订阅。
     */
    private void subscribeToAgent(
            String reviewId,
            String contractId,
            String traceparent,
            int version,
            String mode,
            int baseVersion) {
        if (subscribing.putIfAbsent(reviewId, Boolean.TRUE) != null) {
            return;
        }
        agentClient.streamRun(reviewId, contractId, traceparent, version, mode, baseVersion)
                .subscribe(
                        event -> handleAgentEvent(reviewId, event),
                        error -> {
                            subscribing.remove(reviewId);
                            logger.warn("agent stream failed for review {}: {}", reviewId, error.getMessage());
                            handleAgentError(reviewId, error);
                        },
                        () -> subscribing.remove(reviewId));
    }

    /**
     * 处理一条来自 Python 的事件：落库 → 写重放存储 → 实时扇出；终态事件收尾订阅。
     */
    void handleAgentEvent(String reviewId, AgentEvent event) {
        String type = event.type();
        // 捕获 Python 生成的 run_id（来自 run.started），供 approve 时 resume 定位 thread_id。
        if (TYPE_RUN_STARTED.equals(type)) {
            String runId = readRunId(event.rawJson());
            if (runId != null) {
                reviewRepository.findById(reviewId).ifPresent(r -> {
                    r.setRunId(runId);
                    reviewRepository.save(r);
                });
            }
        }
        // 捕获 interrupt 基线（contract_version / content_hash / 逐条款待审项），
        // 并将审查置 AWAITING_APPROVAL——审批乐观锁与回显均依赖此基线。
        if (TYPE_INTERRUPT.equals(type)) {
            JsonNode payload = readInterruptPayload(event.rawJson());
            if (payload != null) {
                String contentHash = textOrNull(payload.path("content_hash"));
                String contractVersion = textOrNull(payload.path("contract_version"));
                String pending = payload.toString();
                reviewRepository.findById(reviewId).ifPresent(r -> {
                    r.setContentHash(contentHash);
                    r.setContractVersion(contractVersion);
                    r.setPendingPayload(pending);
                    r.setStatus(ReviewStatus.AWAITING_APPROVAL);
                    reviewRepository.save(r);
                });
            }
        }
        if (TYPE_RUN_COMPLETED.equals(type)) {
            reviewRepository.findById(reviewId).ifPresent(r -> {
                r.setStatus(ReviewStatus.COMPLETED);
                reviewRepository.save(r);
            });
        }
        if (TYPE_RUN_FAILED.equals(type)) {
            reviewRepository.findById(reviewId).ifPresent(r -> {
                r.setStatus(ReviewStatus.FAILED);
                reviewRepository.save(r);
            });
        }
        ReviewEvent entity = new ReviewEvent();
        entity.setReviewId(reviewId);
        entity.setSeq(event.seq());
        entity.setType(event.type());
        entity.setPayload(event.rawJson());
        entity.setTs(parseTs(event.ts()));
        eventRepository.save(entity);

        replayStore.append(reviewId, event.seq(), event.type(), event.rawJson());
        fanout(reviewId, event);

        if (TERMINAL_TYPES.contains(event.type())) {
            subscribing.remove(reviewId);
        }
    }

    /**
     * Python 调用失败（含熔断打开）：补一条合规 run.failed 事件，保证前端总能收到终态。
     */
    void handleAgentError(String reviewId, Throwable error) {
        long seq = replayStore.currentMaxSeq(reviewId) + 1;
        if (seq < 0) {
            seq = 0;
        }
        String ts = nowRfc3339();
        AgentEvent failed = agentClient.buildRunFailedEvent(
                seq, ts, "AGENT_UNAVAILABLE", "Python agent unreachable: " + errorMessage(error), true);
        handleAgentEvent(reviewId, failed);
    }

    /**
     * 高风险条款人工审批决定：乐观锁校验 → 落库审批记录（含操作人/时间/前后措辞）→ 转发 Python resume。
     *
     * 原子性（验收 c）：contract_version/content_hash 与中断基线逐条比对，任一不匹配即整体抛
     * ContractChangedException（409 CONTRACT_CHANGED），在写入任何审批记录/触发 resume 之前判定，
     * 因此不会产生部分执行。审批记录与「恢复运行」均在通过校验后才发生。
     */
    public ApproveResponse approveReview(String reviewId, List<ApproveRequest> approvals, String operator) {
        Review review = reviewRepository.findById(reviewId)
                .orElseThrow(() -> new ReviewNotFoundException(reviewId));
        if (review.getStatus() != ReviewStatus.AWAITING_APPROVAL) {
            // 非待审态（已审批/仍在跑）视为冲突，避免重复或错序审批。
            throw new ContractChangedException(reviewId);
        }
        if (approvals == null || approvals.isEmpty()) {
            throw new ContractChangedException(reviewId);
        }
        // 乐观锁：逐条比对基线；任一不匹配即整体拒绝（原子，无部分写入）。
        for (ApproveRequest req : approvals) {
            if (!Objects.equals(req.getContractVersion(), review.getContractVersion())
                    || !Objects.equals(req.getContentHash(), review.getContentHash())) {
                throw new ContractChangedException(reviewId);
            }
        }
        // 解析中断时缓存的逐条款待审项，供审批记录留痕（前措辞 / 风险档）。
        Map<String, JsonNode> pendingByClause = parsePendingItems(review.getPendingPayload());
        Instant now = Instant.now();
        // 落库审批记录（含操作人、时间、前后措辞、风险档）；先写记录再触发 resume。
        for (ApproveRequest req : approvals) {
            Approval approval = new Approval();
            approval.setReviewId(reviewId);
            approval.setClauseId(req.getClauseId());
            approval.setDecision(req.getDecision() == null ? null : req.getDecision().getValue());
            approval.setComment(req.getComment());
            approval.setOperator(operator);
            approval.setCreatedAt(now);
            JsonNode item = pendingByClause.get(req.getClauseId());
            if (item != null) {
                approval.setBeforeWording(textOrNull(item.path("clause_text")));
                approval.setRisk(textOrNull(item.path("risk")));
            }
            approval.setAfterWording(req.getEditedText());
            approvalRepository.save(approval);
        }
        // 状态切回 RUNNING；恢复后由 run.completed 事件置 COMPLETED。
        review.setStatus(ReviewStatus.RUNNING);
        reviewRepository.save(review);
        // 转发到 Python resume：审批结果数组即 interrupt 的返回值。以 SSE 订阅续跑事件，
        // 由本服务的 handleAgentEvent 把完成事件续接到前端订阅的 SSE（前端无需重连即收终态）。
        String runId = review.getRunId();
        if (runId != null) {
            subscribing.put(reviewId, Boolean.TRUE);
            agentClient.streamResume(runId, approvals, generateTraceparent())
                    .subscribe(
                            event -> handleAgentEvent(reviewId, event),
                            error -> {
                                subscribing.remove(reviewId);
                                logger.warn(
                                        "resume stream failed for review {}: {}", reviewId, error.getMessage());
                            },
                            () -> subscribing.remove(reviewId));
        }
        return new ApproveResponse()
                .status(ApproveResponse.StatusEnum.ACCEPTED)
                .decidedAt(OffsetDateTime.now(ZoneOffset.UTC));
    }

    private static String textOrNull(JsonNode node) {
        if (node == null || node.isNull()) {
            return null;
        }
        return node.asText();
    }

    private String readRunId(String rawJson) {
        try {
            JsonNode node = mapper.readTree(rawJson);
            return textOrNull(node.path("run_id"));
        } catch (Exception e) {
            logger.warn("parse run.started failed: {}", e.getMessage());
            return null;
        }
    }

    private JsonNode readInterruptPayload(String rawJson) {
        try {
            JsonNode node = mapper.readTree(rawJson);
            JsonNode payload = node.path("payload");
            return (payload.isMissingNode() || payload.isNull()) ? null : payload;
        } catch (Exception e) {
            logger.warn("parse interrupt failed: {}", e.getMessage());
            return null;
        }
    }

    private Map<String, JsonNode> parsePendingItems(String pendingPayload) {
        Map<String, JsonNode> byClause = new HashMap<>();
        if (pendingPayload == null || pendingPayload.isBlank()) {
            return byClause;
        }
        try {
            JsonNode node = mapper.readTree(pendingPayload);
            JsonNode items = node.path("items");
            if (items.isArray()) {
                for (JsonNode item : items) {
                    String clauseId = textOrNull(item.path("clause_id"));
                    if (clauseId != null) {
                        byClause.put(clauseId, item);
                    }
                }
            }
        } catch (Exception e) {
            logger.warn("parse pending_payload failed: {}", e.getMessage());
        }
        return byClause;
    }

    private void fanout(String reviewId, AgentEvent event) {
        Set<LiveSubscription> subscriptions = liveEmitters.get(reviewId);
        if (subscriptions == null) {
            return;
        }
        for (LiveSubscription sub : subscriptions) {
            if (event.seq() <= sub.afterSeq()) {
                continue;
            }
            try {
                sub.emitter().send(SseEmitter.event()
                        .id(String.valueOf(event.seq()))
                        .data(event.rawJson()));
                if (TERMINAL_TYPES.contains(event.type())) {
                    sub.emitter().complete();
                    subscriptions.remove(sub);
                }
            } catch (Exception ex) {
                subscriptions.remove(sub);
            }
        }
    }

    /**
     * 打开前端 SSE 订阅：先按 Last-Event-ID 从重放存储补齐缺失事件，再注册实时扇出。
     */
    public SseEmitter stream(String reviewId, String lastEventId) {
        reviewRepository.findById(reviewId)
                .orElseThrow(() -> new ReviewNotFoundException(reviewId));

        SseEmitter emitter = new SseEmitter(Duration.ofMinutes(10).toMillis());
        long afterSeq = parseLastEventId(lastEventId);

        // 先注册实时订阅，避免重放与实时之间漏事件；afterSeq 用于去重。
        LiveSubscription sub = new LiveSubscription(emitter, afterSeq);
        liveEmitters.computeIfAbsent(reviewId, k -> new CopyOnWriteArraySet<>()).add(sub);

        List<StoredEvent> replay = replayStore.replaySince(reviewId, afterSeq);
        boolean completedDuringReplay = false;
        for (StoredEvent e : replay) {
            try {
                emitter.send(SseEmitter.event().id(String.valueOf(e.seq())).data(e.payload()));
            } catch (Exception ex) {
                break;
            }
            if (TERMINAL_TYPES.contains(e.type())) {
                completedDuringReplay = true;
            }
        }

        // 运行已结束则重放后直接收尾；否则保持连接等待实时事件。
        if (completedDuringReplay) {
            Set<LiveSubscription> subs = liveEmitters.get(reviewId);
            if (subs != null) {
                subs.remove(sub);
            }
            emitter.complete();
        }

        emitter.onCompletion(() -> removeLive(reviewId, sub));
        emitter.onTimeout(() -> {
            emitter.complete();
            removeLive(reviewId, sub);
        });
        emitter.onError((ex) -> removeLive(reviewId, sub));
        return emitter;
    }

    private void removeLive(String reviewId, LiveSubscription sub) {
        Set<LiveSubscription> subs = liveEmitters.get(reviewId);
        if (subs != null) {
            subs.remove(sub);
        }
    }

    /**
     * 熔断打开时视为下游不可用，供 Controller 直接返回 503。
     */
    public boolean isAgentUnavailable() {
        return circuitBreaker.getState() == CircuitBreaker.State.OPEN;
    }

    /**
     * W3C traceparent：00-<32hex trace>-<16hex span>-01。无传入时由网关生成。
     */
    public String generateTraceparent() {
        return "00-" + UUID.randomUUID().toString().replace("-", "")
                + "-" + UUID.randomUUID().toString().replace("-", "").substring(0, 16)
                + "-01";
    }

    private long parseLastEventId(String lastEventId) {
        if (lastEventId == null || lastEventId.isBlank()) {
            return -1L;
        }
        try {
            return Long.parseLong(lastEventId.trim());
        } catch (NumberFormatException e) {
            return -1L;
        }
    }

    private Instant parseTs(String ts) {
        if (ts == null || ts.isBlank()) {
            return null;
        }
        try {
            return OffsetDateTime.parse(ts).toInstant();
        } catch (Exception e) {
            try {
                return Instant.parse(ts);
            } catch (Exception ex) {
                return null;
            }
        }
    }

    private String nowRfc3339() {
        return DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss.SSSSSSxxx")
                .format(OffsetDateTime.now(ZoneOffset.UTC));
    }

    private String errorMessage(Throwable error) {
        String msg = error.getMessage();
        return msg == null ? error.getClass().getSimpleName() : msg;
    }

    /**
     * 实时订阅句柄：携带已重放的截止 seq，用于实时扇出去重。
     */
    private record LiveSubscription(SseEmitter emitter, long afterSeq) {
    }
}
