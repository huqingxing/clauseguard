package com.clauseguard.review;

import com.clauseguard.agent.PythonAgentClient;
import com.clauseguard.contract.ContractRepository;
import com.clauseguard.events.AgentEvent;
import com.clauseguard.events.EventReplayStore;
import com.clauseguard.events.InMemoryEventReplayStore;
import com.clauseguard.events.StoredEvent;
import com.clauseguard.observability.TraceContextSupport;
import com.clauseguard.review.domain.Review;
import com.clauseguard.review.domain.ReviewEvent;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.github.resilience4j.circuitbreaker.CircuitBreakerRegistry;
import io.micrometer.tracing.Tracer;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.web.reactive.function.client.WebClient;

import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.function.Consumer;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doAnswer;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.spy;
import static org.mockito.Mockito.when;

/**
 * 方案 A 的编排层单测：状态变更必须发生在事务写入口内部，写失败要按优先级补偿。
 *
 * <p>真实的「事务回滚后 DB 里 status 仍为 RUNNING」由
 * {@link ReviewEventConsistencyIntegrationTest} 用本地 MySQL 验证（需
 * -Dclauseguard.db.integration=true）；这里用 mock writer 把「服务层不在事务外提前改状态」
 * 与「失败后怎么补偿」两条契约锁住，无需数据库即可跑。
 */
@ExtendWith(MockitoExtension.class)
class ReviewEventAtomicityTest {

    private static final String TS = "2026-11-12T00:00:00Z";

    @Mock
    private ReviewRepository reviewRepository;
    @Mock
    private ReviewEventRepository eventRepository;
    @Mock
    private ApprovalRepository approvalRepository;
    @Mock
    private ContractRepository contractRepository;
    @Mock
    private ReviewEventWriter eventWriter;

    private final ObjectMapper mapper = new ObjectMapper();
    private final CircuitBreakerRegistry registry = CircuitBreakerRegistry.ofDefaults();
    private PythonAgentClient agentClient;
    private EventReplayStore replayStore;
    private ReviewService service;

    @SuppressWarnings("unchecked")
    private static ObjectProvider<Tracer> noTracer() {
        ObjectProvider<Tracer> provider = org.mockito.Mockito.mock(ObjectProvider.class);
        lenient().when(provider.getIfAvailable()).thenReturn(null);
        return provider;
    }

    @BeforeEach
    void setup() {
        PythonAgentClient real = new PythonAgentClient(
                WebClient.builder(), mapper, registry, "http://localhost:8000", Duration.ofSeconds(30));
        agentClient = spy(real);
        replayStore = new InMemoryEventReplayStore();
        service = serviceWith(replayStore);
    }

    private ReviewService serviceWith(EventReplayStore store) {
        return new ReviewService(
                reviewRepository, eventRepository, approvalRepository, store,
                agentClient, registry, mapper, contractRepository,
                new TraceContextSupport(noTracer()), eventWriter,
                new ApprovalApplier(reviewRepository, approvalRepository),
                "target/test-artifacts");
    }

    private static AgentEvent completed(long seq) {
        return new AgentEvent(seq, TS, "run.completed",
                "{\"seq\":" + seq + ",\"ts\":\"" + TS + "\",\"type\":\"run.completed\","
                        + "\"usage\":{\"input_tokens\":1,\"output_tokens\":1,\"cost_cny\":0.1},"
                        + "\"findings_count\":0,\"quality\":{\"verified_rate\":1.0,"
                        + "\"rework_count\":0,\"unverified_count\":0}}");
    }

    private static AgentEvent nodeStarted(long seq) {
        return new AgentEvent(seq, TS, "node.started",
                "{\"seq\":" + seq + ",\"ts\":\"" + TS + "\",\"type\":\"node.started\","
                        + "\"node\":\"review.liability\",\"agent\":\"liability_expert\"}");
    }

    // ---- 事务回滚场景：服务层不得在事务外先把状态改掉 ----

    @Test
    void terminalWriteFailureNeverAdvancesStatusAndCompensatesWithRunFailed() {
        Review review = new Review();
        review.setId("r-rollback");
        review.setStatus(ReviewStatus.RUNNING);
        List<String> statusWhenWriterFailed = new ArrayList<>();
        int[] calls = {0};
        doAnswer(inv -> {
            calls[0]++;
            if (calls[0] == 1) {
                // 模拟 @Transactional 抛异常回滚：状态变更根本没被应用
                statusWhenWriterFailed.add(review.getStatus().name());
                throw new DataIntegrityViolationException("Data too long for column 'payload'");
            }
            // 补偿调用（新事务）：执行状态变更 lambda，把审查标成 FAILED
            Consumer<Review> mutation = inv.getArgument(1);
            mutation.accept(review);
            return null;
        }).when(eventWriter).persist(eq("r-rollback"), any(), any(ReviewEvent.class), any());

        service.handleAgentEvent("r-rollback", completed(9));

        assertThat(statusWhenWriterFailed).containsExactly("RUNNING");
        assertThat(review.getStatus()).isEqualTo(ReviewStatus.FAILED);
        assertThat(calls[0]).isEqualTo(2);
        // 绝不能出现「COMPLETED 但没有 run.completed」：重放里只有补偿终态
        List<StoredEvent> events = replayStore.replaySince("r-rollback", -1);
        assertThat(events).hasSize(1);
        assertThat(events.get(0).type()).isEqualTo("run.failed");
        assertThat(events.get(0).payload()).contains("EVENT_WRITE_FAILED").contains("\"retryable\":true");
        assertThat(replayStore.replaySince("r-rollback", -1))
                .noneMatch(e -> "run.completed".equals(e.type()));
    }

    @Test
    void normalPathMutatesStatusOnlyInsideWriter() {
        Review review = new Review();
        review.setId("r-ok");
        review.setStatus(ReviewStatus.RUNNING);
        // 刻意不 stub findById：状态变更只应发生在 writer 内部，服务层不该自己去查 review
        doAnswer(inv -> {
            Consumer<Review> mutation = inv.getArgument(1);
            mutation.accept(review);
            return null;
        }).when(eventWriter).persist(eq("r-ok"), any(), any(ReviewEvent.class), any());

        service.handleAgentEvent("r-ok", completed(2));

        assertThat(review.getStatus()).isEqualTo(ReviewStatus.COMPLETED);
        assertThat(replayStore.replaySince("r-ok", -1)).hasSize(1);
        assertThat(replayStore.replaySince("r-ok", -1).get(0).type()).isEqualTo("run.completed");
    }

    // ---- 终态最高优先级：非关键事件写失败不能把流打断、把终态弄丢 ----

    @Test
    void nonCriticalWriteFailureDegradesToDiagnosticAndTerminalStillArrives() {
        int[] calls = {0};
        doAnswer(inv -> {
            calls[0]++;
            if (calls[0] == 1) {
                throw new DataIntegrityViolationException("Data too long for column 'payload'");
            }
            return null;
        }).when(eventWriter).persist(eq("r-priority"), any(), any(ReviewEvent.class), any());

        service.handleAgentEvent("r-priority", nodeStarted(1));
        service.handleAgentEvent("r-priority", completed(2));

        List<StoredEvent> events = replayStore.replaySince("r-priority", -1);
        assertThat(events).hasSize(2);
        assertThat(events.get(0).seq()).isEqualTo(1);
        assertThat(events.get(0).type()).isEqualTo("node.failed");
        assertThat(events.get(0).payload()).contains("EVENT_PERSIST_FAILED");
        // 真正终态仍然到达并进入重放（?since=0 拿得到）
        assertThat(events.get(1).type()).isEqualTo("run.completed");
        assertThat(replayStore.replaySince("r-priority", 0))
                .anyMatch(e -> "run.completed".equals(e.type()));
    }

    @Test
    void stateCriticalWriteFailurePropagatesToCaller() {
        doThrow(new DataIntegrityViolationException("boom"))
                .when(eventWriter).persist(eq("r-critical"), any(), any(ReviewEvent.class), any());
        AgentEvent runStarted = new AgentEvent(1, TS, "run.started",
                "{\"seq\":1,\"ts\":\"" + TS + "\",\"type\":\"run.started\","
                        + "\"run_id\":\"run-abc\",\"trace_id\":\"trace-abc\"}");

        // run_id / interrupt 基线丢了没法 resume：不降级，交给调用方（reactor onError）标失败
        assertThatThrownBy(() -> service.handleAgentEvent("r-critical", runStarted))
                .isInstanceOf(DataIntegrityViolationException.class);
        assertThat(replayStore.replaySince("r-critical", -1)).isEmpty();
    }

    // ---- 补偿场景：重放投影写失败，从 DB 事实源按 seq 重建终态 ----

    @Test
    void replayProjectionFailureIsRepairedFromEventTable() {
        EventReplayStore flaky = spy(new InMemoryEventReplayStore());
        doThrow(new RuntimeException("redis down"))
                .doCallRealMethod()
                .when(flaky).append(anyString(), anyLong(), anyString(), anyString());
        service = serviceWith(flaky);

        ReviewEvent row = new ReviewEvent();
        row.setReviewId("r-rebuild");
        row.setSeq(7);
        row.setType("run.completed");
        row.setPayload(completed(7).rawJson());
        when(eventRepository.findByReviewIdOrderBySeqAsc("r-rebuild")).thenReturn(List.of(row));

        service.handleAgentEvent("r-rebuild", completed(7));

        assertThat(flaky.replaySince("r-rebuild", -1)).hasSize(1);
        assertThat(flaky.replaySince("r-rebuild", -1).get(0).type()).isEqualTo("run.completed");
        assertThat(flaky.replaySince("r-rebuild", 0))
                .anyMatch(e -> "run.completed".equals(e.type()));
    }
}
