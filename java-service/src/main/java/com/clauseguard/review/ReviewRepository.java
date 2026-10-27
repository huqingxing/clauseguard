package com.clauseguard.review;

import com.clauseguard.review.domain.Review;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

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
}
