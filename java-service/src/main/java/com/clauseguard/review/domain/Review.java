package com.clauseguard.review.domain;

import com.clauseguard.review.ReviewStatus;
import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

import java.time.Instant;

/**
 * 审查任务聚合根。唯一由 Java 拥有与持久化的合同/审查元数据，Python 绝不直连此表。
 */
@Entity
@Table(name = "review")
public class Review {

    @Id
    @Column(name = "id", length = 36)
    private String id;

    @Column(name = "contract_id", nullable = false, length = 36)
    private String contractId;

    @Column(name = "version", nullable = false)
    private int version;

    @Column(name = "status", nullable = false, length = 32)
    private String status;

    @Column(name = "run_id", length = 36)
    private String runId;

    /**
     * 审批乐观锁基线：来自 interrupt 事件的 contract_version（合同的版本号）。
     * 与 content_hash 一起用于 approve 时校验合同是否被并发篡改（不匹配即 409）。
     */
    @Column(name = "contract_version", length = 16)
    private String contractVersion;

    /**
     * 审批乐观锁基线：来自 interrupt 事件的 content_hash（合同内容哈希）。
     */
    @Column(name = "content_hash", length = 128)
    private String contentHash;

    /**
     * interrupt 事件的完整 payload JSON（含逐条款待审项 + 基线），供审批回显与基线校验。
     */
    @Column(name = "pending_payload", columnDefinition = "TEXT")
    private String pendingPayload;

    @Column(name = "created_at", nullable = false)
    private Instant createdAt;

    public Review() {
    }

    public String getId() {
        return id;
    }

    public void setId(String id) {
        this.id = id;
    }

    public String getContractId() {
        return contractId;
    }

    public void setContractId(String contractId) {
        this.contractId = contractId;
    }

    public int getVersion() {
        return version;
    }

    public void setVersion(int version) {
        this.version = version;
    }

    public ReviewStatus getStatus() {
        return ReviewStatus.valueOf(status);
    }

    public void setStatus(ReviewStatus status) {
        this.status = status.name();
    }

    public String getRunId() {
        return runId;
    }

    public void setRunId(String runId) {
        this.runId = runId;
    }

    public String getContractVersion() {
        return contractVersion;
    }

    public void setContractVersion(String contractVersion) {
        this.contractVersion = contractVersion;
    }

    public String getContentHash() {
        return contentHash;
    }

    public void setContentHash(String contentHash) {
        this.contentHash = contentHash;
    }

    public String getPendingPayload() {
        return pendingPayload;
    }

    public void setPendingPayload(String pendingPayload) {
        this.pendingPayload = pendingPayload;
    }

    public Instant getCreatedAt() {
        return createdAt;
    }

    public void setCreatedAt(Instant createdAt) {
        this.createdAt = createdAt;
    }
}
