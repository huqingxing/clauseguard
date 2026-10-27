package com.clauseguard.admin;

import com.clauseguard.api.model.ApprovalQueueItem;
import com.clauseguard.api.model.ApprovalQueuePage;
import com.clauseguard.contract.ContractRepository;
import com.clauseguard.contract.domain.Contract;
import com.clauseguard.review.ReviewEventRepository;
import com.clauseguard.review.ReviewRepository;
import com.clauseguard.review.ReviewStatus;
import com.clauseguard.review.domain.Review;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageRequest;
import org.springframework.stereotype.Service;

import java.time.Instant;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

/**
 * 待审批队列（审核者的待办）：只读聚合，复用 review / contract / review_event 三张既有表。
 *
 * <p>为什么不建队列表：待审批是 review 的一个状态，不是独立实体。单独存一张队列表就要为
 * 「谁把它从表里删掉」负责——审批完成、运行失败、合同被删都得同步，任何一处漏了就会出现
 * 队列里有一条点进去却已经批完的幽灵条目。状态查询永远不会与事实源漂移。
 */
@Service
public class ApprovalQueueService {

    private static final Logger logger = LoggerFactory.getLogger(ApprovalQueueService.class);

    /** 外部契约用 WAITING_APPROVAL，内部枚举叫 AWAITING_APPROVAL：两个都认，见 {@link #normalizeStatus}。 */
    private static final String WAITING_APPROVAL = "WAITING_APPROVAL";
    private static final String INTERRUPT = "interrupt";
    private static final int MAX_SIZE = 100;

    private final ReviewRepository reviewRepository;
    private final ReviewEventRepository eventRepository;
    private final ContractRepository contractRepository;
    private final ObjectMapper mapper;

    public ApprovalQueueService(
            ReviewRepository reviewRepository,
            ReviewEventRepository eventRepository,
            ContractRepository contractRepository,
            ObjectMapper mapper) {
        this.reviewRepository = reviewRepository;
        this.eventRepository = eventRepository;
        this.contractRepository = contractRepository;
        this.mapper = mapper;
    }

    public ApprovalQueuePage list(String status, int page, int size) {
        String normalized = normalizeStatus(status);
        int safePage = Math.max(0, page);
        // 上限在服务端也要卡：前端传 size=100000 时不能真去查十万行。
        int safeSize = Math.min(Math.max(1, size), MAX_SIZE);

        Page<Review> reviews = reviewRepository.findByStatusOrderByCreatedAtAsc(
                normalized, PageRequest.of(safePage, safeSize));

        List<Review> rows = reviews.getContent();
        Map<String, Instant> waitingSince = latestWaitingTimes(rows);
        List<ApprovalQueueItem> items = new ArrayList<>(rows.size());
        for (Review review : rows) {
            UUID reviewId = toUuid(review.getId());
            if (reviewId == null) {
                // 主键不是 UUID 的脏数据不进队列：点了也只会 404，还会把整页拖成 500。
                continue;
            }
            items.add(toItem(reviewId, review, waitingSince.get(review.getId())));
        }
        return new ApprovalQueuePage()
                .items(items)
                .page(safePage)
                .size(safeSize)
                .total((int) reviews.getTotalElements());
    }

    /**
     * 状态只有一个合法取值：队列问的是「等我批的」，问别的状态没有业务含义。
     * 未知值直接 400 而不是返回空页——空页会被读成「没有待办」，那是错的结论。
     */
    private static String normalizeStatus(String status) {
        if (status == null || status.isBlank()) {
            return ReviewStatus.AWAITING_APPROVAL.name();
        }
        String upper = status.trim().toUpperCase(Locale.ROOT);
        if ("AWAITING_APPROVAL".equals(upper)) {
            return ReviewStatus.AWAITING_APPROVAL.name();
        }
        if (WAITING_APPROVAL.equals(upper)) {
            return ReviewStatus.AWAITING_APPROVAL.name();
        }
        throw new InvalidApprovalStatusException(status);
    }

    /**
     * 等待时长按「进审批的时间」算：用 review.created_at 会把运行耗时也算成审核者的等待，
     * 审核者看到的是自己的锅。interrupt 事件的 ts 才是进入待审那一刻。
     * 事件缺失（老数据、事件表被清过）时退回 created_at，宁可高估也不显示空白。
     */
    private Map<String, Instant> latestWaitingTimes(List<Review> rows) {
        if (rows.isEmpty()) {
            return Map.of();
        }
        List<String> ids = rows.stream().map(Review::getId).toList();
        Map<String, Instant> result = new LinkedHashMap<>();
        for (Object[] row : eventRepository.findLatestTsByType(ids, INTERRUPT)) {
            String reviewId = (String) row[0];
            // max(ts) 在全是 NULL 时返回 NULL：这种行不进 map，由调用方退回 created_at。
            if (row[1] instanceof Instant ts) {
                result.put(reviewId, ts);
            }
        }
        return result;
    }

    private ApprovalQueueItem toItem(UUID reviewId, Review review, Instant waitingSince) {
        return new ApprovalQueueItem()
                .reviewId(reviewId)
                .contractId(toUuid(review.getContractId()))
                .contractTitle(titleOf(review))
                .riskHighCount(highRiskClauseCount(review.getPendingPayload()))
                .createdAt(review.getCreatedAt().atOffset(ZoneOffset.UTC))
                .waitingSince((waitingSince == null ? review.getCreatedAt() : waitingSince)
                        .atOffset(ZoneOffset.UTC));
    }

    /**
     * 合同表没有独立标题字段，文件名就是人认得出的那个名字。
     * 取审查对应的那个版本（审批的是这一版），版本行不存在时才退回最新版。
     */
    private String titleOf(Review review) {
        return contractRepository.findByIdAndVersion(review.getContractId(), review.getVersion())
                .or(() -> contractRepository.findFirstByIdOrderByVersionDesc(review.getContractId()))
                .map(Contract::getFilename)
                .orElse("（合同已删除）");
    }

    /**
     * 待审的高风险条款数：按 clause_id 去重。
     * 同一条款可能被多个专家各报一次（付款风险 + 违约风险），审核者要拍板的是条款，
     * 用风险点数量会让人以为要批 N 条、打开却只有 N/2 张卡片。
     */
    private int highRiskClauseCount(String pendingPayload) {
        if (pendingPayload == null || pendingPayload.isBlank()) {
            return 0;
        }
        try {
            JsonNode items = mapper.readTree(pendingPayload).path("items");
            if (!items.isArray()) {
                return 0;
            }
            Set<String> clauses = new HashSet<>();
            for (JsonNode item : items) {
                if (!"high".equalsIgnoreCase(item.path("risk").asText(""))) {
                    continue;
                }
                String clauseId = item.path("clause_id").asText("");
                if (!clauseId.isEmpty()) {
                    clauses.add(clauseId);
                }
            }
            return clauses.size();
        } catch (Exception e) {
            // 快照解析失败不该让整个队列 500：这一行显示 0，其余照常可批。
            logger.warn("parse pending_payload failed for queue row: {}", e.getMessage());
            return 0;
        }
    }

    private static UUID toUuid(String value) {
        try {
            return UUID.fromString(value);
        } catch (IllegalArgumentException | NullPointerException e) {
            return null;
        }
    }
}
