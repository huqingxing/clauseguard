package com.clauseguard.review;

import com.clauseguard.review.domain.Review;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.util.Collection;
import java.util.List;
import java.util.Optional;

@Repository
public interface ReviewRepository extends JpaRepository<Review, String> {

    Optional<Review> findFirstByContractIdOrderByCreatedAtDesc(String contractId);

    Optional<Review> findFirstByContractIdAndVersionOrderByCreatedAtDesc(String contractId, int version);

    /**
     * 待审批队列：按发起时间升序，等最久的排最前——队列是干活的顺序，不是时间线。
     * 分页交给数据库（status 上有天然的低基数过滤，一页 20 条不该把全表拉进内存）。
     */
    Page<Review> findByStatusOrderByCreatedAtAsc(String status, Pageable pageable);

    /**
     * 落库事件时同步刷新 review.last_event_at（阶段 13 写入路径）。
     *
     * <p>为什么用 GREATEST 而不是「直接覆盖」：Python 推送的事件 ts 不保证随 seq 单调递增
     * （同一秒内多条事件、或续跑事件时钟偏差），若最后落库的那条 ts 不是全局最大，直接覆盖会
     * 把更大的值冲掉，导致实时写入与回填语义（MAX(ts)）对不上。取「已有值与事件 ts 的较大者」，
     * 最终等价于 MAX(所有事件 ts)，与迁移回填、阶段 14 超时判定的口径完全一致。COALESCE 处理初始 NULL。
     * 调用方已把 ts 截断到秒（review_event.ts 是秒级 timestamp），两边精度一致。
     * <p>事务由调用方（ReviewService 的 TransactionTemplate）提供：@Modifying 自己不开事务，
     * 少了外层事务会直接抛 TransactionRequiredException。
     */
    /**
     * ts 用格式化字符串（yyyy-MM-dd HH:mm:ss, UTC）而非 Instant：踩坑确认 —— Hibernate 把
     * java.time.Instant 直绑 DATETIME 列时会系统性偏 1 秒（同一 Instant 绑 TIMESTAMP 列却正常），
     * 导致 last_event_at 比 review_event.ts 小 1 秒、与回填的 MAX(ts) 对不上。改用字符串字面让
     * MySQL 自己解析，规避这个绑定缺陷。GREATEST(COALESCE(...,:ts),:ts) 取「已有值与事件 ts 的较大者」，
     * 最终等价于 MAX(所有事件 ts)，与迁移回填、阶段 14 超时判定口径完全一致。
     */
    @Modifying(flushAutomatically = true, clearAutomatically = true)
    @Query(value = "UPDATE review SET last_event_at = GREATEST(COALESCE(last_event_at, :ts), :ts) WHERE id = :id",
            nativeQuery = true)
    void updateLastEventAt(@Param("id") String id, @Param("ts") String ts);

    /**
     * 终态守护（方案 A 的补偿）：只取 id + status，不整行读——review 行含 pending_payload
     * 这类大字段，看门狗每轮扫描不该把它们全捞进内存。
     */
    @Query("select r.id, r.status from Review r where r.status in :statuses")
    List<Object[]> findTerminalReviews(@Param("statuses") Collection<String> statuses);

    /**
     * 按 id 批量取 last_event_at（阶段 14 超时回收：一次拿全页，避免每条审查各查一次 N+1）。
     * 只 select id 与 last_event_at，不整行取——review 行含 pending_payload 等较大字段。
     */
    @Query("SELECT r.id, r.lastEventAt FROM Review r WHERE r.id IN :ids")
    List<Object[]> findLastEventAtByIds(@Param("ids") Collection<String> ids);

    /**
     * 看门狗候选集（Stage 14）：只挑「还在 RUNNING 且最后一次活动早于 cutoff」的 id。
     *
     * <p>lastEventAt 为 NULL 时兜底 createdAt——「状态落了、一条事件都没来」正是最典型的
     * 卡死形态，排除 NULL 会让看门狗对它彻底失明。这里的 COALESCE 条件必须与下面的条件更新
     * 完全一致，否则会出现「选出来但更新不动」（永久 skipped）。
     *
     * <p>用原生 SQL + UTC 字符串参数，绕开 Hibernate 把 Instant 绑 DATETIME 列时的时区偏移
     * （与 updateLastEventAt 同一坑：Instant 绑 DATETIME 会系统性偏 8 小时，导致
     * COALESCE(...) < cutoff 恒为 FALSE、静默地一条都选不出来）。cutoff 由调用方用 UTC 字符串传入，
     * 与 last_event_at 的写入口径一致；本环境下 TIMESTAMP 与 DATETIME 显示在同一墙钟框架，
     * 直接用字符串比较既正确又无需 CONVERT_TZ（避免依赖时区表）。
     */
    @Query(value = "SELECT r.id FROM review r WHERE r.status = :running "
            + "AND COALESCE(r.last_event_at, r.created_at) < :cutoff", nativeQuery = true)
    List<String> findStaleRunningIds(@Param("running") String runningStatus,
                                     @Param("cutoff") String cutoff);

    /**
     * 条件更新（Stage 14）：把单条 RUNNING 置 FAILED，顺手把 last_event_at 推进到回收时刻（now），返回受影响行数。
     *
     * <p>幂等 + 并发安全全靠 WHERE 里的两个条件：多副本同时扫同一批时，只有一个实例
     * 能拿到 1，其余拿 0 —— 拿不到就什么都不做，绝不重复补终态事件。
     * <p>同样用原生 SQL + UTC 字符串（cutoff / now），原因同 findStaleRunningIds：绕开 Instant→DATETIME 的时区偏移。
     */
    @Modifying
    @Query(value = "UPDATE review r SET r.status = :failed, r.last_event_at = :now "
            + "WHERE r.id = :id AND r.status = :running "
            + "AND COALESCE(r.last_event_at, r.created_at) < :cutoff", nativeQuery = true)
    int reapStuckRunning(@Param("id") String id,
                         @Param("failed") String failedStatus,
                         @Param("running") String runningStatus,
                         @Param("cutoff") String cutoff,
                         @Param("now") String now);
}
