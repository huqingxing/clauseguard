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
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
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
import java.time.temporal.ChronoUnit;
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
import java.util.function.Consumer;
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
    /** 会改变审查状态、丢了就没法续跑的事件：写失败必须让调用方标失败，不能只降级。 */
    private static final Set<String> STATE_CRITICAL_TYPES = Set.of(TYPE_RUN_STARTED, TYPE_INTERRUPT);
    /** 空状态变更：绝大多数节点、消息、工具事件不碰 review 行。 */
    private static final Consumer<Review> NO_STATE_CHANGE = review -> { };

    private final ReviewRepository reviewRepository;
    private final ReviewEventRepository eventRepository;
    private final ApprovalRepository approvalRepository;
    private final EventReplayStore replayStore;
    private final PythonAgentClient agentClient;
    private final CircuitBreaker circuitBreaker;
    private final ObjectMapper mapper;
    private final ContractRepository contractRepository;
    private final TraceContextSupport traceSupport;
    /**
     * 方案 A：「更新状态 + 插入事件 + 刷新 last_event_at」的唯一事务写入口。
     * 单独成 Bean 是为了让 @Transactional 真正经过 Spring 代理——handleAgentEvent 由同一 bean
     * 内的 reactor 回调调用，直接给它加注解会静默失效（详见 ReviewEventWriter 注释）。
     */
    private final ReviewEventWriter eventWriter;

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
            TraceContextSupport traceSupport,
            ReviewEventWriter eventWriter) {
        this.reviewRepository = reviewRepository;
        this.eventRepository = eventRepository;
        this.approvalRepository = approvalRepository;
        this.replayStore = replayStore;
        this.agentClient = agentClient;
        this.circuitBreaker = circuitBreakerRegistry.circuitBreaker("pythonAgent");
        this.mapper = mapper;
        this.contractRepository = contractRepository;
        this.traceSupport = traceSupport;
        this.eventWriter = eventWriter;
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
     * 处理一条来自 Python 的事件：单事务落库（状态 + 事件）→ 写重放投影 → 实时扇出；终态收尾订阅。
     *
     * <p>一致性契约（方案 A）：状态前进与事件落库同生共死。事件插入失败 → 事务回滚 → 状态保持
     * 原值；若失败的是终态事件，再在新事务里补偿一条契约合规的 run.failed（标失败），
     * 绝不会留下「status=COMPLETED 但没有终态事件」这种不可解释组合。
     *
     * <p>重放存储（Redis Stream / 内存）是 review_event 表的派生投影，不参与 DB 事务；
     * 终态事件在投影里缺失时会立刻按 seq 从表重建，看门狗再做周期兜底。
     */
    void handleAgentEvent(String reviewId, AgentEvent event) {
        AgentEvent effective;
        try {
            persistEvent(reviewId, event);
            effective = event;
        } catch (RuntimeException writeFailure) {
            // 不吞异常：要么补偿成终态，要么降级落诊断事件，要么抛给调用方标失败。
            effective = recoverWriteFailure(reviewId, event, writeFailure);
        }

        publishToReplay(reviewId, effective);
        fanout(reviewId, effective);
        if (TERMINAL_TYPES.contains(effective.type())) {
            subscribing.remove(reviewId);
        }
    }

    /** 一次事务：状态变更 + 事件行 + last_event_at。ts 解析失败时不回写时间戳，保留上一值。 */
    private void persistEvent(String reviewId, AgentEvent event) {
        ReviewEvent entity = new ReviewEvent();
        entity.setReviewId(reviewId);
        entity.setSeq(event.seq());
        entity.setType(event.type());
        entity.setPayload(event.rawJson());
        entity.setTs(parseTs(event.ts()));
        // 对齐到「秒」且 round-half-up，与 review_event.ts（TIMESTAMP(fsp=0) 落库四舍五入）严格一致，
        // 保证实时写入与 V5 回填口径（MAX(review_event.ts)）相等，阶段 14 超时判定不偏 1 秒。
        String lastEventAtUtc = entity.getTs() == null ? null
                : DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss").withZone(ZoneOffset.UTC)
                        .format(roundSecond(entity.getTs()));
        eventWriter.persist(reviewId, stateMutation(event), entity, lastEventAtUtc);
    }

    /** 从事件推导状态变更（纯解析，不含任何写库）；真正的赋值在事务内对受管实体执行。 */
    private Consumer<Review> stateMutation(AgentEvent event) {
        String type = event.type();
        if (TYPE_RUN_STARTED.equals(type)) {
            String runId = readRunId(event.rawJson());
            return runId == null ? NO_STATE_CHANGE : review -> review.setRunId(runId);
        }
        if (TYPE_INTERRUPT.equals(type)) {
            JsonNode payload = readInterruptPayload(event.rawJson());
            if (payload == null) {
                return NO_STATE_CHANGE;
            }
            String contentHash = textOrNull(payload.path("content_hash"));
            String contractVersion = textOrNull(payload.path("contract_version"));
            String pending = payload.toString();
            return review -> {
                review.setContentHash(contentHash);
                review.setContractVersion(contractVersion);
                review.setPendingPayload(pending);
                review.setStatus(ReviewStatus.AWAITING_APPROVAL);
            };
        }
        if (TYPE_RUN_COMPLETED.equals(type)) {
            return review -> review.setStatus(ReviewStatus.COMPLETED);
        }
        if (TYPE_RUN_FAILED.equals(type)) {
            return review -> review.setStatus(ReviewStatus.FAILED);
        }
        return NO_STATE_CHANGE;
    }

    /**
     * 写入失败的补偿分派。终态优先：原终态写不进去也必须给这笔审查一个终态，
     * 否则用户端会永远停在「等待审批」。返回的事件继续走投影与扇出。
     */
    private AgentEvent recoverWriteFailure(String reviewId, AgentEvent event, RuntimeException cause) {
        String type = event.type();
        if (TERMINAL_TYPES.contains(type)) {
            logger.error("终态事件写入失败，状态已随事务回滚；补偿 run.failed：review={} seq={} type={} err={}",
                    reviewId, event.seq(), type, cause.getMessage());
            String ts = nowRfc3339();
            AgentEvent failed = agentClient.buildRunFailedEvent(event.seq(), ts, "EVENT_WRITE_FAILED",
                    "终态事件持久化失败，状态已回滚并标记 FAILED：" + shortMessage(cause), true);
            try {
                persistEvent(reviewId, failed);
            } catch (RuntimeException compensationFailure) {
                logger.error("终态补偿写入也失败，原异常向上抛给调用方：review={} err={}",
                        reviewId, compensationFailure.getMessage());
                throw cause;
            }
            return failed;
        }
        if (STATE_CRITICAL_TYPES.contains(type)) {
            // run.started 的 run_id、interrupt 的审批基线丢了就没法 resume，不能降级：交给调用方标失败
            logger.error("状态关键事件写入失败，异常向上抛给调用方：review={} seq={} type={} err={}",
                    reviewId, event.seq(), type, cause.getMessage());
            throw cause;
        }
        // 非关键事件（node.*/message.*/tool.*）写失败不能中断 SSE 流——那会连真正的终态一起丢。
        // 但也不是「catch 掉打日志」：在同一 seq 落一条契约合规的 node.failed 诊断事件，
        // 序列连续、终态仍可写入，并留下可查的降级证据。
        logger.warn("非关键事件写入失败，降级为 node.failed 诊断事件：review={} seq={} type={} err={}",
                reviewId, event.seq(), type, cause.getMessage());
        AgentEvent diagnostic = degradedDiagnostic(event, cause);
        persistEvent(reviewId, diagnostic);
        return diagnostic;
    }

    /** 降级诊断事件：严格使用 events.schema.json 中 node.failed 的字段，不改契约、不私加字段。 */
    private AgentEvent degradedDiagnostic(AgentEvent event, Throwable cause) {
        ObjectNode body = mapper.createObjectNode();
        body.put("seq", event.seq());
        body.put("ts", event.ts() == null || event.ts().isBlank() ? nowRfc3339() : event.ts());
        body.put("type", "node.failed");
        body.put("node", "java.event-write");
        body.put("agent", "java-gateway");
        body.put("code", "EVENT_PERSIST_FAILED");
        body.put("message", "事件持久化失败，已降级为诊断事件；原始负载已丢弃：" + shortMessage(cause));
        try {
            return new AgentEvent(event.seq(), event.ts(), "node.failed", mapper.writeValueAsString(body));
        } catch (JsonProcessingException e) {
            throw new IllegalStateException("serialize degraded event failed", e);
        }
    }

    /**
     * 重放投影写入。DB 的 review_event 是事实源，投影丢了可以重建；
     * 终态事件在投影里缺失时立即重建并强校验，保证客户端 ?since=0 一定能拿到终态。
     */
    private void publishToReplay(String reviewId, AgentEvent event) {
        try {
            replayStore.append(reviewId, event.seq(), event.type(), event.rawJson());
        } catch (RuntimeException ex) {
            logger.warn("重放投影写入失败，按 seq 从 review_event 重建：review={} seq={} err={}",
                    reviewId, event.seq(), ex.getMessage());
            rebuildReplayProjection(reviewId);
        }
        if (TERMINAL_TYPES.contains(event.type()) && !hasTerminalInReplay(reviewId)) {
            rebuildReplayProjection(reviewId);
            if (!hasTerminalInReplay(reviewId)) {
                throw new IllegalStateException("终态事件未能写入重放投影：review=" + reviewId);
            }
        }
    }

    /** 从 DB 事件表按 seq 补齐投影里缺失的行（幂等：只 append 已存在的 seq 之外的行）。 */
    private void rebuildReplayProjection(String reviewId) {
        Set<Long> present = replayStore.replaySince(reviewId, -1).stream()
                .map(StoredEvent::seq)
                .collect(java.util.stream.Collectors.toSet());
        for (ReviewEvent row : eventRepository.findByReviewIdOrderBySeqAsc(reviewId)) {
            if (!present.contains(row.getSeq())) {
                replayStore.append(reviewId, row.getSeq(), row.getType(), row.getPayload());
            }
        }
    }

    private boolean hasTerminalInReplay(String reviewId) {
        return replayStore.replaySince(reviewId, -1).stream()
                .anyMatch(e -> TERMINAL_TYPES.contains(e.type()));
    }

    /**
     * Python 调用失败（含熔断打开）：补一条合规 run.failed 事件，保证前端总能收到终态。
     * 若 DB 里已有终态（例如终态写失败已补偿过），只重建投影、不重复发第二条终态。
     */
    void handleAgentError(String reviewId, Throwable error) {
        if (hasTerminalInReplay(reviewId)) {
            logger.info("已有终态事件，跳过重复的 run.failed：review={}", reviewId);
            return;
        }
        if (eventRepository.countByReviewIdAndTypeIn(reviewId, TERMINAL_TYPES) > 0) {
            rebuildReplayProjection(reviewId);
            return;
        }
        long seq = replayStore.currentMaxSeq(reviewId) + 1;
        if (seq < 0) {
            seq = 0;
        }
        String ts = nowRfc3339();
        AgentEvent failed = agentClient.buildRunFailedEvent(
                seq, ts, "AGENT_UNAVAILABLE", "Python agent unreachable: " + errorMessage(error), true);
        handleAgentEvent(reviewId, failed);
    }

    private static String shortMessage(Throwable error) {
        String msg = error.getMessage();
        if (msg == null) {
            return error.getClass().getSimpleName();
        }
        return msg.length() > 180 ? msg.substring(0, 180) : msg;
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

    /**
     * Stage 14：看门狗把卡死的审查补上终态后，经由这里扇出给在线订阅者。
     * 为什么要单独一个入口：卡死意味着 Python 那边已经不会再有任何事件了，
     * 终态只能由本服务自己生成；不扇出的话前端 SSE 会一直干等到超时才断。
     */
    public void fanoutSystemEvent(String reviewId, AgentEvent event) {
        fanout(reviewId, event);
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

    /**
     * 把事件时间戳对齐到「秒」并采用 round-half-up，使其与 review_event.ts
     * （TIMESTAMP(fsp=0)，MySQL 落库按四舍五入）完全一致。这样 last_event_at 写入路径
     * 与 V5 回填路径（MAX(review_event.ts)）产出的秒级值严格相等，阶段 14 看门狗读到的
     * 时间戳就不会和事件表对不上。
     */
    private Instant roundSecond(Instant ts) {
        return ts.plus(500, ChronoUnit.MILLIS).truncatedTo(ChronoUnit.SECONDS);
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
