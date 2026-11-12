package com.clauseguard.review;

import ch.qos.logback.classic.Level;
import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import com.clauseguard.events.AgentEvent;
import com.clauseguard.events.EventReplayStore;
import com.clauseguard.events.StoredEvent;
import com.clauseguard.review.domain.ReviewEvent;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.InOrder;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.slf4j.LoggerFactory;

import java.time.Duration;
import java.time.Instant;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyCollection;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.inOrder;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 看门狗单元测试（Stage 14）。不启 Spring 上下文——@Scheduled 在测试里会跟着 clock 乱跑，
 * 关它的手段放在 application.yml（clauseguard.review.watchdog.enabled=false）而不是靠睡 5 分钟。
 * 这里只验判定逻辑与「多条抢同一行只转一次」。
 */
@ExtendWith(MockitoExtension.class)
class ReviewWatchdogTest {

    private static final Duration TIMEOUT = Duration.ofMinutes(30);
    private static final Instant NOW = Instant.parse("2026-10-28T02:00:00Z");

    @Mock
    private ReviewRepository reviewRepository;
    @Mock
    private ReviewEventRepository eventRepository;
    @Mock
    private EventReplayStore replayStore;
    @Mock
    private ReviewService reviewService;

    private ReviewWatchdog watchdog() {
        return new ReviewWatchdog(reviewRepository, eventRepository, replayStore, reviewService,
                new ObjectMapper(), TIMEOUT);
    }

    // ---- 判定条件（纯函数） ----

    @Test
    void runningBeyondTimeoutIsReaped() {
        Instant lastActivity = NOW.minus(Duration.ofMinutes(41));

        assertThat(ReviewWatchdog.shouldReap(ReviewStatus.RUNNING, lastActivity, NOW, TIMEOUT)).isTrue();
        // 边界：刚好等于阈值还不算卡死，避免与正常长审查抢在同一毫秒
        assertThat(ReviewWatchdog.shouldReap(
                ReviewStatus.RUNNING, NOW.minus(TIMEOUT), NOW, TIMEOUT)).isTrue();
        assertThat(ReviewWatchdog.shouldReap(
                ReviewStatus.RUNNING, NOW.minus(Duration.ofMinutes(29)), NOW, TIMEOUT)).isFalse();
    }

    @Test
    void awaitingApprovalIsNeverReapedEvenWhenStaleForDays() {
        Instant staleForDays = NOW.minus(Duration.ofDays(7));

        // 这是最容易被误伤的一条：审批中可以停留数天等人批，超时不该由看门狗终结
        assertThat(ReviewWatchdog.shouldReap(
                ReviewStatus.AWAITING_APPROVAL, staleForDays, NOW, TIMEOUT)).isFalse();
        assertThat(ReviewWatchdog.shouldReap(
                ReviewStatus.COMPLETED, staleForDays, NOW, TIMEOUT)).isFalse();
        assertThat(ReviewWatchdog.shouldReap(
                ReviewStatus.FAILED, staleForDays, NOW, TIMEOUT)).isFalse();
    }

    @Test
    void missingActivityTimeFallsBackToCreatedAtAndThenIsReaped() {
        Instant createdAt = NOW.minus(Duration.ofMinutes(45));

        // 一条事件都没有的审查（事件流从未通）正是靠 created_at 兜底才能被回收
        assertThat(ReviewWatchdog.activityAt(null, createdAt)).isEqualTo(createdAt);
        assertThat(ReviewWatchdog.shouldReap(ReviewStatus.RUNNING, createdAt, NOW, TIMEOUT)).isTrue();
        // 兜底也兜不出来时（既无事件也无 created_at）宁可不回收，也不把未知情况判成卡死
        assertThat(ReviewWatchdog.activityAt(null, null)).isNull();
        assertThat(ReviewWatchdog.shouldReap(ReviewStatus.RUNNING, null, NOW, TIMEOUT)).isFalse();
    }

    // ---- 扫描与并发 ----

    @Test
    void scanOnlyAsksForRunningStatus() {
        when(reviewRepository.findStaleRunningIds(anyString(), anyString())).thenReturn(List.of());

        ReviewWatchdog.ScanResult result = watchdog().scan();

        // AWAITING_APPROVAL / COMPLETED / FAILED 一个都不该进候选集
        verify(reviewRepository).findStaleRunningIds(eq(ReviewStatus.RUNNING.name()), anyString());
        assertThat(result.scanned()).isZero();
        assertThat(result.killed()).isZero();
    }

    @Test
    void onlyOneSideWinsWhenTwoInstancesRaceOnTheSameRow() {
        String reviewId = "r-race";
        when(reviewRepository.findStaleRunningIds(eq(ReviewStatus.RUNNING.name()), anyString()))
                .thenReturn(List.of(reviewId, reviewId));
        // 同一条被两个副本同时扫到：第一次条件更新命中，第二次被 WHERE(status=RUNNING) 挡下 → 0 行
        when(reviewRepository.reapStuckRunning(eq(reviewId), eq(ReviewStatus.FAILED.name()),
                eq(ReviewStatus.RUNNING.name()), anyString(), anyString()))
                .thenReturn(1, 0);
        when(eventRepository.maxSeq(reviewId)).thenReturn(52L);

        ReviewWatchdog.ScanResult result = watchdog().scan();

        assertThat(result.scanned()).isEqualTo(2);
        assertThat(result.killed()).isEqualTo(1);
        assertThat(result.skipped()).isEqualTo(1);
        // 赢的那次补事件，输的那次什么都不做：绝不重复写终态
        verify(eventRepository, times(1)).save(any(ReviewEvent.class));
        verify(replayStore, times(1)).append(eq(reviewId), eq(53L), eq("run.failed"), anyString());
        verify(reviewService, times(1)).fanoutSystemEvent(eq(reviewId), any(AgentEvent.class));
    }

    @Test
    void losingCandidateGeneratesNoEventAtAll() {
        String reviewId = "r-skipped";
        when(reviewRepository.findStaleRunningIds(eq(ReviewStatus.RUNNING.name()), anyString()))
                .thenReturn(List.of(reviewId));
        when(reviewRepository.reapStuckRunning(anyString(), anyString(), anyString(),
                anyString(), anyString())).thenReturn(0);

        ReviewWatchdog.ScanResult result = watchdog().scan();

        assertThat(result.killed()).isZero();
        assertThat(result.skipped()).isEqualTo(1);
        verify(eventRepository, never()).save(any(ReviewEvent.class));
        verify(replayStore, never()).append(anyString(), anyLong(), anyString(), anyString());
    }

    @Test
    void eventIsPersistedBeforeReplayAndFanout() {
        String reviewId = "r-order";
        when(reviewRepository.findStaleRunningIds(eq(ReviewStatus.RUNNING.name()), anyString()))
                .thenReturn(List.of(reviewId));
        when(reviewRepository.reapStuckRunning(anyString(), anyString(), anyString(),
                anyString(), anyString())).thenReturn(1);
        when(eventRepository.maxSeq(reviewId)).thenReturn(9L);

        watchdog().scan();

        // 顺序：先落库再进重放/扇出。重放是 SSE 续传的事实源，反过来会漏事件。
        InOrder order = inOrder(eventRepository, replayStore, reviewService);
        order.verify(eventRepository).save(any(ReviewEvent.class));
        order.verify(replayStore).append(eq(reviewId), eq(10L), eq("run.failed"), anyString());
        order.verify(reviewService).fanoutSystemEvent(eq(reviewId), any(AgentEvent.class));
    }

    // ---- 阶段 4：终态缺口补偿（状态已终态、终态事件缺失 → 补一条 + WARN 记录修复次数）----

    @Test
    void terminalGapIsRepairedOnceAndWarnedWithRepairCount() {
        String id = "r-gap";
        when(reviewRepository.findTerminalReviews(anyCollection()))
                .thenReturn(List.<Object[]>of(new Object[]{id, "COMPLETED"}));
        when(eventRepository.findByReviewIdOrderBySeqAsc(id)).thenReturn(List.of());
        when(eventRepository.maxSeq(id)).thenReturn(4L);
        when(eventRepository.insertTerminalIfAbsent(eq(id), eq(5L), eq("run.completed"),
                anyString(), anyString(), anyCollection())).thenReturn(1);

        Logger log = (Logger) LoggerFactory.getLogger(ReviewWatchdog.class);
        ListAppender<ILoggingEvent> appender = new ListAppender<>();
        appender.start();
        log.addAppender(appender);
        ReviewWatchdog.RepairResult result;
        try {
            result = watchdog().repairTerminalGaps();
        } finally {
            log.detachAppender(appender);
            appender.stop();
        }

        assertThat(result.dbWritten()).isEqualTo(1);
        assertThat(result.replayRestored()).isZero();
        assertThat(result.failed()).isZero();
        assertThat(result.totalRepaired()).isEqualTo(1);
        verify(replayStore).append(eq(id), eq(5L), eq("run.completed"), anyString());
        verify(reviewService).fanoutSystemEvent(eq(id), any(AgentEvent.class));
        // 补写事件必须同步推进 last_event_at，否则阶段 13 的 MAX(review_event.ts) 口径又会对不上
        verify(reviewRepository).updateLastEventAt(eq(id), anyString());
        // 修复次数必须可观测：单轮计数 + 累计计数都要出现在 WARN 里
        assertThat(appender.list).anySatisfy(e -> {
            assertThat(e.getLevel()).isEqualTo(Level.WARN);
            assertThat(e.getFormattedMessage()).contains("dbWritten=1").contains("累计修复次数=1");
        });
    }

    @Test
    void compensatedCompletedPayloadKeepsEventSchemaFields() {
        String id = "r-schema";
        when(reviewRepository.findTerminalReviews(anyCollection()))
                .thenReturn(List.<Object[]>of(new Object[]{id, "COMPLETED"}));
        when(eventRepository.findByReviewIdOrderBySeqAsc(id)).thenReturn(List.of());
        when(eventRepository.maxSeq(id)).thenReturn(0L);
        when(eventRepository.insertTerminalIfAbsent(eq(id), eq(1L), eq("run.completed"),
                anyString(), anyString(), anyCollection())).thenReturn(1);

        watchdog().repairTerminalGaps();

        ArgumentCaptor<String> payload = ArgumentCaptor.forClass(String.class);
        verify(eventRepository).insertTerminalIfAbsent(eq(id), eq(1L), eq("run.completed"),
                payload.capture(), anyString(), anyCollection());
        // 不改事件 schema：run.completed 的必填字段齐全，没有私加字段
        assertThat(payload.getValue())
                .contains("\"type\":\"run.completed\"")
                .contains("\"usage\"")
                .contains("\"input_tokens\":0")
                .contains("\"findings_count\":0")
                .contains("\"quality\"")
                .contains("\"verified_rate\":1.0")
                .contains("\"degraded\":true");
    }

    @Test
    void compensatedFailedPayloadUsesRunFailedShape() {
        String id = "r-failed-gap";
        when(reviewRepository.findTerminalReviews(anyCollection()))
                .thenReturn(List.<Object[]>of(new Object[]{id, "FAILED"}));
        when(eventRepository.findByReviewIdOrderBySeqAsc(id)).thenReturn(List.of());
        when(eventRepository.maxSeq(id)).thenReturn(11L);
        when(eventRepository.insertTerminalIfAbsent(eq(id), eq(12L), eq("run.failed"),
                anyString(), anyString(), anyCollection())).thenReturn(1);

        watchdog().repairTerminalGaps();

        ArgumentCaptor<String> payload = ArgumentCaptor.forClass(String.class);
        verify(eventRepository).insertTerminalIfAbsent(eq(id), eq(12L), eq("run.failed"),
                payload.capture(), anyString(), anyCollection());
        assertThat(payload.getValue())
                .contains("\"type\":\"run.failed\"")
                .contains("\"code\":\"TERMINAL_EVENT_REPAIRED\"")
                .contains("\"retryable\":false");
    }

    @Test
    void secondScanIsIdempotentAndDoesNotDoubleWrite() {
        String id = "r-idem";
        when(reviewRepository.findTerminalReviews(anyCollection()))
                .thenReturn(List.<Object[]>of(new Object[]{id, "FAILED"}));
        when(eventRepository.findByReviewIdOrderBySeqAsc(id))
                .thenReturn(List.of(terminalRow(id, 5, "run.failed")));
        when(replayStore.replaySince(id, -1))
                .thenReturn(List.of(new StoredEvent(5, "run.failed", "{}")));

        ReviewWatchdog dog = watchdog();
        ReviewWatchdog.RepairResult first = dog.repairTerminalGaps();
        ReviewWatchdog.RepairResult second = dog.repairTerminalGaps();

        assertThat(first.dbWritten()).isZero();
        assertThat(first.replayRestored()).isZero();
        assertThat(second.totalRepaired()).isZero();
        verify(eventRepository, never()).insertTerminalIfAbsent(
                anyString(), anyLong(), anyString(), anyString(), anyString(), anyCollection());
    }

    @Test
    void missingProjectionIsRestoredFromDbWithoutWritingAnotherRow() {
        String id = "r-projection";
        when(reviewRepository.findTerminalReviews(anyCollection()))
                .thenReturn(List.<Object[]>of(new Object[]{id, "COMPLETED"}));
        when(eventRepository.findByReviewIdOrderBySeqAsc(id))
                .thenReturn(List.of(terminalRow(id, 3, "run.completed")));
        when(replayStore.replaySince(id, -1)).thenReturn(List.of());

        ReviewWatchdog.RepairResult result = watchdog().repairTerminalGaps();

        assertThat(result.dbWritten()).isZero();
        assertThat(result.replayRestored()).isEqualTo(1);
        assertThat(result.totalRepaired()).isEqualTo(1);
        verify(replayStore).append(eq(id), eq(3L), eq("run.completed"), anyString());
        verify(eventRepository, never()).insertTerminalIfAbsent(
                anyString(), anyLong(), anyString(), anyString(), anyString(), anyCollection());
    }

    @Test
    void conditionalInsertLosingRaceWritesNothing() {
        String id = "r-race-repair";
        when(reviewRepository.findTerminalReviews(anyCollection()))
                .thenReturn(List.<Object[]>of(new Object[]{id, "FAILED"}));
        when(eventRepository.findByReviewIdOrderBySeqAsc(id)).thenReturn(List.of());
        when(eventRepository.maxSeq(id)).thenReturn(9L);
        // 另一个副本已经抢先补过：条件 INSERT 返回 0 行
        when(eventRepository.insertTerminalIfAbsent(eq(id), eq(10L), eq("run.failed"),
                anyString(), anyString(), anyCollection())).thenReturn(0);

        ReviewWatchdog.RepairResult result = watchdog().repairTerminalGaps();

        assertThat(result.dbWritten()).isZero();
        assertThat(result.totalRepaired()).isZero();
        verify(replayStore, never()).append(anyString(), anyLong(), anyString(), anyString());
        verify(reviewService, never()).fanoutSystemEvent(anyString(), any(AgentEvent.class));
    }

    @Test
    void oneFailedRepairDoesNotAbortTheRestOfTheBatch() {
        when(reviewRepository.findTerminalReviews(anyCollection()))
                .thenReturn(List.<Object[]>of(new Object[]{"r-bad", "FAILED"}, new Object[]{"r-good", "COMPLETED"}));
        when(eventRepository.findByReviewIdOrderBySeqAsc("r-bad"))
                .thenThrow(new RuntimeException("db hiccup"));
        when(eventRepository.findByReviewIdOrderBySeqAsc("r-good")).thenReturn(List.of());
        when(eventRepository.maxSeq("r-good")).thenReturn(2L);
        when(eventRepository.insertTerminalIfAbsent(eq("r-good"), eq(3L), eq("run.completed"),
                anyString(), anyString(), anyCollection())).thenReturn(1);

        ReviewWatchdog.RepairResult result = watchdog().repairTerminalGaps();

        assertThat(result.failed()).isEqualTo(1);
        assertThat(result.dbWritten()).isEqualTo(1);
        assertThat(result.totalRepaired()).isEqualTo(1);
    }

    private static ReviewEvent terminalRow(String reviewId, long seq, String type) {
        ReviewEvent event = new ReviewEvent();
        event.setReviewId(reviewId);
        event.setSeq(seq);
        event.setType(type);
        event.setPayload("{\"seq\":" + seq + ",\"type\":\"" + type + "\"}");
        event.setTs(Instant.parse("2026-10-28T01:00:00Z"));
        return event;
    }
}
