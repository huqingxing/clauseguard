package com.clauseguard.review;

import com.clauseguard.agent.AgentUnavailableException;
import com.clauseguard.agent.PythonAgentClient;
import com.clauseguard.api.model.ApproveRequest;
import com.clauseguard.api.model.ApproveResponse;
import com.clauseguard.api.model.ContractDiff;
import com.clauseguard.api.model.Finding;
import com.clauseguard.api.model.FindingCitationsInner;
import com.clauseguard.api.model.FindingsPage;
import com.clauseguard.api.model.ReviewCreateRequest;
import com.clauseguard.api.model.ReviewCreateResponse;
import com.clauseguard.api.model.ReviewResult;
import com.clauseguard.contract.ContractNotFoundException;
import com.clauseguard.contract.ContractRepository;
import com.clauseguard.contract.domain.Contract;
import com.clauseguard.events.AgentEvent;
import com.clauseguard.events.EventReplayStore;
import com.clauseguard.events.StoredEvent;
import com.clauseguard.observability.TraceContextSupport;
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
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;
import java.util.Optional;
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
    private final ContractRepository contractRepository;
    private final TraceContextSupport traceSupport;

    private final Map<String, Set<LiveSubscription>> liveEmitters = new ConcurrentHashMap<>();
    private final Map<String, Boolean> subscribing = new ConcurrentHashMap<>();

    public ReviewService(
            ReviewRepository reviewRepository,
            ReviewEventRepository eventRepository,
            ApprovalRepository approvalRepository,
            EventReplayStore replayStore,
            PythonAgentClient agentClient,
            CircuitBreakerRegistry circuitBreakerRegistry,
            ObjectMapper mapper,
            ContractRepository contractRepository,
            TraceContextSupport traceSupport) {
        this.reviewRepository = reviewRepository;
        this.eventRepository = eventRepository;
        this.approvalRepository = approvalRepository;
        this.replayStore = replayStore;
        this.agentClient = agentClient;
        this.circuitBreaker = circuitBreakerRegistry.circuitBreaker("pythonAgent");
        this.mapper = mapper;
        this.contractRepository = contractRepository;
        this.traceSupport = traceSupport;
    }

    /**
     * 创建审查任务：校验合同存在 → 落库 → 生成 review_id → 异步触发 Python（不阻塞返回）。
     * traceparent 来自调用方请求头，缺失时由网关生成，随后透传给 Python。
     *
     * Stage 12：合同必须先经上传端点落库（合同不存在即 404，不启动空跑）；
     * 版本缺省取合同当前版本，source（文件在哪/怎么取）随运行请求透传给 Python。
     */
    public ReviewCreateResponse createReview(ReviewCreateRequest request, String incomingTraceparent) {
        // Stage 14：Java 侧这一段（校验 + 落库 + 触发）单独成一个 span，并作为
        // 透传给 Python 的 traceparent 来源——Python 的每个节点 span 都是它的子 span。
        String mode = request.getMode() == null ? "full" : request.getMode().getValue();
        Map<String, String> tags = new LinkedHashMap<>();
        tags.put("clauseguard.contract.id", String.valueOf(request.getContractId()));
        tags.put("clauseguard.review.mode", mode);
        return traceSupport.inSpan(
                "review.create", tags, () -> doCreateReview(request, incomingTraceparent, mode));
    }

    private ReviewCreateResponse doCreateReview(
            ReviewCreateRequest request, String incomingTraceparent, String mode) {
        String contractId = request.getContractId().toString();
        Contract contract = contractRepository.findFirstByIdOrderByVersionDesc(contractId)
                .orElseThrow(() -> new ContractNotFoundException(contractId));
        String reviewId = UUID.randomUUID().toString();
        Review review = new Review();
        review.setId(reviewId);
        review.setContractId(contractId);
        // Stage 11：合同版本来自请求（前端「仅重审变更」会带上真实版本号）。
        // 落库版本与透传给 Python 的版本必须是同一个——人工审批的乐观锁与增量 diff
        // 都以它为基准，两处漂移会让审批基线对不上、diff 拿错基线。
        // Stage 12：未显式指定时取合同当前版本（上传后直接审查的默认路径）。
        int requested = request.getVersion() == null ? 0 : Math.max(0, request.getVersion());
        int version = requested > 0 ? requested : contract.getVersion();
        review.setVersion(version);
        review.setStatus(ReviewStatus.RUNNING);
        review.setCreatedAt(Instant.now());
        reviewRepository.save(review);

        String streamUrl = "/api/v1/reviews/" + reviewId + "/stream";
        // 当前 span（review.create）优先：它才是本服务此刻的真实上下文；未启用追踪时
        // 退化为透传调用方头或本地生成，保证 Python 侧始终有一条可聚合的 trace。
        String traceparent = traceSupport.resolveTraceparent(incomingTraceparent);
        int baseVersion = request.getBaseVersion() == null ? 0 : Math.max(0, request.getBaseVersion());
        subscribeToAgent(
                reviewId, contractId, traceparent, version, mode, baseVersion, sourceOf(contractId, contract));
        return new ReviewCreateResponse()
                .reviewId(UUID.fromString(reviewId))
                .streamUrl(URI.create(streamUrl));
    }

    /**
     * Stage 12：构造合同源描述。download_url 指向 Java 自己的内容端点——Python 经 REST
     * 拉文件（铁律 1：不直连业务库/存储），Java 不解释文件内容，只告诉编排层「从哪拿」。
     */
    private static Map<String, Object> sourceOf(String contractId, Contract contract) {
        Map<String, Object> source = new LinkedHashMap<>();
        source.put("object_key", contract.getStorageKey());
        source.put("download_url", "/api/v1/contracts/" + contractId + "/content");
        source.put("content_type", contract.getContentType());
        source.put("version", contract.getVersion());
        source.put("filename", contract.getFilename());
        return source;
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
            int baseVersion,
            Map<String, Object> source) {
        if (subscribing.putIfAbsent(reviewId, Boolean.TRUE) != null) {
            return;
        }
        agentClient.streamRun(reviewId, contractId, traceparent, version, mode, baseVersion, source)
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
     * 打开前端 SSE 订阅：先按断点从重放存储补齐缺失事件，再注册实时扇出。
     *
     * 只认一个 afterSeq：`?since=` 与 `Last-Event-ID` 的折算放在 Controller，
     * 否则「我看到第几条」会出现两套解析规则，边界上必然对不齐。
     */
    public SseEmitter stream(String reviewId, long afterSeq) {
        reviewRepository.findById(reviewId)
                .orElseThrow(() -> new ReviewNotFoundException(reviewId));

        SseEmitter emitter = new SseEmitter(Duration.ofMinutes(10).toMillis());

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
     * W3C traceparent：优先取当前 span（追踪启用时），否则本地生成。
     *
     * <p>审批恢复路径用它：恢复是同一笔业务操作的续跑，若当前有活动 span 就沿用它，
     * 审计侧看到的仍是同一条 trace 的后续片段。
     */
    public String generateTraceparent() {
        return traceSupport.resolveTraceparent(null);
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

    // ---- Stage 12 查询面：run.completed 的 payload 是唯一事实源，查询只做整形 ----

    /**
     * 审查结果（含 findings 明细，来自 run.completed.findings，已由 Python 剔除 stale）。
     * 运行未完成/失败时 findings 为空数组——前端据此区分「还没有结论」与「没有风险」。
     */
    public ReviewResult getReviewResult(String reviewId) {
        Review review = reviewRepository.findById(reviewId)
                .orElseThrow(() -> new ReviewNotFoundException(reviewId));
        ReviewResult result = new ReviewResult();
        result.setReviewId(UUID.fromString(review.getId()));
        result.setContractId(UUID.fromString(review.getContractId()));
        result.setVersion(review.getVersion());
        result.setRunId(review.getRunId());
        result.setStatus(ReviewResult.StatusEnum.fromValue(review.getStatus().name().toLowerCase(java.util.Locale.ROOT)));
        result.setCreatedAt(review.getCreatedAt().atOffset(ZoneOffset.UTC));
        result.setFindings(toFindings(latestCompletedPayload(reviewId).orElse(null)));
        return result;
    }

    /** 分页 findings：先取全量再切片（事件里的明细本身不分页，演示规模无性能问题）。 */
    public FindingsPage findingsPage(String reviewId, int page, int size) {
        reviewRepository.findById(reviewId)
                .orElseThrow(() -> new ReviewNotFoundException(reviewId));
        List<Finding> all = toFindings(latestCompletedPayload(reviewId).orElse(null));
        int from = Math.min(page * size, all.size());
        int to = Math.min(from + size, all.size());
        return new FindingsPage()
                .items(all.subList(from, to))
                .page(page)
                .size(size)
                .total(all.size());
    }

    /**
     * 批注产物下载 URL：从 run.completed 的 artifact 字段解析预签名 URL。
     * 前端只认 Java（Controller 302 跳转到该 URL），不直连 MinIO。
     */
    public Optional<String> artifactDownloadUrl(String reviewId) {
        return latestCompletedPayload(reviewId)
                .map(payload -> payload.path("artifact"))
                .filter(node -> node.isObject() && node.has("download_url"))
                .map(node -> node.path("download_url").asText());
    }

    /**
     * 合同版本 diff：取该合同（目标版本或最近一次）审查的 incremental 账本透传。
     * Python diff 是单一事实源，Java 不重新计算条款差异。
     */
    public ContractDiff contractDiff(String contractId, Integer targetVersion) {
        Optional<Review> found = targetVersion == null
                ? reviewRepository.findFirstByContractIdOrderByCreatedAtDesc(contractId)
                : reviewRepository.findFirstByContractIdAndVersionOrderByCreatedAtDesc(contractId, targetVersion);
        ContractDiff diff = new ContractDiff();
        diff.setContractId(UUID.fromString(contractId));
        if (found.isEmpty()) {
            // 「这个版本还没审过」不是「资源不存在」：真相是账本为空。
            // 之前这里抛 404，前端每次切到刚上传、还没跑过审查的新版本都会往控制台打一条红色错误，
            // 报障的人第一眼看到的是「接口坏了」。合同本身不存在才该 404（由 Controller 先验一次）。
            diff.setTargetVersion(targetVersion == null ? 0 : targetVersion);
            diff.setBaselineVersion(0);
            diff.setChanged(List.of());
            diff.setAdded(List.of());
            diff.setRemoved(List.of());
            diff.setStaleFindings(0);
            return diff;
        }
        Review review = found.get();
        diff.setTargetVersion(review.getVersion());
        JsonNode incremental = latestCompletedPayload(review.getId())
                .map(payload -> payload.path("incremental"))
                .orElse(null);
        if (incremental == null || !incremental.isObject()) {
            // 尚无 completed 事件（审查未跑完）：空账本，前端展示「无对比数据」空态。
            diff.setBaselineVersion(0);
            diff.setChanged(List.of());
            diff.setAdded(List.of());
            diff.setRemoved(List.of());
            diff.setStaleFindings(0);
            return diff;
        }
        diff.setBaselineVersion(incremental.path("baseline_version").asInt(0));
        diff.setChanged(stringList(incremental.path("changed_clause_ids")));
        diff.setAdded(stringList(incremental.path("added_clause_ids")));
        diff.setRemoved(stringList(incremental.path("removed_clause_ids")));
        diff.setStaleFindings(incremental.path("stale_findings").asInt(0));
        return diff;
    }

    private Optional<JsonNode> latestCompletedPayload(String reviewId) {
        List<ReviewEvent> events =
                eventRepository.findByReviewIdAndTypeOrderBySeqAsc(reviewId, TYPE_RUN_COMPLETED);
        if (events.isEmpty()) {
            return Optional.empty();
        }
        // seq 升序取最后一条：resume 后同一 review 会有第二条 run.completed（审批后续跑），
        // 以最新一条为准（含 render 产物与最终 findings）。
        ReviewEvent last = events.get(events.size() - 1);
        try {
            return Optional.ofNullable(mapper.readTree(last.getPayload()));
        } catch (Exception e) {
            logger.warn("parse run.completed failed for review {}: {}", reviewId, e.getMessage());
            return Optional.empty();
        }
    }

    private static List<Finding> toFindings(JsonNode payload) {
        if (payload == null) {
            return List.of();
        }
        JsonNode findings = payload.path("findings");
        if (!findings.isArray()) {
            return List.of();
        }
        List<Finding> mapped = new ArrayList<>();
        findings.forEach(node -> mapped.add(toFinding(node)));
        return mapped;
    }

    private static Finding toFinding(JsonNode node) {
        Finding finding = new Finding();
        finding.setClauseId(node.path("clause_id").asText(""));
        finding.setAgent(node.path("agent").asText(""));
        finding.setRiskType(node.path("risk_type").asText(""));
        // 未知等级按 low 收纳：枚举外值是契约破坏，不抛异常中断整个查询面。
        String level = node.path("risk_level").asText("low");
        try {
            finding.setRiskLevel(Finding.RiskLevelEnum.fromValue(level));
        } catch (IllegalArgumentException ex) {
            finding.setRiskLevel(Finding.RiskLevelEnum.LOW);
        }
        finding.setSummary(node.path("summary").asText(""));
        finding.setSuggestion(node.path("suggestion").asText(""));
        finding.setEvidence(node.path("evidence").asText(""));
        finding.setStale(node.path("stale").asBoolean(false));
        List<FindingCitationsInner> citations = new ArrayList<>();
        node.path("citations").forEach(citation -> citations.add(new FindingCitationsInner()
                .sourceId(citation.path("source_id").asText(""))
                .title(citation.path("title").asText(""))
                .excerpt(citation.path("excerpt").asText(""))
                .url(citation.hasNonNull("url") ? citation.path("url").asText() : null)));
        finding.setCitations(citations);
        return finding;
    }

    private static List<String> stringList(JsonNode node) {
        if (!node.isArray()) {
            return List.of();
        }
        List<String> values = new ArrayList<>();
        node.forEach(item -> values.add(item.asText()));
        return values;
    }

    /**
     * 实时订阅句柄：携带已重放的截止 seq，用于实时扇出去重。
     */
    private record LiveSubscription(SseEmitter emitter, long afterSeq) {
    }
}
