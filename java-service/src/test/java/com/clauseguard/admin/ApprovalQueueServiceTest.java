package com.clauseguard.admin;

import com.clauseguard.api.model.ApprovalQueueItem;
import com.clauseguard.api.model.ApprovalQueuePage;
import com.clauseguard.contract.ContractRepository;
import com.clauseguard.contract.domain.Contract;
import com.clauseguard.review.ReviewEventRepository;
import com.clauseguard.review.ReviewRepository;
import com.clauseguard.review.domain.Review;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.data.domain.PageImpl;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Pageable;

import java.time.Instant;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyCollection;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 待审批队列的整形逻辑：等待时长取哪一列、高风险条数怎么数、状态参数怎么收。
 *
 * <p>不启动容器：这里没有 SQL 语义要验证（分页交给 repository），要验的是
 * 「事件时间戳缺失时退回什么」「同一条款的两次上报算一条还是两条」这些判断。
 */
@ExtendWith(MockitoExtension.class)
class ApprovalQueueServiceTest {

    private static final String REVIEW_ID = "11111111-1111-4111-8111-111111111111";
    private static final String CONTRACT_ID = "22222222-2222-4222-8222-222222222222";
    private static final Instant CREATED = Instant.parse("2026-10-27T06:00:00Z");
    private static final Instant INTERRUPTED = Instant.parse("2026-10-27T06:05:00Z");

    @Mock
    private ReviewRepository reviewRepository;

    @Mock
    private ReviewEventRepository eventRepository;

    @Mock
    private ContractRepository contractRepository;

    private ApprovalQueueService service;

    @BeforeEach
    void setUp() {
        service = new ApprovalQueueService(
                reviewRepository, eventRepository, contractRepository, new ObjectMapper());
    }

    private static Review review(String id, String pendingPayload) {
        Review review = new Review();
        review.setId(id);
        review.setContractId(CONTRACT_ID);
        review.setVersion(1);
        review.setStatus(com.clauseguard.review.ReviewStatus.AWAITING_APPROVAL);
        review.setCreatedAt(CREATED);
        review.setPendingPayload(pendingPayload);
        return review;
    }

    private static Contract contract(String filename) {
        Contract contract = new Contract();
        contract.setId(CONTRACT_ID);
        contract.setVersion(1);
        contract.setFilename(filename);
        return contract;
    }

    @Test
    void waitingSinceComesFromInterruptEventNotReviewCreation() {
        when(reviewRepository.findByStatusOrderByCreatedAtAsc(eq("AWAITING_APPROVAL"), any(Pageable.class)))
                .thenReturn(new PageImpl<>(List.of(review(REVIEW_ID, null)), PageRequest.of(0, 20), 1));
        when(contractRepository.findByIdAndVersion(CONTRACT_ID, 1))
                .thenReturn(Optional.of(contract("研发外包合同.docx")));
        when(eventRepository.findLatestTsByType(anyCollection(), eq("interrupt")))
                .thenReturn(List.<Object[]>of(new Object[] {REVIEW_ID, INTERRUPTED}));

        ApprovalQueuePage page = service.list(null, 0, 20);

        ApprovalQueueItem item = page.getItems().get(0);
        assertThat(item.getWaitingSince().toInstant()).isEqualTo(INTERRUPTED);
        assertThat(item.getCreatedAt().toInstant()).isEqualTo(CREATED);
        assertThat(item.getContractTitle()).isEqualTo("研发外包合同.docx");
        assertThat(page.getTotal()).isEqualTo(1);
    }

    @Test
    void waitingSinceFallsBackToCreatedAtWhenNoInterruptEvent() {
        when(reviewRepository.findByStatusOrderByCreatedAtAsc(anyString(), any(Pageable.class)))
                .thenReturn(new PageImpl<>(List.of(review(REVIEW_ID, null))));
        when(contractRepository.findByIdAndVersion(CONTRACT_ID, 1)).thenReturn(Optional.of(contract("a.docx")));
        when(eventRepository.findLatestTsByType(anyCollection(), anyString())).thenReturn(List.of());

        ApprovalQueueItem item = service.list(null, 0, 20).getItems().get(0);

        // 事件表被清过时宁可高估等待时长，也不能给一个空时间让前端显示「-」
        assertThat(item.getWaitingSince().toInstant()).isEqualTo(CREATED);
    }

    @Test
    void highRiskCountCountsDistinctClausesNotRiskPoints() {
        String payload = """
                {"items":[
                  {"clause_id":"c-1","risk_type":"payment_risk","risk":"high"},
                  {"clause_id":"c-1","risk_type":"breach_risk","risk":"high"},
                  {"clause_id":"c-2","risk_type":"payment_risk","risk":"high"},
                  {"clause_id":"c-3","risk_type":"ip_risk","risk":"medium"}
                ]}""";
        when(reviewRepository.findByStatusOrderByCreatedAtAsc(anyString(), any(Pageable.class)))
                .thenReturn(new PageImpl<>(List.of(review(REVIEW_ID, payload))));
        when(contractRepository.findByIdAndVersion(CONTRACT_ID, 1)).thenReturn(Optional.of(contract("a.docx")));
        when(eventRepository.findLatestTsByType(anyCollection(), anyString())).thenReturn(List.of());

        ApprovalQueueItem item = service.list(null, 0, 20).getItems().get(0);

        // 4 个风险点只对应 2 条要拍板的条款：审核者打开详情看到的也是 2 张卡片
        assertThat(item.getRiskHighCount()).isEqualTo(2);
    }

    @Test
    void unparsablePayloadDoesNotBreakTheWholeQueue() {
        when(reviewRepository.findByStatusOrderByCreatedAtAsc(anyString(), any(Pageable.class)))
                .thenReturn(new PageImpl<>(List.of(review(REVIEW_ID, "{not json"))));
        when(contractRepository.findByIdAndVersion(CONTRACT_ID, 1)).thenReturn(Optional.of(contract("a.docx")));
        when(eventRepository.findLatestTsByType(anyCollection(), anyString())).thenReturn(List.of());

        assertThat(service.list(null, 0, 20).getItems().get(0).getRiskHighCount()).isZero();
    }

    @Test
    void acceptsBothWaitingAndAwaitingSpelling() {
        when(reviewRepository.findByStatusOrderByCreatedAtAsc(anyString(), any(Pageable.class)))
                .thenReturn(new PageImpl<>(List.of()));

        service.list("WAITING_APPROVAL", 0, 20);
        service.list("awaiting_approval", 0, 20);
        service.list(null, 0, 20);

        verify(reviewRepository, org.mockito.Mockito.times(3))
                .findByStatusOrderByCreatedAtAsc(eq("AWAITING_APPROVAL"), any(Pageable.class));
    }

    @Test
    void rejectsUnknownStatusInsteadOfReturningAnEmptyPage() {
        assertThatThrownBy(() -> service.list("COMPLETED", 0, 20))
                .isInstanceOf(InvalidApprovalStatusException.class)
                .hasMessageContaining("COMPLETED");
    }

    @Test
    void clampsPageAndSizeToSaneBounds() {
        when(reviewRepository.findByStatusOrderByCreatedAtAsc(anyString(), any(Pageable.class)))
                .thenReturn(new PageImpl<>(List.of()));
        ArgumentCaptor<Pageable> captor = ArgumentCaptor.forClass(Pageable.class);

        service.list(null, -3, 5000);

        verify(reviewRepository).findByStatusOrderByCreatedAtAsc(anyString(), captor.capture());
        assertThat(captor.getValue().getPageNumber()).isZero();
        assertThat(captor.getValue().getPageSize()).isEqualTo(100);
    }

    @Test
    void skipsRowsWhoseIdIsNotAUuid() {
        when(reviewRepository.findByStatusOrderByCreatedAtAsc(anyString(), any(Pageable.class)))
                .thenReturn(new PageImpl<>(List.of(review("not-a-uuid", null))));
        when(eventRepository.findLatestTsByType(anyCollection(), anyString())).thenReturn(List.of());

        assertThat(service.list(null, 0, 20).getItems()).isEmpty();
    }

    @Test
    void fallsBackToLatestContractVersionWhenTheVersionRowIsGone() {
        when(reviewRepository.findByStatusOrderByCreatedAtAsc(anyString(), any(Pageable.class)))
                .thenReturn(new PageImpl<>(List.of(review(REVIEW_ID, null))));
        when(contractRepository.findByIdAndVersion(CONTRACT_ID, 1)).thenReturn(Optional.empty());
        when(contractRepository.findFirstByIdOrderByVersionDesc(CONTRACT_ID))
                .thenReturn(Optional.of(contract("最新版.docx")));
        when(eventRepository.findLatestTsByType(anyCollection(), anyString())).thenReturn(List.of());

        assertThat(service.list(null, 0, 20).getItems().get(0).getContractTitle()).isEqualTo("最新版.docx");
    }

    @Test
    void reportsMissingContractWithoutThrowing() {
        when(reviewRepository.findByStatusOrderByCreatedAtAsc(anyString(), any(Pageable.class)))
                .thenReturn(new PageImpl<>(List.of(review(REVIEW_ID, null))));
        when(contractRepository.findByIdAndVersion(CONTRACT_ID, 1)).thenReturn(Optional.empty());
        when(contractRepository.findFirstByIdOrderByVersionDesc(CONTRACT_ID)).thenReturn(Optional.empty());
        when(eventRepository.findLatestTsByType(anyCollection(), anyString())).thenReturn(List.of());

        ApprovalQueueItem item = service.list(null, 0, 20).getItems().get(0);

        assertThat(item.getContractTitle()).isEqualTo("（合同已删除）");
        assertThat(item.getReviewId()).isEqualTo(UUID.fromString(REVIEW_ID));
    }
}
