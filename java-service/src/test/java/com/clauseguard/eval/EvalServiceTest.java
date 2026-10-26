package com.clauseguard.eval;

import com.clauseguard.review.ReviewEventRepository;
import com.clauseguard.review.ReviewRepository;
import com.clauseguard.review.ReviewStatus;
import com.clauseguard.review.domain.Review;
import com.clauseguard.review.domain.ReviewEvent;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.time.Instant;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.when;

/**
 * EvalService 单元测试：聚合口径（总数/完成/失败、token 汇总、质量均值、增量收益折算）。
 * 数据全部来自 review_event 的 run.completed/node.finished，不建指标表（Stage 12 设计）。
 */
@ExtendWith(MockitoExtension.class)
class EvalServiceTest {

    @Mock
    private ReviewRepository reviewRepository;

    @Mock
    private ReviewEventRepository eventRepository;

    private final ObjectMapper mapper = new ObjectMapper();

    private EvalService service() {
        return new EvalService(reviewRepository, eventRepository, mapper);
    }

    @Test
    void summaryAggregatesUsageQualityAndIncrementalSaving() {
        when(reviewRepository.findAll()).thenReturn(List.of(
                review("r-1", ReviewStatus.COMPLETED),
                review("r-2", ReviewStatus.COMPLETED),
                review("r-3", ReviewStatus.FAILED)));
        when(eventRepository.findByTypeOrderBySeqAsc("run.completed")).thenReturn(List.of(
                completed("r-1",
                        "{\"usage\":{\"input_tokens\":100,\"output_tokens\":20,\"cost_cny\":0.5},"
                                + "\"findings_count\":3,\"quality\":{\"verified_rate\":1.0,\"rework_count\":0,"
                                + "\"unverified_count\":0},"
                                + "\"incremental\":{\"mode\":\"full\"}}"),
                completed("r-2",
                        "{\"usage\":{\"input_tokens\":10,\"output_tokens\":2,\"cost_cny\":0.05},"
                                + "\"findings_count\":2,\"quality\":{\"verified_rate\":0.5,\"rework_count\":1,"
                                + "\"unverified_count\":1},"
                                + "\"incremental\":{\"mode\":\"incremental\",\"cache_hits\":38,"
                                + "\"cache_misses\":2}}")));
        when(eventRepository.findByTypeOrderBySeqAsc("node.finished")).thenReturn(List.of(
                nodeFinished("r-1", 100), nodeFinished("r-1", 50), nodeFinished("r-2", 40)));

        com.clauseguard.api.model.EvalSummary summary = service().summary();

        assertThat(summary.getTotalReviews()).isEqualTo(3);
        assertThat(summary.getCompletedReviews()).isEqualTo(2);
        assertThat(summary.getFailedReviews()).isEqualTo(1);
        assertThat(summary.getTotals().getInputTokens()).isEqualTo(110);
        assertThat(summary.getTotals().getOutputTokens()).isEqualTo(22);
        assertThat(summary.getTotals().getFindings()).isEqualTo(5);
        // 质量均值：(1.0 + 0.5)/2 = 0.75；打回均值 0.5。
        assertThat(summary.getAverages().getVerifiedRate().doubleValue()).isEqualTo(0.75);
        assertThat(summary.getAverages().getReworkCount().doubleValue()).isEqualTo(0.5);
        // 节点耗时：r-1 合计 150ms、r-2 40ms → 均值 95ms。
        assertThat(summary.getAverages().getDurationMs().doubleValue()).isEqualTo(95.0);
        // 增量收益：r-2 实测 12 token，命中 38/未命中 2 → 全量等价 12*40/2=240，
        // saved_ratio = 1 - 12/240 = 0.95。
        assertThat(summary.getIncremental().getRuns()).isEqualTo(1);
        assertThat(summary.getIncremental().getIncrementalTokens()).isEqualTo(12);
        assertThat(summary.getIncremental().getFullEquivalentTokens()).isEqualTo(240);
        assertThat(summary.getIncremental().getSavedRatio().doubleValue()).isEqualTo(0.95);
    }

    @Test
    void summaryWithNoEventsIsAllZero() {
        when(reviewRepository.findAll()).thenReturn(List.of());
        when(eventRepository.findByTypeOrderBySeqAsc("run.completed")).thenReturn(List.of());
        when(eventRepository.findByTypeOrderBySeqAsc("node.finished")).thenReturn(List.of());

        com.clauseguard.api.model.EvalSummary summary = service().summary();
        assertThat(summary.getTotalReviews()).isZero();
        assertThat(summary.getAverages().getVerifiedRate().doubleValue()).isZero();
        assertThat(summary.getIncremental().getSavedRatio().doubleValue()).isZero();
    }

    private static Review review(String id, ReviewStatus status) {
        Review review = new Review();
        review.setId(id);
        review.setContractId("00000000-0000-0000-0000-0000000000a1");
        review.setVersion(1);
        review.setStatus(status);
        review.setCreatedAt(Instant.parse("2026-10-25T10:00:00Z"));
        return review;
    }

    private static ReviewEvent completed(String reviewId, String payload) {
        ReviewEvent event = new ReviewEvent();
        event.setReviewId(reviewId);
        event.setSeq(30);
        event.setType("run.completed");
        event.setPayload(payload);
        event.setTs(Instant.parse("2026-10-25T10:05:00Z"));
        return event;
    }

    private static ReviewEvent nodeFinished(String reviewId, long durationMs) {
        ReviewEvent event = new ReviewEvent();
        event.setReviewId(reviewId);
        event.setSeq(5);
        event.setType("node.finished");
        event.setPayload("{\"duration_ms\":" + durationMs + "}");
        event.setTs(Instant.parse("2026-10-25T10:01:00Z"));
        return event;
    }
}
