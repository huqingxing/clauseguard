package com.clauseguard.contract.domain;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.IdClass;
import jakarta.persistence.Table;

import java.io.Serializable;
import java.time.Instant;

/**
 * Stage 12：合同的一个版本。复合主键 (id, version)——同 id 多版本共存，
 * 增量重审按版本定位基线，历史版本不删（红线：removed 条款的历史结论要可追溯）。
 */
@Entity
@Table(name = "contract")
@IdClass(Contract.ContractId.class)
public class Contract {

    @Id
    @Column(name = "id", length = 36)
    private String id;

    @Id
    @Column(name = "version", nullable = false)
    private int version;

    @Column(name = "filename", nullable = false, length = 255)
    private String filename;

    @Column(name = "content_type", nullable = false, length = 128)
    private String contentType;

    @Column(name = "size_bytes", nullable = false)
    private long sizeBytes;

    /**
     * Java 侧存储中的相对键；磁盘绝对路径只由 ContractService 依据根目录拼装，
     * 实体不持有路径，杜绝调用方自行拼接导致穿越。
     */
    @Column(name = "storage_key", nullable = false, length = 512)
    private String storageKey;

    @Column(name = "created_at", nullable = false)
    private Instant createdAt;

    public Contract() {
    }

    public String getId() {
        return id;
    }

    public void setId(String id) {
        this.id = id;
    }

    public int getVersion() {
        return version;
    }

    public void setVersion(int version) {
        this.version = version;
    }

    public String getFilename() {
        return filename;
    }

    public void setFilename(String filename) {
        this.filename = filename;
    }

    public String getContentType() {
        return contentType;
    }

    public void setContentType(String contentType) {
        this.contentType = contentType;
    }

    public long getSizeBytes() {
        return sizeBytes;
    }

    public void setSizeBytes(long sizeBytes) {
        this.sizeBytes = sizeBytes;
    }

    public String getStorageKey() {
        return storageKey;
    }

    public void setStorageKey(String storageKey) {
        this.storageKey = storageKey;
    }

    public Instant getCreatedAt() {
        return createdAt;
    }

    public void setCreatedAt(Instant createdAt) {
        this.createdAt = createdAt;
    }

    /**
     * 复合主键类：JPA @IdClass 要求 public 无参构造 + equals/hashCode。
     */
    public static class ContractId implements Serializable {
        private String id;
        private int version;

        public ContractId() {
        }

        public ContractId(String id, int version) {
            this.id = id;
            this.version = version;
        }

        @Override
        public boolean equals(Object other) {
            if (this == other) {
                return true;
            }
            if (!(other instanceof ContractId that)) {
                return false;
            }
            return version == that.version && java.util.Objects.equals(id, that.id);
        }

        @Override
        public int hashCode() {
            return java.util.Objects.hash(id, version);
        }
    }
}
