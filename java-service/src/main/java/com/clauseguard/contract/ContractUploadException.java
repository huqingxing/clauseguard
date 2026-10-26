package com.clauseguard.contract;

/** Stage 12：上传内容不合法（空文件/类型不符/超大/存储键污染）。 */
public class ContractUploadException extends RuntimeException {
    public ContractUploadException(String message) {
        super(message);
    }
}
