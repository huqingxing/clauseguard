package com.clauseguard.review;

import com.clauseguard.review.domain.ReviewEvent;
import org.springframework.data.jpa.repository.JpaRepository;
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
}
