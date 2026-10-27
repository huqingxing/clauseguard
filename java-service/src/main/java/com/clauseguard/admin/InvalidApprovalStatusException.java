package com.clauseguard.admin;

/**
 * 待审批队列收到不支持的 status。
 *
 * <p>单独一个异常类型是为了让 400 也带 {code,message}：直接抛 IllegalArgumentException
 * 会被 Spring 兜成 500 的默认错误页，前端就拿不到统一的错误体了。
 */
public class InvalidApprovalStatusException extends RuntimeException {

    public InvalidApprovalStatusException(String status) {
        super("不支持的审批状态：" + status + "（仅支持 WAITING_APPROVAL）");
    }
}
