package com.clauseguard.review;

import com.clauseguard.events.AgentEvent;
import com.clauseguard.events.EventReplayStore;
import com.clauseguard.review.domain.ReviewEvent;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InOrder;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.time.Duration;
import java.time.Instant;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
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
        when(reviewRepository.findStaleRunningIds(anyString(), any(Instant.class))).thenReturn(List.of());

        ReviewWatchdog.ScanResult result = watchdog().scan();

        // AWAITING_APPROVAL / COMPLETED / FAILED 一个都不该进候选集
        verify(reviewRepository).findStaleRunningIds(eq(ReviewStatus.RUNNING.name()), any(Instant.class));
        assertThat(result.scanned()).isZero();
        assertThat(result.killed()).isZero();
    }

    @Test
    void onlyOneSideWinsWhenTwoInstancesRaceOnTheSameRow() {
        String reviewId = "r-race";
        when(reviewRepository.findStaleRunningIds(eq(ReviewStatus.RUNNING.name()), any(Instant.class)))
                .thenReturn(List.of(reviewId, reviewId));
        // 同一条被两个副本同时扫到：第一次条件更新命中，第二次被 WHERE(status=RUNNING) 挡下 → 0 行
        when(reviewRepository.reapStuckRunning(eq(reviewId), eq(ReviewStatus.FAILED.name()),
                eq(ReviewStatus.RUNNING.name()), any(Instant.class), any(Instant.class)))
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
        when(reviewRepository.findStaleRunningIds(eq(ReviewStatus.RUNNING.name()), any(Instant.class)))
                .thenReturn(List.of(reviewId));
        when(reviewRepository.reapStuckRunning(anyString(), anyString(), anyString(),
                any(Instant.class), any(Instant.class))).thenReturn(0);

        ReviewWatchdog.ScanResult result = watchdog().scan();

        assertThat(result.killed()).isZero();
        assertThat(result.skipped()).isEqualTo(1);
        verify(eventRepository, never()).save(any(ReviewEvent.class));
        verify(replayStore, never()).append(anyString(), anyLong(), anyString(), anyString());
    }

    @Test
    void eventIsPersistedBeforeReplayAndFanout() {
        String reviewId = "r-order";
        when(reviewRepository.findStaleRunningIds(eq(ReviewStatus.RUNNING.name()), any(Instant.class)))
                .thenReturn(List.of(reviewId));
        when(reviewRepository.reapStuckRunning(anyString(), anyString(), anyString(),
                any(Instant.class), any(Instant.class))).thenReturn(1);
        when(eventRepository.maxSeq(reviewId)).thenReturn(9L);

        watchdog().scan();

        // 顺序：先落库再进重放/扇出。重放是 SSE 续传的事实源，反过来会漏事件。
        InOrder order = inOrder(eventRepository, replayStore, reviewService);
        order.verify(eventRepository).save(any(ReviewEvent.class));
        order.verify(replayStore).append(eq(reviewId), eq(10L), eq("run.failed"), anyString());
        order.verify(reviewService).fanoutSystemEvent(eq(reviewId), any(AgentEvent.class));
    }
}
