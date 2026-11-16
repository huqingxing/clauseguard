package com.clauseguard.review;

import com.clauseguard.review.domain.Approval;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.List;

@Repository
public interface ApprovalRepository extends JpaRepository<Approval, Long> {

    /**
     * 按审查取全部审批记录，用于写前幂等判断。
     *
     * <p>为什么不写成 existsByReviewIdAndClauseId：一次审批请求会带上多个 clause，
     * 逐条 exists 就是 N 次查询；这里一次取回该审查的行，在内存里判重即可。
     * 单份合同的待审项是个位数，不需要分页或投影。
     */
    List<Approval> findByReviewId(String reviewId);
}
