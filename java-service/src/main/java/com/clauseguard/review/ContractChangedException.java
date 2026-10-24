package com.clauseguard.review;

/**
 * 审批时合同版本/内容哈希与中断时基线不一致：合同已被并发修改，审批基于过期快照。
 * 映射为 HTTP 409 CONTRACT_CHANGED（前端需拉取最新合同后重试审批）。
 */
public class ContractChangedException extends RuntimeException {

    public ContractChangedException(String reviewId) {
        super("contract_version/content_hash mismatch for review " + reviewId
                + "; contract changed since interrupt, approval rejected");
    }
}
