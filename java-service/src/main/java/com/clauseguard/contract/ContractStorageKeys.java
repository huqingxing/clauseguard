package com.clauseguard.contract;

/**
 * 源合同对象键生成（单一事实来源，跨服务约定见 contracts/storage-keys.md）。
 *
 * 修复「批注稿链路源对象键没接通」：Java 上传写入与 Python render 读取必须共用同一套键。
 * 此处为 Java 端实现，必须与 Python 端 {@code app.clients.minio_client.source_object_key}
 * 逐字一致——由 {@code ContractStorageKeysTest} 与 Python 侧
 * {@code test_object_key_conventions} 双向守门。
 *
 * <p>为什么集中到这一类而不是散落在 {@code ContractService}：避免「Java 写一种键、Python 读
 * 另一种键」再次漂移（本次 bug 的根因）。新增/修改键格式只改这一处 + Python 一处 + 两测试。
 */
public final class ContractStorageKeys {

    private ContractStorageKeys() {
    }

    /** 源合同对象键：{@code contracts/{contractId}/v{version}/source.{ext}}。 */
    public static String sourceKey(String contractId, int version, String ext) {
        return "contracts/" + contractId + "/v" + version + "/source." + ext;
    }

    /** 历史遗留键（修复前 Java 落点）：{@code {contractId}-v{version}.{ext}}，仅作只读回退。 */
    public static String legacySourceKey(String contractId, int version, String ext) {
        return contractId + "-v" + version + "." + ext;
    }
}
