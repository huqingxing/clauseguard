package com.clauseguard.contract;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;

/**
 * 守门：Java 端源合同键必须与 Python 端 {@code source_object_key} 逐字一致
 * （约定见 contracts/storage-keys.md）。任一侧改了格式，这里或 Python 侧
 * {@code test_object_key_conventions} 会先红，防止「键约定没接通」的 bug 复发。
 */
class ContractStorageKeysTest {

    @Test
    void sourceKeyMatchesPythonConvention() {
        assertThat(ContractStorageKeys.sourceKey("c-1", 3, "docx"))
                .isEqualTo("contracts/c-1/v3/source.docx");
        assertThat(ContractStorageKeys.sourceKey("c-1", 3, "pdf"))
                .isEqualTo("contracts/c-1/v3/source.pdf");
    }

    @Test
    void legacySourceKeyIsOldFlatFormat() {
        assertThat(ContractStorageKeys.legacySourceKey("c-1", 3, "docx"))
                .isEqualTo("c-1-v3.docx");
    }
}
