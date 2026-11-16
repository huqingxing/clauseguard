package com.clauseguard.review;

import com.clauseguard.api.model.ApproveRequest;
import com.clauseguard.review.domain.Approval;
import com.clauseguard.review.domain.Review;
import com.fasterxml.jackson.databind.JsonNode;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.Objects;

/**
 * 审批落库的唯一事务入口：抢占状态 → 乐观锁校验 → 写审批记录。
 *
 * <p>为什么必须是独立 Bean：{@code approveReview} 在本类之外的同一个 Spring 上下文里
 * 由 ReviewService 内部调用，若把 {@code @Transactional} 直接加在 ReviewService 的方法上，
 * 自调用不经过代理，注解会静默失效（与 ReviewEventWriter 同一个坑）。
 *
 * <p>为什么不能把 resume 调用也包进来：{@code agentClient.streamResume} 是异步订阅，
 * 事务里发起会出现「事务未提交、事件已回调」；而且 SSE 可能持续几十秒，长期占用数据库连接。
 * 事务边界只罩数据库写入，HTTP/SSE 一律在外面。
 *
 * <p>修复前的现场：每条 approval 各自一次提交，19 条就是 19 个事务。第 10 条失败时
 * 前 9 条已落库、review 仍是 AWAITING_APPROVAL；两个请求并发时又都能通过校验并双写。
 */
@Service
public class ApprovalApplier {

    private final ReviewRepository reviewRepository;
    private final ApprovalRepository approvalRepository;

    public ApprovalApplier(ReviewRepository reviewRepository,
                           ApprovalRepository approvalRepository) {
        this.reviewRepository = reviewRepository;
        this.approvalRepository = approvalRepository;
    }

    /**
     * @param pendingByClause 中断快照里按 clause_id 索引的待审项，用于留痕前措辞与风险档
     * @return 该审查的 run_id（可能为 null），供调用方在事务外发起 resume
     */
    @Transactional
    public String apply(String reviewId,
                        List<ApproveRequest> approvals,
                        String operator,
                        Map<String, JsonNode> pendingByClause) {
        // 先抢占状态：并发下只有一个事务能拿到 1，另一个直接按冲突拒绝，
        // 不会出现两边都通过校验、都写 approval 的情况。
        int claimed = reviewRepository.markStatusIfCurrent(
                reviewId, ReviewStatus.AWAITING_APPROVAL.name(), ReviewStatus.RUNNING.name());
        if (claimed == 0) {
            throw new ContractChangedException(reviewId);
        }
        // 抢占语句开了 clearAutomatically，这里拿到的是库里的最新状态。
        Review review = reviewRepository.findById(reviewId)
                .orElseThrow(() -> new ReviewNotFoundException(reviewId));

        if (approvals == null || approvals.isEmpty()) {
            throw new ContractChangedException(reviewId);
        }
        // 乐观锁：逐条比对基线；任一不匹配即整体抛异常，事务回滚后状态回到 AWAITING_APPROVAL。
        for (ApproveRequest req : approvals) {
            if (!Objects.equals(req.getContractVersion(), review.getContractVersion())
                    || !Objects.equals(req.getContentHash(), review.getContentHash())) {
                throw new ContractChangedException(reviewId);
            }
        }

        Instant now = Instant.now();
        for (ApproveRequest req : approvals) {
            Approval approval = new Approval();
            approval.setReviewId(reviewId);
            approval.setClauseId(req.getClauseId());
            approval.setDecision(req.getDecision() == null ? null : req.getDecision().getValue());
            approval.setComment(req.getComment());
            approval.setOperator(operator);
            approval.setCreatedAt(now);
            JsonNode item = pendingByClause == null ? null : pendingByClause.get(req.getClauseId());
            if (item != null) {
                approval.setBeforeWording(textOrNull(item.path("clause_text")));
                approval.setRisk(textOrNull(item.path("risk")));
            }
            approval.setAfterWording(req.getEditedText());
            approvalRepository.save(approval);
        }
        // 状态已由抢占语句改成 RUNNING，这里只取 run_id 返回，不再重复 save：
        // 恢复后由 run.completed 事件置 COMPLETED。
        return review.getRunId();
    }

    private static String textOrNull(JsonNode node) {
        if (node == null || node.isNull()) {
            return null;
        }
        return node.asText();
    }
}
