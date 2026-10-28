package com.clauseguard.review;

import com.clauseguard.events.AgentEvent;
import com.clauseguard.events.EventReplayStore;
import com.clauseguard.review.domain.ReviewEvent;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

import java.time.Duration;
import java.time.Instant;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 审查看门狗（Stage 14）：把「长时间没有任何事件的 RUNNING」自动收敛到 FAILED。
 *
 * <p>为什么需要：状态机是「先落 RUNNING，再去开 Python 事件流」，中间没有补偿。事件流一旦断了
 * （进程重启、Redis 降级、下游不可用），状态就永远停在 RUNNING——Redis 降级那次留过 6 条，
 * 最后是靠人发现 + 一次性 SQL 抹平的。这里把这件没人盯的事变成后台自动收敛。
 *
 * <p>为什么按 last_event_at 而不是 created_at 判定（阶段 13 铺好的前提）：
 * 审批续跑的审查 created_at 可能是一周前，用发起时间判超时会误杀正在走审批的合同。
 * 「活动」以事件为准，审批动作本身不入 review_event，因此 AWAITING_APPROVAL 直接从扫描条件里排除。
 *
 * <p>并发：多副本部署时每个实例都会扫描，唯一的安全性来自条件更新的 WHERE——
 * 只有抢到的那一个能拿到 affected rows = 1，其余都是 0。不引入分布式锁，
 * 因为 MySQL 的行锁已经把这单点串行化了，加锁只是把同样的事做第二遍。
 */
@Component
@ConditionalOnProperty(prefix = "clauseguard.review.watchdog", name = "enabled",
        havingValue = "true", matchIfMissing = true)
public class ReviewWatchdog {

    private static final Logger logger = LoggerFactory.getLogger(ReviewWatchdog.class);
    private static final String TYPE_RUN_FAILED = "run.failed";
    private static final String CODE_REVIEW_TIMEOUT = "REVIEW_TIMEOUT";

    private final ReviewRepository reviewRepository;
    private final ReviewEventRepository eventRepository;
    private final EventReplayStore replayStore;
    private final ReviewService reviewService;
    private final ObjectMapper mapper;
    private final Duration timeout;

    /** UTC 墙钟格式化（与 ReviewRepository 看门狗查询、updateLastEventAt 写入口径一致）。 */
    private static final DateTimeFormatter UTC_FMT =
            DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss").withZone(ZoneOffset.UTC);

    public ReviewWatchdog(ReviewRepository reviewRepository,
                          ReviewEventRepository eventRepository,
                          EventReplayStore replayStore,
                          ReviewService reviewService,
                          ObjectMapper mapper,
                          @Value("${clauseguard.review.watchdog.timeout:30m}") Duration timeout) {
        this.reviewRepository = reviewRepository;
        this.eventRepository = eventRepository;
        this.replayStore = replayStore;
        this.reviewService = reviewService;
        this.mapper = mapper;
        this.timeout = timeout;
    }

    @Scheduled(fixedDelayString = "${clauseguard.review.watchdog.interval:5m}")
    @Transactional
    public void reapStuckRunning() {
        try {
            ScanResult result = scan();
            // 安静的系统不该刷屏：只有真扫到候选（或真回收了）才打一条。
            if (result.scanned() > 0) {
                logger.info("review watchdog: scanned={} killed={} skipped={} timeout={}",
                        result.scanned(), result.killed(), result.skipped(), timeout);
            }
        } catch (RuntimeException ex) {
            // 看门狗自己绝不能把调度线程打死：一次 DB 抖动就丢掉后续所有扫描比漏扫更糟。
            logger.warn("review watchdog scan failed: {}", ex.getMessage());
        }
    }

    /**
     * 一次完整扫描。返回计数以便日志与测试断言，不抛业务异常。
     */
    public ScanResult scan() {
        Instant now = Instant.now();
        Instant cutoff = now.minus(timeout);
        // cutoff / now 以 UTC 字符串传入，与 Repository 看门狗查询、last_event_at 写入口径一致，
        // 彻底绕开 Hibernate 把 Instant 绑 DATETIME 列时的时区偏移（否则会静默选不出候选）。
        String nowUtc = UTC_FMT.format(now);
        String cutoffUtc = UTC_FMT.format(cutoff);
        List<String> candidates = reviewRepository.findStaleRunningIds(ReviewStatus.RUNNING.name(), cutoffUtc);
        int killed = 0;
        int skipped = 0;
        for (String reviewId : candidates) {
            int affected = reviewRepository.reapStuckRunning(
                    reviewId, ReviewStatus.FAILED.name(), ReviewStatus.RUNNING.name(), cutoffUtc, nowUtc);
            if (affected == 1) {
                emitTimeoutEvent(reviewId, now);
                killed++;
            } else {
                skipped++;
            }
        }
        return new ScanResult(candidates.size(), killed, skipped);
    }

    /**
     * 判定条件（纯函数，无 IO）。抽出来是为了让「审批中的审查永不被回收」能被断言锁住——
     * 这条一旦漂移，损失的是正在走审批的真实合同。
     *
     * @param lastActivityAt 最后一次有活动的时间；由调用方用 {@link #activityAt} 折算，
     *                       null 表示这条审查还没有任何事件也没有 created_at，无法判定 → 不回收
     */
    static boolean shouldReap(ReviewStatus status, Instant lastActivityAt, Instant now, Duration timeout) {
        // 只认 RUNNING：AWAITING_APPROVAL 可能停留数天（等人批），不是卡死；
        // COMPLETED / FAILED 已是终态，交给状态机自己。
        if (status != ReviewStatus.RUNNING) {
            return false;
        }
        if (lastActivityAt == null || now == null || timeout == null) {
            return false;
        }
        return !lastActivityAt.plus(timeout).isAfter(now);
    }

    /**
     * last_event_at 为 NULL 时兜底 created_at（与 SQL 里的 COALESCE 保持一致）。
     *
     * <p>选兜底而不是直接排除的原因：事件流一条都没来的那种卡死（状态已落库、Python 从未响应）
     * 恰好就是 last_event_at 永远为 NULL 的场景，直接排除等于对这类故障装作看不见。
     * 刚创建的审查 created_at 就在当下，不会被这条规则误伤。
     */
    static Instant activityAt(Instant lastEventAt, Instant createdAt) {
        return lastEventAt != null ? lastEventAt : createdAt;
    }

    /**
     * 回收成功后补一条终态事件，让订阅中的前端收到 run.failed 而不是一直干等。
     * 顺序：先落 review_event，再进重放存储与在线扇出——重放是 SSE 续传的事实源，宁可重复也不断。
     */
    private void emitTimeoutEvent(String reviewId, Instant now) {
        long seq = eventRepository.maxSeq(reviewId) + 1;
        String ts = now.toString();
        String payload = writeJson(seq, ts);

        ReviewEvent row = new ReviewEvent();
        row.setReviewId(reviewId);
        row.setSeq(seq);
        row.setType(TYPE_RUN_FAILED);
        row.setPayload(payload);
        row.setTs(now);
        eventRepository.save(row);

        AgentEvent event = new AgentEvent(seq, ts, TYPE_RUN_FAILED, payload);
        replayStore.append(reviewId, seq, TYPE_RUN_FAILED, payload);
        reviewService.fanoutSystemEvent(reviewId, event);
        logger.info("review watchdog: {} marked FAILED after {} with no events", reviewId, timeout);
    }

    /** 与 Python {@code buildRunFailedEvent} 的字段形状保持一致，前端解析不用分叉。 */
    private String writeJson(long seq, String ts) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("seq", seq);
        body.put("ts", ts);
        body.put("type", TYPE_RUN_FAILED);
        body.put("code", CODE_REVIEW_TIMEOUT);
        body.put("message", "审查超过 " + timeout + " 没有任何事件，判定为卡死并终止");
        body.put("retryable", true);
        try {
            return mapper.writeValueAsString(body);
        } catch (JsonProcessingException ex) {
            throw new IllegalStateException("serialize run.failed failed", ex);
        }
    }

    public record ScanResult(int scanned, int killed, int skipped) {
    }
}
