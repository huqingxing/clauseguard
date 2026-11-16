package com.clauseguard.review;

import com.clauseguard.api.model.ApproveRequest;
import com.clauseguard.review.domain.Approval;
import com.clauseguard.review.domain.Review;
import com.fasterxml.jackson.databind.JsonNode;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Instant;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;

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
 *
 * <p>三道防线各管一段，别混为一谈：
 * <ol>
 *   <li>状态 CAS（{@code markStatusIfCurrent}）管「两个请求同时抢同一份待审快照」；</li>
 *   <li>写前判重（{@link ApprovalRepository#findByReviewId}）管「同一 clause 被写两次」；</li>
 *   <li>{@code uk_approval_review_clause} 唯一约束管前两道都漏掉的并发窗口，是最后兜底。</li>
 * </ol>
 * 顺序不能反：约束是兜底不是入口，靠它挡日常路径只会把正常重试变成 500。
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

        // 写前幂等：库里已有该 clause 的审批记录，说明这一条之前就批过了（历史「一条审批一个事务」
        // 时代留下的半成品），再写一次会撞 uk_approval_review_clause 把整单打成失败。
        // 同一个 written 集合顺带挡掉「一次请求里重复带同一个 clause_id」——CAS 拦不住这条，
        // 因为它看的是 review 状态，不看请求体。
        Set<String> written = new HashSet<>();
        for (Approval existing : approvalRepository.findByReviewId(reviewId)) {
            written.add(existing.getClauseId());
        }

        Instant now = Instant.now();
        for (ApproveRequest req : approvals) {
            if (!written.add(req.getClauseId())) {
                // 已经批过：以第一次的结论为准，不覆盖。审批是「一次性决定」，不是可反复改写的配置。
                continue;
            }
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
                // 聚合后一条待审项可能覆盖多个风险维度（payment_risk + disclaimer_risk）：
                // 只存档位会让审计里看不出这条决定审了几类问题。
                approval.setRiskTypes(joinRiskTypes(item.path("risk_types")));
            }
            approval.setAfterWording(req.getEditedText());
            approvalRepository.save(approval);
        }
        // 状态已由抢占语句改成 RUNNING，这里只取 run_id 返回，不再重复 save：
        // 恢复后由 run.completed 事件置 COMPLETED。
        return review.getRunId();
    }

    /**
     * risk_types 数组压成逗号分隔串；缺字段（旧事件）或空数组返回 null，不编造。
     */
    private static String joinRiskTypes(JsonNode node) {
        if (node == null || !node.isArray() || node.isEmpty()) {
            return null;
        }
        StringBuilder sb = new StringBuilder();
        for (JsonNode type : node) {
            String value = type.asText();
            if (value == null || value.isBlank()) {
                continue;
            }
            if (sb.length() > 0) {
                sb.append(',');
            }
            sb.append(value);
        }
        return sb.length() == 0 ? null : sb.toString();
    }

    private static String textOrNull(JsonNode node) {
        if (node == null || node.isNull()) {
            return null;
        }
        return node.asText();
    }
}
