package com.clauseguard.review;

import com.clauseguard.review.domain.ReviewEvent;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.util.Collection;
import java.util.List;

@Repository
public interface ReviewEventRepository extends JpaRepository<ReviewEvent, Long> {

    List<ReviewEvent> findByReviewIdAndTypeOrderBySeqAsc(String reviewId, String type);

    /** Stage 12 评测看板：聚合所有 run.completed（演示规模全量加载，见报告遗留风险）。 */
    List<ReviewEvent> findByTypeOrderBySeqAsc(String type);

    /**
     * 批量取「各审查进入某状态的时间」（Stage 5 待审批队列算等待时长）。
     *
     * <p>只 select review_id 与 max(ts)，不整行取：payload 是整份待审快照（几百字节到几 KB），
     * 队列一页 20 条就是白读几十 KB，而这里只需要一个时间戳。
     * 一次 group by 拿全页，避免每条审查各查一次（N+1）。
     */
    @Query("select e.reviewId, max(e.ts) from ReviewEvent e "
            + "where e.reviewId in :reviewIds and e.type = :type group by e.reviewId")
    List<Object[]> findLatestTsByType(
            @Param("reviewIds") Collection<String> reviewIds, @Param("type") String type);

    /**
     * 某条审查当前最大的事件 seq；没有任何事件返回 -1（coalesce 兜底，调用方直接 +1 即下一条）。
     * Stage 14：看门狗补写终态事件时必须接着已有序列编号，不能从 0 重排——前端按 seq 去重与续传。
     */
    @Query("select coalesce(max(e.seq), -1) from ReviewEvent e where e.reviewId = :reviewId")
    long maxSeq(@Param("reviewId") String reviewId);

    /** 按 seq 升序取一条审查的全部事件：重放投影补偿时按此顺序重建，保证 seq 不重排。 */
    List<ReviewEvent> findByReviewIdOrderBySeqAsc(String reviewId);

    /** 是否已有终态事件（事务回滚后判断要不要标失败、看门狗判断要不要补写）。 */
    long countByReviewIdAndTypeIn(String reviewId, Collection<String> types);

    /**
     * 原子条件补写终态事件：仅当该审查一条终态事件都没有时插入，返回受影响行数。
     *
     * <p>为什么不用「先 count 再 save」：多副本同时扫到同一笔时会各补一条，前端会收到两个终态。
     * WHERE NOT EXISTS 把判定与插入放到同一条 SQL 里，靠唯一写入方（数据库）串行化，
     * 不引入分布式锁/新中间件。派生表 {@code (SELECT ...)} 是 MySQL 1093 限制
     * （不能在同一语句里直接读目标表）的规避写法。
     */
    @Modifying
    @Query(value = "INSERT INTO review_event (review_id, seq, type, payload, ts) "
            + "SELECT :reviewId, :seq, :type, :payload, :ts FROM DUAL "
            + "WHERE NOT EXISTS (SELECT 1 FROM (SELECT review_id, type FROM review_event) e "
            + "WHERE e.review_id = :reviewId AND e.type IN (:terminalTypes))", nativeQuery = true)
    int insertTerminalIfAbsent(@Param("reviewId") String reviewId,
                               @Param("seq") long seq,
                               @Param("type") String type,
                               @Param("payload") String payload,
                               @Param("ts") String ts,
                               @Param("terminalTypes") Collection<String> terminalTypes);
}
