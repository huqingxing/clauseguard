package com.clauseguard.review;

import com.clauseguard.review.domain.Review;
import com.clauseguard.review.domain.ReviewEvent;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.function.Consumer;

/**
 * 「审查状态 + 事件行」的唯一事务写入口（方案 A，替代原先两次独立提交）。
 *
 * <p>修复前 {@code ReviewService.handleAgentEvent} 先用 {@code reviewRepository.save} 改状态
 * （各自自动提交），再在另一个事务里插 {@code review_event}。事件插入一旦失败（Stage 5 的
 * payload 超列宽只是其中一种触发），状态已经 COMMIT，用户端重放永远看不到终态。
 *
 * <p>为什么单独一个 Bean 而不是给 {@code handleAgentEvent} 直接加 {@code @Transactional}：
 * 它由同一 Bean 内的 reactor 回调调用，走不到 Spring 代理，注解会静默失效；而且 SSE 回调线程
 * 本来也没有外层事务可用。把写入挪到独立 Bean，由外部调用 → 代理生效 → 整个写入是一个
 * 本地事务：事件插入抛异常时，前面的状态更新一并回滚。
 *
 * <p>调用方约定：本方法不吞异常。写入失败必须向外抛，由 ReviewService 做补偿或标失败，
 * 禁止「catch 掉只打日志」（那样状态与事件会再次分叉）。
 */
@Service
public class ReviewEventWriter {

    private final ReviewRepository reviewRepository;
    private final ReviewEventRepository eventRepository;

    public ReviewEventWriter(ReviewRepository reviewRepository, ReviewEventRepository eventRepository) {
        this.reviewRepository = reviewRepository;
        this.eventRepository = eventRepository;
    }

    /**
     * 一次事务内完成三件事：应用状态变更 → 插入事件行 → 刷新 last_event_at。
     *
     * @param stateMutation 由事件推导出的状态变更，必须在事务内、对受管实体执行；
     *                      传 no-op 表示这条事件不改状态（如 node.started）
     * @param lastEventAtUtc 秒级 UTC 墙钟字符串；ts 解析失败时传 null，不回写时间戳
     */
    @Transactional
    public void persist(String reviewId,
                        Consumer<Review> stateMutation,
                        ReviewEvent event,
                        String lastEventAtUtc) {
        // 状态变更与事件插入必须在同一个持久化上下文里：先改受管实体，再插事件。
        // 事件行用 IDENTITY 主键，save 会立即执行 INSERT；失败时事务被标记 rollback-only，
        // 上面这一步（以及任何已 flush 的状态更新）在 commit 阶段一起回滚。
        Review review = reviewRepository.findById(reviewId).orElse(null);
        if (review != null) {
            stateMutation.accept(review);
            reviewRepository.save(review);
        }
        eventRepository.save(event);
        if (lastEventAtUtc != null) {
            // updateLastEventAt 上加了 flushAutomatically/clearAutomatically：先把受管实体的
            // 状态 UPDATE 落库，再执行原生 UPDATE 时间戳，最后清空持久化上下文，避免 commit
            // 时 Hibernate 用旧字段值把刚写好的 last_event_at 覆盖回去。
            reviewRepository.updateLastEventAt(reviewId, lastEventAtUtc);
        }
    }
}
