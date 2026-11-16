package com.clauseguard.review.domain;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

import java.time.Instant;

/**
 * 高风险条款人工审批决定。表结构先于审批端点落地（端点属后续阶段）。
 */
@Entity
@Table(name = "approval")
public class Approval {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column(name = "id")
    private Long id;

    @Column(name = "review_id", nullable = false, length = 36)
    private String reviewId;

    @Column(name = "clause_id", nullable = false, length = 64)
    private String clauseId;

    @Column(name = "decision", nullable = false, length = 16)
    private String decision;

    @Column(name = "comment", columnDefinition = "TEXT")
    private String comment;

    @Column(name = "operator", length = 64)
    private String operator;

    /**
     * 审批前措辞（条款原文，来自 interrupt 事件的 clause_text），留痕用。
     */
    @Column(name = "before_wording", columnDefinition = "TEXT")
    private String beforeWording;

    /**
     * 审批后措辞：decision=edit 时为 edited_text；approve/reject 时与 before_wording 一致（未改）。
     */
    @Column(name = "after_wording", columnDefinition = "TEXT")
    private String afterWording;

    /**
     * 该条款的风险档（high 等），来自 interrupt 事件项的 risk。
     */
    @Column(name = "risk", length = 32)
    private String risk;

    /**
     * 这条决定覆盖的风险类型，逗号分隔（如 {@code payment_risk,disclaimer_risk}）。
     *
     * <p>待审项按 clause 聚合后，一条决定可能同时覆盖多个风险维度；只留 risk（档位）
     * 会让审计里看不出「法务到底审了几类问题」。可空：V8 之前的历史记录没有这个信息。
     */
    @Column(name = "risk_types", length = 255)
    private String riskTypes;

    @Column(name = "created_at", nullable = false)
    private Instant createdAt;

    public Approval() {
    }

    public Long getId() {
        return id;
    }

    public void setId(Long id) {
        this.id = id;
    }

    public String getReviewId() {
        return reviewId;
    }

    public void setReviewId(String reviewId) {
        this.reviewId = reviewId;
    }

    public String getClauseId() {
        return clauseId;
    }

    public void setClauseId(String clauseId) {
        this.clauseId = clauseId;
    }

    public String getDecision() {
        return decision;
    }

    public void setDecision(String decision) {
        this.decision = decision;
    }

    public String getComment() {
        return comment;
    }

    public void setComment(String comment) {
        this.comment = comment;
    }

    public String getOperator() {
        return operator;
    }

    public void setOperator(String operator) {
        this.operator = operator;
    }

    public String getBeforeWording() {
        return beforeWording;
    }

    public void setBeforeWording(String beforeWording) {
        this.beforeWording = beforeWording;
    }

    public String getAfterWording() {
        return afterWording;
    }

    public void setAfterWording(String afterWording) {
        this.afterWording = afterWording;
    }

    public String getRisk() {
        return risk;
    }

    public void setRisk(String risk) {
        this.risk = risk;
    }

    public String getRiskTypes() {
        return riskTypes;
    }

    public void setRiskTypes(String riskTypes) {
        this.riskTypes = riskTypes;
    }

    public Instant getCreatedAt() {
        return createdAt;
    }

    public void setCreatedAt(Instant createdAt) {
        this.createdAt = createdAt;
    }
}
