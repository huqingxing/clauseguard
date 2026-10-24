package com.clauseguard.review;

import com.clauseguard.agent.PythonAgentClient;
import com.clauseguard.api.model.ApproveRequest;
import com.clauseguard.api.model.ApproveResponse;
import com.clauseguard.api.model.ReviewCreateRequest;
import com.clauseguard.api.model.ReviewCreateResponse;
import com.clauseguard.events.AgentEvent;
import com.clauseguard.events.InMemoryEventReplayStore;
import com.clauseguard.review.domain.Approval;
import com.clauseguard.review.domain.Review;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.github.resilience4j.circuitbreaker.CircuitBreakerRegistry;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Flux;

import java.time.Duration;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import java.util.function.BooleanSupplier;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
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

    private PythonAgentClient agentClient;

    private final InMemoryEventReplayStore replayStore = new InMemoryEventReplayStore();
    private final ObjectMapper mapper = new ObjectMapper();
    private final CircuitBreakerRegistry registry = CircuitBreakerRegistry.ofDefaults();

    private ReviewService service;

    @BeforeEach
    void setup() {
        PythonAgentClient realAgent = new PythonAgentClient(
                WebClient.builder(), mapper, registry,
                "http://localhost:8000", Duration.ofSeconds(30));
        agentClient = spy(realAgent);
        service = new ReviewService(
                reviewRepository, reviewEventRepository, approvalRepository,
                replayStore, agentClient, registry, mapper);
        // 仅 createReview 路径需要 save；其余测试不触发，故标为 lenient 避免严格模式报错。
        lenient().when(reviewRepository.save(any(Review.class))).thenAnswer(i -> i.getArgument(0));
    }

    @Test
    void createReviewPersistsAndStoresStreamedEvents() throws Exception {
        AgentEvent e1 = new AgentEvent(1, "2026-10-23T10:00:00+00:00", "node.started",
                "{\"seq\":1,\"type\":\"node.started\"}");
        AgentEvent e2 = new AgentEvent(2, "2026-10-23T10:00:01+00:00", "run.completed",
                "{\"seq\":2,\"type\":\"run.completed\"}");
        when(agentClient.streamRun(anyString(), anyString(), anyString())).thenReturn(Flux.just(e1, e2));

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
    void agentUnavailableEmitsRunFailedWithRetryable() throws Exception {
        when(agentClient.streamRun(anyString(), anyString(), anyString()))
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
        // 状态切回 RUNNING（恢复后由 run.completed 置 COMPLETED）。
        assertThat(review.getStatus()).isEqualTo(ReviewStatus.RUNNING);
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
