package com.clauseguard.web;

import com.clauseguard.agent.AgentUnavailableException;
import com.clauseguard.admin.InvalidApprovalStatusException;
import com.clauseguard.api.model.Error;
import com.clauseguard.contract.ContractNotFoundException;
import com.clauseguard.contract.ContractUploadException;
import com.clauseguard.review.ApprovalPayloadInvalidException;
import com.clauseguard.review.ContractChangedException;
import com.clauseguard.review.ReviewNotFoundException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.ErrorResponse;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.multipart.MaxUploadSizeExceededException;

/**
 * 统一异常映射。Python 不可用时返回 503 且可重试；资源不存在返回 404；
 * 审批时合同变更返回 409 CONTRACT_CHANGED（前端拉取最新合同后重试）；
 * 上传内容不合法返回 400（类型/大小/空文件）。
 * 事件流内的失败由 run.failed 事件承载，不走 HTTP 体。
 */
@RestControllerAdvice
public class GlobalExceptionHandler {

    private static final Logger log = LoggerFactory.getLogger(GlobalExceptionHandler.class);

    @ExceptionHandler(AgentUnavailableException.class)
    @ResponseStatus(HttpStatus.SERVICE_UNAVAILABLE)
    public Error handleAgentUnavailable(AgentUnavailableException ex) {
        return new Error().code("AGENT_UNAVAILABLE").message(ex.getMessage());
    }

    @ExceptionHandler(ReviewNotFoundException.class)
    @ResponseStatus(HttpStatus.NOT_FOUND)
    public Error handleReviewNotFound(ReviewNotFoundException ex) {
        return new Error().code("REVIEW_NOT_FOUND").message(ex.getMessage());
    }

    @ExceptionHandler(ContractNotFoundException.class)
    @ResponseStatus(HttpStatus.NOT_FOUND)
    public Error handleContractNotFound(ContractNotFoundException ex) {
        return new Error().code("CONTRACT_NOT_FOUND").message(ex.getMessage());
    }

    @ExceptionHandler(ContractUploadException.class)
    @ResponseStatus(HttpStatus.BAD_REQUEST)
    public Error handleContractUpload(ContractUploadException ex) {
        return new Error().code("CONTRACT_UPLOAD_REJECTED").message(ex.getMessage());
    }

    @ExceptionHandler(MaxUploadSizeExceededException.class)
    @ResponseStatus(HttpStatus.BAD_REQUEST)
    public Error handleUploadTooLarge(MaxUploadSizeExceededException ex) {
        return new Error().code("CONTRACT_UPLOAD_TOO_LARGE").message("上传文件超过大小上限");
    }

    @ExceptionHandler(ContractChangedException.class)
    @ResponseStatus(HttpStatus.CONFLICT)
    public Error handleContractChanged(ContractChangedException ex) {
        return new Error().code("CONTRACT_CHANGED").message(ex.getMessage());
    }

    @ExceptionHandler(InvalidApprovalStatusException.class)
    @ResponseStatus(HttpStatus.BAD_REQUEST)
    public Error handleInvalidApprovalStatus(InvalidApprovalStatusException ex) {
        return new Error().code("INVALID_APPROVAL_STATUS").message(ex.getMessage());
    }

    /**
     * 审批快照不可用 → 422，绝不用 409。
     *
     * <p>409 CONTRACT_CHANGED 的语义是「基线不匹配，前端刷新合同后重试」；这里是
     * 「数据本身不合法，重试无用」。用错码会把排查引向并发方向，而真正的现场是快照损坏。
     *
     * <p>详情（哪个 review、哪条 clause、JSON 第几行坏）只进日志，响应体给固定文案。
     */
    @ExceptionHandler(ApprovalPayloadInvalidException.class)
    @ResponseStatus(HttpStatus.UNPROCESSABLE_ENTITY)
    public Error handleApprovalPayloadInvalid(ApprovalPayloadInvalidException ex) {
        log.error("审批被拒绝（快照不可用）：review={}，{}", ex.getReviewId(), ex.getDetail());
        return new Error().code("APPROVAL_PAYLOAD_INVALID").message(ex.getMessage());
    }

    /**
     * 兜底：任何没有被上面映射到的异常都不许把堆栈/内部路径带进响应体。
     * 详情只进日志（带堆栈），对外只给固定文案 —— 否则一个空指针就能把表名、SQL、
     * 本机绝对路径写进前端拿到的 JSON 里。
     *
     * <p>为什么先判断 ErrorResponse 再兜底：Spring MVC 自己的 404（未知路径）、405（方法不允许）、
     * 415（媒体类型）等都以 ErrorResponse 形式抛出，状态码和标题本身是安全且语义正确的。
     * 一律当 500 处理会把「路径写错」变成「服务挂了」，比泄露堆栈更误导人。
     */
    @ExceptionHandler(Exception.class)
    public ResponseEntity<Error> handleUnexpected(Exception ex) {
        if (ex instanceof ErrorResponse errorResponse) {
            return ResponseEntity.status(errorResponse.getStatusCode())
                    .body(new Error().code("REQUEST_REJECTED").message("请求无法处理"));
        }
        log.error("未处理异常", ex);
        return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
                .body(new Error().code("INTERNAL_ERROR").message("服务内部错误，请稍后重试"));
    }
}
