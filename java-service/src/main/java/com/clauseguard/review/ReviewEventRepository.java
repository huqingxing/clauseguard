package com.clauseguard.review;

import com.clauseguard.review.domain.ReviewEvent;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.List;

@Repository
public interface ReviewEventRepository extends JpaRepository<ReviewEvent, Long> {

    List<ReviewEvent> findByReviewIdAndTypeOrderBySeqAsc(String reviewId, String type);

    /** Stage 12 评测看板：聚合所有 run.completed（演示规模全量加载，见报告遗留风险）。 */
    List<ReviewEvent> findByTypeOrderBySeqAsc(String type);
}
