package com.clauseguard.web;

import com.clauseguard.agent.AgentUnavailableException;
import com.clauseguard.api.model.Error;
import com.clauseguard.contract.ContractNotFoundException;
import com.clauseguard.contract.ContractUploadException;
import com.clauseguard.review.ContractChangedException;
import com.clauseguard.review.ReviewNotFoundException;
import org.springframework.http.HttpStatus;
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
}
