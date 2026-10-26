package com.clauseguard.eval;

import com.clauseguard.review.ReviewEventRepository;
import com.clauseguard.review.ReviewRepository;
import com.clauseguard.review.ReviewStatus;
import com.clauseguard.review.domain.ReviewEvent;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * Stage 12：评测看板聚合。
 *
 * 为什么不建指标表：所有原料（usage/quality/incremental/节点耗时）本来就在
 * review_event 的 run.completed/node.finished 里——再建一张指标表就多一处需要
 * 一致性维护的事实源。演示规模全量扫描事件表足够（规模化风险见阶段报告）。
 */
@Service
public class EvalService {

    private static final String TYPE_RUN_COMPLETED = "run.completed";
    private static final String TYPE_NODE_FINISHED = "node.finished";

    private final ReviewRepository reviewRepository;
    private final ReviewEventRepository eventRepository;
    private final ObjectMapper mapper;

    public EvalService(
            ReviewRepository reviewRepository,
            ReviewEventRepository eventRepository,
            ObjectMapper mapper) {
        this.reviewRepository = reviewRepository;
        this.eventRepository = eventRepository;
        this.mapper = mapper;
    }

    public com.clauseguard.api.model.EvalSummary summary() {
        List<com.clauseguard.review.domain.Review> reviews = reviewRepository.findAll();
        long completed = reviews.stream()
                .filter(r -> r.getStatus() == ReviewStatus.COMPLETED)
                .count();
        long failed = reviews.stream().filter(r -> r.getStatus() == ReviewStatus.FAILED).count();

        List<ReviewEvent> completedEvents =
                eventRepository.findByTypeOrderBySeqAsc(TYPE_RUN_COMPLETED);
        long inputTokens = 0;
        long outputTokens = 0;
        double costCny = 0.0;
        long findings = 0;
        double verifiedRateSum = 0.0;
        double reworkSum = 0.0;
        int completedWithQuality = 0;
        // 增量收益：incremental_tokens 为实际消耗；full_equivalent 按「缓存命中本可
        // 免审的（专家×条款）对」等比折算——这是估算值，语义与 Stage 11 验收报告一致。
        long incrementalTokens = 0;
        long fullEquivalentTokens = 0;
        int incrementalRuns = 0;
        for (ReviewEvent event : completedEvents) {
            JsonNode payload = parse(event.getPayload());
            if (payload == null) {
                continue;
            }
            JsonNode usage = payload.path("usage");
            inputTokens += usage.path("input_tokens").asLong(0);
            outputTokens += usage.path("output_tokens").asLong(0);
            costCny += usage.path("cost_cny").asDouble(0.0);
            findings += payload.path("findings_count").asLong(0);
            JsonNode quality = payload.path("quality");
            if (quality.isObject()) {
                verifiedRateSum += quality.path("verified_rate").asDouble(0.0);
                reworkSum += quality.path("rework_count").asDouble(0.0);
                completedWithQuality++;
            }
            JsonNode incremental = payload.path("incremental");
            if (incremental.isObject() && "incremental".equals(incremental.path("mode").asText())) {
                incrementalRuns++;
                long tokens = usage.path("input_tokens").asLong(0) + usage.path("output_tokens").asLong(0);
                incrementalTokens += tokens;
                long hits = incremental.path("cache_hits").asLong(0);
                long misses = incremental.path("cache_misses").asLong(0);
                if (misses > 0) {
                    // 全量等价 = 实测按 (hits+misses)/misses 放大（命中即免审的对数）。
                    fullEquivalentTokens += Math.round(tokens * (double) (hits + misses) / misses);
                } else {
                    fullEquivalentTokens += tokens;
                }
            }
        }

        // 节点耗时：按 review 汇总 node.finished.duration_ms 后取均值（一次审查一轮图遍历）。
        Map<String, Long> durationByReview = new HashMap<>();
        for (ReviewEvent event : eventRepository.findByTypeOrderBySeqAsc(TYPE_NODE_FINISHED)) {
            JsonNode payload = parse(event.getPayload());
            if (payload == null) {
                continue;
            }
            durationByReview.merge(
                    event.getReviewId(), payload.path("duration_ms").asLong(0), Long::sum);
        }
        double avgDuration = durationByReview.values().stream()
                .mapToLong(Long::longValue)
                .average()
                .orElse(0.0);

        double avgVerifiedRate = completedWithQuality == 0 ? 0.0 : verifiedRateSum / completedWithQuality;
        double avgRework = completedWithQuality == 0 ? 0.0 : reworkSum / completedWithQuality;

        // 生成模型把 integer/number 映射为 Integer/BigDecimal：汇总值先按 long/double
        // 累加，出口处统一转换，避免中间精度丢失与装箱歧义。
        com.clauseguard.api.model.EvalSummary summary = new com.clauseguard.api.model.EvalSummary()
                .totalReviews(reviews.size())
                .completedReviews((int) completed)
                .failedReviews((int) failed)
                .totals(new com.clauseguard.api.model.EvalSummaryTotals()
                        .inputTokens(clampToInt(inputTokens))
                        .outputTokens(clampToInt(outputTokens))
                        .costCny(BigDecimal.valueOf(costCny))
                        .findings(clampToInt(findings)))
                .averages(new com.clauseguard.api.model.EvalSummaryAverages()
                        .verifiedRate(BigDecimal.valueOf(avgVerifiedRate))
                        .reworkCount(BigDecimal.valueOf(avgRework))
                        .durationMs(BigDecimal.valueOf(avgDuration)))
                .incremental(new com.clauseguard.api.model.EvalSummaryIncremental()
                        .runs(incrementalRuns)
                        .fullEquivalentTokens(clampToInt(fullEquivalentTokens))
                        .incrementalTokens(clampToInt(incrementalTokens))
                        .savedRatio(BigDecimal.valueOf(
                                savedRatio(incrementalTokens, fullEquivalentTokens))));
        return summary;
    }

    /** 汇总口径按 int 截断展示（事件量为演示规模，远超 int 上限前不丢语义）。 */
    private static int clampToInt(long value) {
        return (int) Math.max(0, Math.min(value, Integer.MAX_VALUE));
    }

    private static double savedRatio(long incrementalTokens, long fullEquivalentTokens) {
        if (fullEquivalentTokens <= 0) {
            return 0.0;
        }
        double ratio = 1.0 - (double) incrementalTokens / fullEquivalentTokens;
        return Math.max(0.0, Math.min(1.0, ratio));
    }

    private JsonNode parse(String rawJson) {
        try {
            return mapper.readTree(rawJson);
        } catch (Exception ex) {
            return null;
        }
    }
}
