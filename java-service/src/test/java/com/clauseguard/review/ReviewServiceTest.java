package com.clauseguard.review;

import com.clauseguard.agent.PythonAgentClient;
import com.clauseguard.api.model.ApproveRequest;
import com.clauseguard.api.model.ApproveResponse;
import com.clauseguard.api.model.ContractDiff;
import com.clauseguard.api.model.Finding;
import com.clauseguard.api.model.FindingsPage;
import com.clauseguard.api.model.ReviewCreateRequest;
import com.clauseguard.api.model.ReviewCreateResponse;
import com.clauseguard.api.model.ReviewResult;
import com.clauseguard.contract.ContractNotFoundException;
import com.clauseguard.contract.ContractRepository;
import com.clauseguard.contract.domain.Contract;
import com.clauseguard.events.AgentEvent;
import com.clauseguard.events.InMemoryEventReplayStore;
import com.clauseguard.observability.TraceContextSupport;
import com.clauseguard.review.domain.Approval;
import com.clauseguard.review.domain.Review;
import com.clauseguard.review.domain.ReviewEvent;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.github.resilience4j.circuitbreaker.CircuitBreakerRegistry;
import io.micrometer.tracing.Tracer;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.junit.jupiter.api.io.TempDir;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Flux;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import java.util.function.BooleanSupplier;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doReturn;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.spy;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * ReviewService 单元测试：验证落库、异步触发、事件落重放存储、下游不可用时的 run.failed，
 * 以及 Stage 9 的人工审批（run_id 捕获、interrupt → AWAITING_APPROVAL、approveReview 乐观锁与落库）。
 * 不启动容器；重放存储用内存实现以观测落库结果。
 *
 * agentClient 用 spy 包裹真实实例：streamRun/streamResume 被 stub，buildRunFailedEvent 走真实实现
 * （依赖真实 ObjectMapper），避免 mock 字段未初始化导致的 NPE。
 */
@ExtendWith(MockitoExtension.class)
class ReviewServiceTest {

    @Mock
    private ReviewRepository reviewRepository;
    @Mock
    private ReviewEventRepository reviewEventRepository;
    @Mock
    private ApprovalRepository approvalRepository;
    @Mock
    private ContractRepository contractRepository;

    private PythonAgentClient agentClient;

    private final InMemoryEventReplayStore replayStore = new InMemoryEventReplayStore();
    private final ObjectMapper mapper = new ObjectMapper();
    private final CircuitBreakerRegistry registry = CircuitBreakerRegistry.ofDefaults();

    private ReviewService service;

    /** 产物白名单根目录：file: 分支只允许读这棵子树，故每条用例给一个干净的临时目录。 */
    @TempDir
    Path artifactRoot;

    @SuppressWarnings("unchecked")
    private static ObjectProvider<Tracer> noTracer() {
        ObjectProvider<Tracer> provider = org.mockito.Mockito.mock(ObjectProvider.class);
        // lenient：只有 createReview 路径才真正读 Tracer（其余用例走查询面）。
        org.mockito.Mockito.lenient().when(provider.getIfAvailable()).thenReturn(null);
        return provider;
    }

    @BeforeEach
    void setup() {
        PythonAgentClient realAgent = new PythonAgentClient(
                WebClient.builder(), mapper, registry,
                "http://localhost:8000", Duration.ofSeconds(30));
        agentClient = spy(realAgent);
        // 追踪关闭是测试默认态：ObjectProvider 取不到 Tracer -> traceparent 走本地生成。
        service = new ReviewService(
                reviewRepository, reviewEventRepository, approvalRepository,
                replayStore, agentClient, registry, mapper, contractRepository,
                new TraceContextSupport(noTracer()),
                new ReviewEventWriter(reviewRepository, reviewEventRepository),
                new ApprovalApplier(reviewRepository, approvalRepository),
                artifactRoot.toString());
        // 仅 createReview 路径需要 save；其余测试不触发，故标为 lenient 避免严格模式报错。
        lenient().when(reviewRepository.save(any(Review.class))).thenAnswer(i -> i.getArgument(0));
    }

    @Test
    void createReviewPersistsAndStoresStreamedEvents() throws Exception {
        AgentEvent e1 = new AgentEvent(1, "2026-10-23T10:00:00+00:00", "node.started",
                "{\"seq\":1,\"type\":\"node.started\"}");
        AgentEvent e2 = new AgentEvent(2, "2026-10-23T10:00:01+00:00", "run.completed",
                "{\"seq\":2,\"type\":\"run.completed\"}");
        givenContract("00000000-0000-0000-0000-0000000000a1", 1);
        when(agentClient.streamRun(anyString(), anyString(), anyString(), anyInt(), anyString(), anyInt(), any()))
                .thenReturn(Flux.just(e1, e2));

        ReviewCreateRequest request = new ReviewCreateRequest()
                .contractId(UUID.fromString("00000000-0000-0000-0000-0000000000a1"));
        ReviewCreateResponse response = service.createReview(request, null);

        assertThat(response.getReviewId()).isNotNull();
        assertThat(response.getStreamUrl().toString()).contains("/api/v1/reviews/");
        verify(reviewRepository).save(any(Review.class));

        await(() -> replayStore.currentMaxSeq(response.getReviewId().toString()) == 2);
        List<?> events = replayStore.replaySince(response.getReviewId().toString(), -1);
        assertThat(events).hasSize(2);
    }

    @Test
    void incrementalCreateForwardsVersionModeAndBaseVersionToAgent() {
        // Stage 11：前端「仅重审变更」= mode=incremental + 真实版本号；Java 不解释语义，
        // 但必须把版本事实原样透传给编排层（落库版本与透传版本也必须一致）。
        givenContract("00000000-0000-0000-0000-0000000000b1", 3);
        when(agentClient.streamRun(anyString(), anyString(), anyString(), anyInt(), anyString(), anyInt(), any()))
                .thenReturn(Flux.empty());

        ReviewCreateRequest request = new ReviewCreateRequest()
                .contractId(UUID.fromString("00000000-0000-0000-0000-0000000000b1"))
                .version(3)
                .mode(ReviewCreateRequest.ModeEnum.INCREMENTAL)
                .baseVersion(2);
        service.createReview(request, null);

        verify(agentClient).streamRun(anyString(), anyString(), anyString(), eq(3), eq("incremental"), eq(2), any());
        ArgumentCaptor<Review> saved = ArgumentCaptor.forClass(Review.class);
        verify(reviewRepository).save(saved.capture());
        assertThat(saved.getValue().getVersion()).isEqualTo(3);
    }

    @Test
    void fullCreateDefaultsToContractCurrentVersion() {
        givenContract("00000000-0000-0000-0000-0000000000b2", 1);
        when(agentClient.streamRun(anyString(), anyString(), anyString(), anyInt(), anyString(), anyInt(), any()))
                .thenReturn(Flux.empty());

        ReviewCreateRequest request = new ReviewCreateRequest()
                .contractId(UUID.fromString("00000000-0000-0000-0000-0000000000b2"));
        service.createReview(request, null);

        // Stage 12：未指定版本时取合同当前版本（上传后直接审查的默认路径）。
        verify(agentClient).streamRun(anyString(), anyString(), anyString(), eq(1), eq("full"), eq(0), any());
        ArgumentCaptor<Review> saved = ArgumentCaptor.forClass(Review.class);
        verify(reviewRepository).save(saved.capture());
        assertThat(saved.getValue().getVersion()).isEqualTo(1);
    }

    @Test
    void agentUnavailableEmitsRunFailedWithRetryable() throws Exception {
        givenContract("00000000-0000-0000-0000-0000000000a2", 1);
        when(agentClient.streamRun(anyString(), anyString(), anyString(), anyInt(), anyString(), anyInt(), any()))
                .thenReturn(Flux.error(new RuntimeException("boom")));
        ReviewCreateRequest request = new ReviewCreateRequest()
                .contractId(UUID.fromString("00000000-0000-0000-0000-0000000000a2"));
        ReviewCreateResponse response = service.createReview(request, null);

        await(() -> replayStore.currentMaxSeq(response.getReviewId().toString()) >= 0);
        List<?> events = replayStore.replaySince(response.getReviewId().toString(), -1);
        assertThat(events).hasSize(1);
        // payload 直接断言（不新增事件 DTO，复用原始 JSON 校验）
        String payload = ((com.clauseguard.events.StoredEvent) events.get(0)).payload();
        assertThat(payload).contains("run.failed").contains("AGENT_UNAVAILABLE").contains("\"retryable\":true");
    }

    @Test
    void isAgentUnavailableReflectsCircuitState() {
        // 默认 CLOSED，未熔断
        assertThat(service.isAgentUnavailable()).isFalse();
    }

    // ---- Stage 9：run.started 捕获 run_id（供 approve 时 resume 定位）----

    @Test
    void runStartedCapturesRunId() {
        Review review = new Review();
        review.setId("r-runid");
        when(reviewRepository.findById("r-runid")).thenReturn(Optional.of(review));

        service.handleAgentEvent("r-runid", new AgentEvent(
                1, "2026-10-23T10:00:00+00:00", "run.started",
                "{\"seq\":1,\"type\":\"run.started\",\"run_id\":\"run-abc\"}"));

        assertThat(review.getRunId()).isEqualTo("run-abc");
    }

    // ---- Stage 9：interrupt 置 AWAITING_APPROVAL 并缓存乐观锁基线 ----

    @Test
    void interruptCapturesBaselineAndSetsAwaiting() {
        Review review = new Review();
        review.setId("r-int");
        when(reviewRepository.findById("r-int")).thenReturn(Optional.of(review));

        String payload = "{\"seq\":5,\"type\":\"interrupt\",\"reason\":\"high_risk_pending_approval\","
                + "\"payload\":{\"reason\":\"high_risk_pending_approval\",\"contract_version\":1,"
                + "\"content_hash\":\"hash-1\",\"items\":[{\"clause_id\":\"c1\",\"risk\":\"high\","
                + "\"clause_text\":\"原文条款\"}]}}";
        service.handleAgentEvent("r-int", new AgentEvent(
                5, "2026-10-23T10:00:05+00:00", "interrupt", payload));

        assertThat(review.getStatus()).isEqualTo(ReviewStatus.AWAITING_APPROVAL);
        assertThat(review.getContractVersion()).isEqualTo("1");
        assertThat(review.getContentHash()).isEqualTo("hash-1");
        assertThat(review.getPendingPayload()).contains("\"clause_id\":\"c1\"");
    }

    // ---- Stage 13：last_event_at 写入路径 ----

    @Test
    void agentEventPersistsEventAndRefreshesLastEventAt() {
        service.handleAgentEvent("r-last", new AgentEvent(
                1, "2026-10-23T10:00:00+00:00", "node.started",
                "{\"seq\":1,\"type\":\"node.started\"}"));

        // 事件行与 last_event_at 必须在同一个写入路径里完成：时间戳取自事件 ts，不是落库时刻
        verify(reviewEventRepository).save(any(ReviewEvent.class));
        verify(reviewRepository).updateLastEventAt(eq("r-last"), eq("2026-10-23 10:00:00"));
    }

    @Test
    void unparsableTsSkipsLastEventAtRefreshAndKeepsPreviousValue() {
        service.handleAgentEvent("r-badts", new AgentEvent(
                2, "not-a-timestamp", "node.started",
                "{\"seq\":2,\"type\":\"node.started\"}"));

        verify(reviewEventRepository).save(any(ReviewEvent.class));
        verify(reviewRepository, never()).updateLastEventAt(anyString(), anyString());
    }

    // ---- Stage 9：approveReview 乐观锁：版本/哈希不匹配 → 409，无部分写入 ----

    @Test
    void approveRejectsOnVersionMismatchWithoutPartialWrite() {
        Review review = new Review();
        review.setId("r-409");
        review.setStatus(ReviewStatus.AWAITING_APPROVAL);
        review.setContractVersion("1");
        review.setContentHash("hash-1");
        review.setRunId("run-abc");
        when(reviewRepository.findById("r-409")).thenReturn(Optional.of(review));
        // 抢占必须成功，否则用例会因为「拿不到状态」而非「基线不匹配」抛异常——那是假绿。
        when(reviewRepository.markStatusIfCurrent(anyString(), anyString(), anyString())).thenReturn(1);

        ApproveRequest bad = new ApproveRequest()
                .clauseId("c1")
                .decision(ApproveRequest.DecisionEnum.APPROVE)
                .contractVersion("2") // 与基线 1 不一致
                .contentHash("hash-1");
        List<ApproveRequest> approvals = List.of(bad);

        assertThatThrownBy(() -> service.approveReview("r-409", approvals, "alice"))
                .isInstanceOf(ContractChangedException.class);

        // 关键：乐观锁在写入任何审批记录之前判定，不得产生部分执行。
        verify(approvalRepository, never()).save(any(Approval.class));
    }

    // ---- 审批并发：状态抢占失败（别人已抢到/已审批）→ 409，且一条审批记录都不写 ----

    @Test
    void approveRejectsWhenStatusClaimLosesRace() {
        Review review = new Review();
        review.setId("r-race");
        review.setStatus(ReviewStatus.AWAITING_APPROVAL);
        review.setContractVersion("1");
        review.setContentHash("hash-1");
        review.setRunId("run-abc");
        when(reviewRepository.findById("r-race")).thenReturn(Optional.of(review));
        // 条件更新拿 0 行 = 另一个事务已经把状态从 AWAITING_APPROVAL 抢走。
        when(reviewRepository.markStatusIfCurrent(anyString(), anyString(), anyString())).thenReturn(0);

        ApproveRequest ok = new ApproveRequest()
                .clauseId("c1")
                .decision(ApproveRequest.DecisionEnum.APPROVE)
                .contractVersion("1")
                .contentHash("hash-1");

        assertThatThrownBy(() -> service.approveReview("r-race", List.of(ok), "alice"))
                .isInstanceOf(ContractChangedException.class);

        // CAS 拿不到行就必须整体退出：不写审批记录，也不转发 resume。
        verify(approvalRepository, never()).save(any(Approval.class));
        verify(agentClient, never()).streamResume(anyString(), any(), anyString());
    }

    // ---- Stage 9：approveReview 成功 → 落库审批记录（含操作人/前后措辞）+ 转发 resume ----

    @Test
    void approvePersistsApprovalsWithOperatorAndForwardsResume() {
        Review review = new Review();
        review.setId("r-ok");
        review.setStatus(ReviewStatus.AWAITING_APPROVAL);
        review.setContractVersion("1");
        review.setContentHash("hash-1");
        review.setRunId("run-abc");
        review.setPendingPayload("{\"reason\":\"high_risk_pending_approval\",\"contract_version\":1,"
                + "\"content_hash\":\"hash-1\",\"items\":["
                + "{\"clause_id\":\"c1\",\"risk\":\"high\",\"clause_text\":\"原文一\"},"
                + "{\"clause_id\":\"c2\",\"risk\":\"high\",\"clause_text\":\"原文二\"}]}");
        when(reviewRepository.findById("r-ok")).thenReturn(Optional.of(review));
        when(approvalRepository.save(any(Approval.class))).thenAnswer(i -> i.getArgument(0));
        when(reviewRepository.markStatusIfCurrent(anyString(), anyString(), anyString())).thenReturn(1);
        doReturn(Flux.empty()).when(agentClient)
                .streamResume(eq("run-abc"), any(), anyString());

        ApproveRequest approve = new ApproveRequest()
                .clauseId("c1")
                .decision(ApproveRequest.DecisionEnum.APPROVE)
                .contractVersion("1")
                .contentHash("hash-1");
        ApproveRequest edit = new ApproveRequest()
                .clauseId("c2")
                .decision(ApproveRequest.DecisionEnum.EDIT)
                .contractVersion("1")
                .contentHash("hash-1")
                .editedText("修订后条款二");
        List<ApproveRequest> approvals = List.of(approve, edit);

        ApproveResponse response = service.approveReview("r-ok", approvals, "alice");

        assertThat(response.getStatus()).isEqualTo(ApproveResponse.StatusEnum.ACCEPTED);
        // 状态切回 RUNNING 由条件更新完成（恢复后由 run.completed 置 COMPLETED）：
        // 实体对象不会被 mock 改动，所以这里验的是「发出了正确的抢占语句」。
        verify(reviewRepository).markStatusIfCurrent(
                "r-ok", ReviewStatus.AWAITING_APPROVAL.name(), ReviewStatus.RUNNING.name());
        // 审批记录落库：两条。
        ArgumentCaptor<Approval> cap = ArgumentCaptor.forClass(Approval.class);
        verify(approvalRepository, org.mockito.Mockito.times(2)).save(cap.capture());
        // approve 项：操作人/前措辞/风险档。
        Approval a1 = cap.getAllValues().get(0);
        assertThat(a1.getOperator()).isEqualTo("alice");
        assertThat(a1.getClauseId()).isEqualTo("c1");
        assertThat(a1.getBeforeWording()).isEqualTo("原文一");
        assertThat(a1.getRisk()).isEqualTo("high");
        assertThat(a1.getAfterWording()).isNull(); // approve 不改措辞
        // edit 项：后措辞为 edited_text。
        Approval a2 = cap.getAllValues().get(1);
        assertThat(a2.getClauseId()).isEqualTo("c2");
        assertThat(a2.getAfterWording()).isEqualTo("修订后条款二");
        // 转发 Python resume（审批结果数组即 interrupt 返回值）。
        verify(agentClient).streamResume(eq("run-abc"), any(), anyString());
    }

    // ---- (review_id, clause_id) 唯一约束：写前幂等 + 兜底冲突不当 500 ----

    @Test
    void approveWritesOneRowWhenSameClauseRepeatsInOneRequest() {
        Review review = awaitingReview("r-dup");
        when(reviewRepository.findById("r-dup")).thenReturn(Optional.of(review));
        when(reviewRepository.markStatusIfCurrent(anyString(), anyString(), anyString())).thenReturn(1);
        when(approvalRepository.findByReviewId("r-dup")).thenReturn(List.of());
        when(approvalRepository.save(any(Approval.class))).thenAnswer(i -> i.getArgument(0));
        doReturn(Flux.empty()).when(agentClient).streamResume(eq("run-abc"), any(), anyString());

        // 前端重复提交同一个 clause：CAS 只看 review 状态，拦不住请求体里的重复。
        ApproveRequest twice = approveOf("c1");
        service.approveReview("r-dup", List.of(twice, twice), "alice");

        verify(approvalRepository, org.mockito.Mockito.times(1)).save(any(Approval.class));
    }

    @Test
    void approveSkipsClauseAlreadyRecordedInsteadOfOverwriting() {
        Review review = awaitingReview("r-old");
        when(reviewRepository.findById("r-old")).thenReturn(Optional.of(review));
        when(reviewRepository.markStatusIfCurrent(anyString(), anyString(), anyString())).thenReturn(1);
        // 库里已有 c1（历史「一条审批一个事务」时代批了一半的现场）。
        when(approvalRepository.findByReviewId("r-old")).thenReturn(List.of(storedApproval("r-old", "c1")));
        doReturn(Flux.empty()).when(agentClient).streamResume(eq("run-abc"), any(), anyString());

        service.approveReview("r-old", List.of(approveOf("c1")), "alice");

        // 已批过就以第一次的结论为准：不再写，也不会撞唯一约束。
        verify(approvalRepository, never()).save(any(Approval.class));
    }

    @Test
    void approveFallsBackToIdempotentWhenUniqueConstraintRaces() {
        Review review = awaitingReview("r-idem");
        when(reviewRepository.findById("r-idem")).thenReturn(Optional.of(review));
        when(reviewRepository.markStatusIfCurrent(anyString(), anyString(), anyString())).thenReturn(1);
        // 第一次判重：还没有；第二次（冲突后复核）：另一个请求已经写进去了。
        when(approvalRepository.findByReviewId("r-idem"))
                .thenReturn(List.of())
                .thenReturn(List.of(storedApproval("r-idem", "c1")));
        when(approvalRepository.save(any(Approval.class)))
                .thenThrow(new DataIntegrityViolationException("Duplicate entry for key 'uk_approval_review_clause'"));

        ApproveResponse response = service.approveReview("r-idem", List.of(approveOf("c1")), "alice");

        // 结论等价于「已经批过了」：返回 accepted，而不是把并发变成 500。
        assertThat(response.getStatus()).isEqualTo(ApproveResponse.StatusEnum.ACCEPTED);
        // 也不能再发 resume —— 赢的那个请求已经在续跑，再发一次 Python 会收到两份审批结果。
        verify(agentClient, never()).streamResume(anyString(), any(), anyString());
    }

    @Test
    void approveRethrowsWhenConstraintViolationIsNotADuplicateApproval() {
        Review review = awaitingReview("r-bad");
        when(reviewRepository.findById("r-bad")).thenReturn(Optional.of(review));
        when(reviewRepository.markStatusIfCurrent(anyString(), anyString(), anyString())).thenReturn(1);
        // 两次复核都查不到记录 → 不是「批过了」，是别的原因撞了约束（字段超长之类）。
        when(approvalRepository.findByReviewId("r-bad")).thenReturn(List.of());
        when(approvalRepository.save(any(Approval.class)))
                .thenThrow(new DataIntegrityViolationException("Data too long for column 'comment'"));

        assertThatThrownBy(() -> service.approveReview("r-bad", List.of(approveOf("c1")), "alice"))
                .isInstanceOf(DataIntegrityViolationException.class);
    }

    // ---- 聚合后的待审项：一条决定覆盖多个风险类型时必须留痕 ----

    @Test
    void approveStoresAllRiskTypesOfAMergedClause() {
        Review review = awaitingReview("r-merged");
        // 新版 payload：一条条款聚合了两个风险维度。
        review.setPendingPayload("{\"reason\":\"high_risk_pending_approval\",\"contract_version\":1,"
                + "\"content_hash\":\"hash-1\",\"items\":[{\"clause_id\":\"c1\",\"risk\":\"high\","
                + "\"risk_types\":[\"payment_risk\",\"disclaimer_risk\"],"
                + "\"suggestion\":\"①缩短账期\\n②删除免责\",\"findings_merged\":2,"
                + "\"clause_text\":\"4.3 付款条款\"}]}");
        when(reviewRepository.findById("r-merged")).thenReturn(Optional.of(review));
        when(reviewRepository.markStatusIfCurrent(anyString(), anyString(), anyString())).thenReturn(1);
        when(approvalRepository.save(any(Approval.class))).thenAnswer(i -> i.getArgument(0));
        doReturn(Flux.empty()).when(agentClient).streamResume(eq("run-abc"), any(), anyString());

        service.approveReview("r-merged", List.of(approveOf("c1")), "alice");

        ArgumentCaptor<Approval> cap = ArgumentCaptor.forClass(Approval.class);
        verify(approvalRepository).save(cap.capture());
        // 只存档位会让审计看不出这条决定审了两类问题，所以 risk_types 必须一起落库。
        assertThat(cap.getValue().getRiskTypes()).isEqualTo("payment_risk,disclaimer_risk");
        assertThat(cap.getValue().getRisk()).isEqualTo("high");
    }

    @Test
    void approveToleratesLegacyPayloadWithoutRiskTypes() {
        Review review = awaitingReview("r-legacy");
        // 旧版 payload（事件存储里还留着）：只有单值 risk_type，没有 risk_types 数组。
        review.setPendingPayload("{\"reason\":\"high_risk_pending_approval\",\"contract_version\":1,"
                + "\"content_hash\":\"hash-1\",\"items\":[{\"clause_id\":\"c1\",\"risk\":\"high\","
                + "\"risk_type\":\"payment_risk\",\"clause_text\":\"旧原文\"}]}");
        when(reviewRepository.findById("r-legacy")).thenReturn(Optional.of(review));
        when(reviewRepository.markStatusIfCurrent(anyString(), anyString(), anyString())).thenReturn(1);
        when(approvalRepository.save(any(Approval.class))).thenAnswer(i -> i.getArgument(0));
        doReturn(Flux.empty()).when(agentClient).streamResume(eq("run-abc"), any(), anyString());

        service.approveReview("r-legacy", List.of(approveOf("c1")), "alice");

        ArgumentCaptor<Approval> cap = ArgumentCaptor.forClass(Approval.class);
        verify(approvalRepository).save(cap.capture());
        // 历史中断快照不能因为缺字段就写入假数据；null 就是「那个年代没有这项记录」。
        assertThat(cap.getValue().getRiskTypes()).isNull();
        assertThat(cap.getValue().getBeforeWording()).isEqualTo("旧原文");
    }

    private static Review awaitingReview(String id) {
        Review review = new Review();
        review.setId(id);
        review.setStatus(ReviewStatus.AWAITING_APPROVAL);
        review.setContractVersion("1");
        review.setContentHash("hash-1");
        review.setRunId("run-abc");
        return review;
    }

    private static ApproveRequest approveOf(String clauseId) {
        return new ApproveRequest()
                .clauseId(clauseId)
                .decision(ApproveRequest.DecisionEnum.APPROVE)
                .contractVersion("1")
                .contentHash("hash-1");
    }

    private static Approval storedApproval(String reviewId, String clauseId) {
        Approval approval = new Approval();
        approval.setReviewId(reviewId);
        approval.setClauseId(clauseId);
        approval.setDecision("approve");
        approval.setOperator("bob");
        return approval;
    }

    // ---- Stage 12：合同必须存在（404 而非空跑）；source 透传；查询面整形 ----

    private void givenContract(String id, int version) {
        Contract contract = new Contract();
        contract.setId(id);
        contract.setVersion(version);
        contract.setFilename("测试合同.pdf");
        contract.setContentType("application/pdf");
        contract.setSizeBytes(2048);
        contract.setStorageKey(id + "-v" + version + ".pdf");
        contract.setCreatedAt(Instant.parse("2026-10-25T10:00:00Z"));
        lenient().when(contractRepository.findFirstByIdOrderByVersionDesc(id))
                .thenReturn(Optional.of(contract));
    }

    @Test
    void createReviewRejectsUnknownContract() {
        when(contractRepository.findFirstByIdOrderByVersionDesc(anyString()))
                .thenReturn(Optional.empty());

        ReviewCreateRequest request = new ReviewCreateRequest()
                .contractId(UUID.fromString("00000000-0000-0000-0000-0000000000ff"));
        assertThatThrownBy(() -> service.createReview(request, null))
                .isInstanceOf(ContractNotFoundException.class);
        // 不落库、不触发 Python：没有合同的审查是空跑。
        verify(reviewRepository, never()).save(any(Review.class));
    }

    @Test
    void createReviewForwardsContractSourceToAgent() {
        givenContract("00000000-0000-0000-0000-0000000000a3", 1);
        when(agentClient.streamRun(anyString(), anyString(), anyString(), anyInt(), anyString(), anyInt(), any()))
                .thenReturn(Flux.empty());

        service.createReview(new ReviewCreateRequest()
                .contractId(UUID.fromString("00000000-0000-0000-0000-0000000000a3")), null);

        // source 描述「文件在哪」：download_url 必须指向 Java 自己的内容端点
        //（Python 经 REST 拉文件，铁律 1），而不是对象存储内部地址。
        ArgumentCaptor<Map<String, Object>> sourceCap = ArgumentCaptor.forClass(Map.class);
        verify(agentClient).streamRun(anyString(), anyString(), anyString(), anyInt(), anyString(), anyInt(),
                sourceCap.capture());
        Map<String, Object> source = sourceCap.getValue();
        assertThat(source.get("download_url"))
                .isEqualTo("/api/v1/contracts/00000000-0000-0000-0000-0000000000a3/content");
        assertThat(source.get("content_type")).isEqualTo("application/pdf");
        assertThat(source.get("version")).isEqualTo(1);
    }

    @Test
    void getReviewResultMapsFindingsFromRunCompleted() {
        givenReviewWithCompleted("00000000-0000-0000-0000-0000000000c1", 2);
        ReviewResult result = service.getReviewResult("00000000-0000-0000-0000-0000000000c1");

        assertThat(result.getVersion()).isEqualTo(2);
        assertThat(result.getFindings()).hasSize(2);
        Finding first = result.getFindings().get(0);
        assertThat(first.getClauseId()).isEqualTo("doc#0007");
        assertThat(first.getRiskLevel()).isEqualTo(Finding.RiskLevelEnum.HIGH);
        assertThat(first.getAgent()).isEqualTo("commercial-v1");
        assertThat(first.getCitations()).hasSize(1);
        assertThat(first.getCitations().get(0).getSourceId()).isEqualTo("LAW-001");
    }

    @Test
    void getReviewResultWithoutCompletedEventHasEmptyFindings() {
        Review review = new Review();
        review.setId("00000000-0000-0000-0000-0000000000c2");
        review.setContractId("00000000-0000-0000-0000-0000000000a1");
        review.setVersion(1);
        review.setStatus(ReviewStatus.RUNNING);
        review.setCreatedAt(Instant.parse("2026-10-25T10:00:00Z"));
        when(reviewRepository.findById("00000000-0000-0000-0000-0000000000c2")).thenReturn(Optional.of(review));
        when(reviewEventRepository.findByReviewIdAndTypeOrderBySeqAsc("00000000-0000-0000-0000-0000000000c2", "run.completed"))
                .thenReturn(List.of());

        ReviewResult result = service.getReviewResult("00000000-0000-0000-0000-0000000000c2");
        assertThat(result.getFindings()).isEmpty();
        assertThat(result.getStatus()).isEqualTo(ReviewResult.StatusEnum.RUNNING);
    }

    @Test
    void findingsPageSlicesCompletedFindings() {
        givenReviewWithCompleted("00000000-0000-0000-0000-0000000000c3", 1);
        FindingsPage page = service.findingsPage("00000000-0000-0000-0000-0000000000c3", 1, 1);
        assertThat(page.getTotal()).isEqualTo(2);
        assertThat(page.getItems()).hasSize(1);
        assertThat(page.getItems().get(0).getClauseId()).isEqualTo("doc#0011");
        assertThat(page.getPage()).isEqualTo(1);
    }

    @Test
    void artifactDownloadKeepsRedirectForPresignedUrl() {
        givenReviewWithCompleted("00000000-0000-0000-0000-0000000000c4", 1);
        ReviewService.ArtifactDownload download =
                service.artifactDownload("00000000-0000-0000-0000-0000000000c4").orElseThrow();
        assertThat(download.location())
                .isEqualTo("http://minio.local/clauseguard/artifacts/annotated.docx?sig=abc");
        assertThat(download.body()).isNull();
    }

    @Test
    void artifactDownloadStreamsLocalFileInsideRoot() throws Exception {
        Path artifact = artifactRoot.resolve("artifacts/cid/v1/annotated.docx");
        Files.createDirectories(artifact.getParent());
        Files.write(artifact, new byte[] {'P', 'K', 3, 4});
        givenArtifactEvent("00000000-0000-0000-0000-0000000000c7", artifact.toUri().toString());

        ReviewService.ArtifactDownload download =
                service.artifactDownload("00000000-0000-0000-0000-0000000000c7").orElseThrow();
        assertThat(download.location()).isNull();
        assertThat(download.filename()).isEqualTo("annotated.docx");
        assertThat(download.contentType()).contains("wordprocessingml.document");
        assertThat(download.body().getInputStream().readAllBytes()).containsExactly('P', 'K', 3, 4);
    }

    @Test
    void artifactDownloadRejectsLocalFileOutsideRoot() throws Exception {
        Path outside = Files.createTempFile("outside-artifact", ".docx");
        Files.write(outside, "secret".getBytes(StandardCharsets.UTF_8));
        givenArtifactEvent("00000000-0000-0000-0000-0000000000c8", outside.toUri().toString());

        assertThat(service.artifactDownload("00000000-0000-0000-0000-0000000000c8")).isEmpty();
    }

    @Test
    void artifactDownloadEmptyWhenNotRendered() {
        when(reviewEventRepository.findByReviewIdAndTypeOrderBySeqAsc("00000000-0000-0000-0000-0000000000c5", "run.completed"))
                .thenReturn(List.of());
        assertThat(service.artifactDownload("00000000-0000-0000-0000-0000000000c5")).isEmpty();
    }

    @Test
    void contractDiffPassesThroughIncrementalLedger() {
        Review review = new Review();
        review.setId("00000000-0000-0000-0000-0000000000c6");
        review.setContractId("00000000-0000-0000-0000-0000000000a1");
        review.setVersion(2);
        when(reviewRepository.findFirstByContractIdOrderByCreatedAtDesc("00000000-0000-0000-0000-0000000000a1"))
                .thenReturn(Optional.of(review));
        when(reviewEventRepository.findByReviewIdAndTypeOrderBySeqAsc("00000000-0000-0000-0000-0000000000c6", "run.completed"))
                .thenReturn(List.of(completedEvent("r-diff",
                        "{\"seq\":20,\"type\":\"run.completed\",\"usage\":{\"input_tokens\":10,"
                        + "\"output_tokens\":2,\"cost_cny\":0.1},\"findings_count\":2,\"degraded\":false,"
                        + "\"quality\":{\"verified_rate\":1.0,\"rework_count\":0,\"unverified_count\":0},"
                        + "\"incremental\":{\"mode\":\"incremental\",\"baseline_version\":1,"
                        + "\"changed_count\":2,\"added_count\":1,\"removed_count\":1,"
                        + "\"unchanged_count\":36,\"reused_findings\":15,\"stale_findings\":1,"
                        + "\"cache_hits\":38,\"cache_misses\":2,\"cache_hit_rate\":0.95,"
                        + "\"changed_clause_ids\":[\"doc#0004\",\"doc#0011\"],"
                        + "\"added_clause_ids\":[\"doc#0039\"],\"removed_clause_ids\":[\"doc#0021\"]}}")));

        ContractDiff diff = service.contractDiff("00000000-0000-0000-0000-0000000000a1", null);
        assertThat(diff.getBaselineVersion()).isEqualTo(1);
        assertThat(diff.getTargetVersion()).isEqualTo(2);
        assertThat(diff.getChanged()).containsExactly("doc#0004", "doc#0011");
        assertThat(diff.getAdded()).containsExactly("doc#0039");
        assertThat(diff.getRemoved()).containsExactly("doc#0021");
        assertThat(diff.getStaleFindings()).isEqualTo(1);
    }

    @Test
    void contractDiffReturnsEmptyLedgerWhenVersionNeverReviewed() {
        // 刚上传的新版本还没跑过审查：这是「没有对比数据」而不是「资源不存在」。
        // 回 404 会让前端每切一次新版本就在控制台留一条红色错误（阶段 10 实测到的噪音）。
        when(reviewRepository.findFirstByContractIdAndVersionOrderByCreatedAtDesc(
                "00000000-0000-0000-0000-0000000000a1", 3))
                .thenReturn(Optional.empty());

        ContractDiff diff = service.contractDiff("00000000-0000-0000-0000-0000000000a1", 3);

        assertThat(diff.getTargetVersion()).isEqualTo(3);
        assertThat(diff.getBaselineVersion()).isZero();
        assertThat(diff.getChanged()).isEmpty();
        assertThat(diff.getAdded()).isEmpty();
        assertThat(diff.getRemoved()).isEmpty();
        assertThat(diff.getStaleFindings()).isZero();
    }

    private void givenReviewWithCompleted(String reviewId, int version) {
        Review review = new Review();
        review.setId(reviewId);
        review.setContractId("00000000-0000-0000-0000-0000000000a1");
        review.setVersion(version);
        review.setStatus(ReviewStatus.COMPLETED);
        review.setRunId("run-xyz");
        review.setCreatedAt(Instant.parse("2026-10-25T10:00:00Z"));
        lenient().when(reviewRepository.findById(reviewId)).thenReturn(Optional.of(review));
        lenient().when(reviewEventRepository.findByReviewIdAndTypeOrderBySeqAsc(reviewId, "run.completed"))
                .thenReturn(List.of(completedEvent(reviewId,
                        "{\"seq\":30,\"type\":\"run.completed\","
                        + "\"usage\":{\"input_tokens\":16800,\"output_tokens\":840,\"cost_cny\":0.35},"
                        + "\"findings_count\":2,\"degraded\":false,"
                        + "\"quality\":{\"verified_rate\":1.0,\"rework_count\":0,\"unverified_count\":0},"
                        + "\"findings\":["
                        + "{\"clause_id\":\"doc#0007\",\"agent\":\"commercial-v1\","
                        + "\"risk_type\":\"付款条件缺失\",\"risk_level\":\"high\","
                        + "\"summary\":\"未约定逾期付款违约金\",\"suggestion\":\"增加违约金条款\","
                        + "\"evidence\":\"买方应在验收合格后支付货款\",\"stale\":false,"
                        + "\"citations\":[{\"source_id\":\"LAW-001\",\"title\":\"《民法典》第五百八十六条\","
                        + "\"excerpt\":\"当事人可以约定一方向对方给付定金\",\"url\":null}]},"
                        + "{\"clause_id\":\"doc#0011\",\"agent\":\"liability-v1\","
                        + "\"risk_type\":\"责任上限缺失\",\"risk_level\":\"medium\","
                        + "\"summary\":\"未约定赔偿责任上限\",\"suggestion\":\"\","
                        + "\"evidence\":\"\",\"stale\":false,\"citations\":[]}],"
                        + "\"artifact\":{\"object_key\":\"artifacts/x/annotated.docx\","
                        + "\"download_url\":\"http://minio.local/clauseguard/artifacts/annotated.docx?sig=abc\","
                        + "\"comments_count\":2,\"size_bytes\":48213}}")));
    }

    private void givenArtifactEvent(String reviewId, String downloadUrl) {
        when(reviewEventRepository.findByReviewIdAndTypeOrderBySeqAsc(reviewId, "run.completed"))
                .thenReturn(List.of(completedEvent(reviewId,
                        "{\"seq\":30,\"type\":\"run.completed\",\"artifact\":{"
                                + "\"object_key\":\"artifacts/x/annotated.docx\","
                                + "\"download_url\":\"" + downloadUrl + "\","
                                + "\"comments_count\":1,\"size_bytes\":4}}")));
    }

    private static ReviewEvent completedEvent(String reviewId, String payload) {
        ReviewEvent event = new ReviewEvent();
        event.setReviewId(reviewId);
        event.setSeq(30);
        event.setType("run.completed");
        event.setPayload(payload);
        event.setTs(Instant.parse("2026-10-25T10:05:00Z"));
        return event;
    }

    private void await(BooleanSupplier condition) throws InterruptedException {
        long deadline = System.currentTimeMillis() + 2000;
        while (System.currentTimeMillis() < deadline) {
            if (condition.getAsBoolean()) {
                return;
            }
            Thread.sleep(20);
        }
        throw new AssertionError("condition not satisfied within timeout");
    }
}
