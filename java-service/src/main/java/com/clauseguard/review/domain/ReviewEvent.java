package com.clauseguard.review.domain;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

import java.time.Instant;

/**
 * 事件落库表。payload 为 Python 事件的原始 JSON（严格符合 events.schema.json），
 * 不在 Java 侧复制事件模型，避免字段漂移。
 */
@Entity
@Table(name = "review_event")
public class ReviewEvent {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column(name = "id")
    private Long id;

    @Column(name = "review_id", nullable = false, length = 36)
    private String reviewId;

    @Column(name = "seq", nullable = false)
    private long seq;

    @Column(name = "type", nullable = false, length = 64)
    private String type;

    // MEDIUMTEXT：TEXT 只有 65535 字节，审批续跑后的 run.completed（含全部 findings）
    // 会超限，插入失败而状态已改——事件丢了，前端就永远停在旧状态。
    @Column(name = "payload", nullable = false, columnDefinition = "MEDIUMTEXT")
    private String payload;

    @Column(name = "ts")
    private Instant ts;

    public ReviewEvent() {
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

    public long getSeq() {
        return seq;
    }

    public void setSeq(long seq) {
        this.seq = seq;
    }

    public String getType() {
        return type;
    }

    public void setType(String type) {
        this.type = type;
    }

    public String getPayload() {
        return payload;
    }

    public void setPayload(String payload) {
        this.payload = payload;
    }

    public Instant getTs() {
        return ts;
    }

    public void setTs(Instant ts) {
        this.ts = ts;
    }
}
