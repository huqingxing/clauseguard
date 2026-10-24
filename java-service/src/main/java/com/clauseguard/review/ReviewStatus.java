package com.clauseguard.review;

/**
 * 审查任务状态机。与 openapi.yaml 的 ReviewResult.status 枚举一致。
 */
public enum ReviewStatus {
    QUEUED,
    RUNNING,
    AWAITING_APPROVAL,
    COMPLETED,
    FAILED
}
