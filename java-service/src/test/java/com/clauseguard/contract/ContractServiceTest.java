package com.clauseguard.contract;

import com.clauseguard.contract.domain.Contract;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.junit.jupiter.api.io.TempDir;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.mock.web.MockMultipartFile;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.when;

/**
 * ContractService 单元测试：上传校验（类型/空/超大）、版本递增、存储键与磁盘一致性、
 * 路径穿越防护、内容加载。不启动容器，仓库为 mock，存储用 @TempDir 真实落盘。
 */
@ExtendWith(MockitoExtension.class)
class ContractServiceTest {

    @TempDir
    Path tempDir;

    @Mock
    private ContractRepository repository;

    private ContractService service;

    @BeforeEach
    void setup() throws IOException {
        service = new ContractService(repository, tempDir.toString(), 1024);
    }

    @Test
    void uploadCreatesVersionOneAndWritesFileToDisk() throws IOException {
        when(repository.findFirstByIdOrderByVersionDesc(any())).thenReturn(Optional.empty());
        when(repository.save(any(Contract.class))).thenAnswer(i -> i.getArgument(0));

        Contract saved = service.upload(pdfFile("采购合同.pdf", 100), null);

        assertThat(saved.getVersion()).isEqualTo(1);
        assertThat(saved.getFilename()).isEqualTo("采购合同.pdf");
        assertThat(saved.getStorageKey()).isEqualTo(saved.getId() + "-v1.pdf");
        // 文件真实落盘且字节一致（不是「看起来能跑」的假实现）。
        Path onDisk = tempDir.resolve(saved.getStorageKey());
        assertThat(Files.exists(onDisk)).isTrue();
        assertThat(Files.size(onDisk)).isEqualTo(100);
    }

    @Test
    void uploadWithExistingContractIdIncrementsVersion() {
        Contract existing = new Contract();
        existing.setId("c-1");
        existing.setVersion(1);
        existing.setFilename("采购合同.pdf");
        existing.setContentType("application/pdf");
        existing.setSizeBytes(10);
        existing.setStorageKey("c-1-v1.pdf");
        existing.setCreatedAt(Instant.now());
        when(repository.findFirstByIdOrderByVersionDesc("c-1")).thenReturn(Optional.of(existing));
        when(repository.save(any(Contract.class))).thenAnswer(i -> i.getArgument(0));

        Contract saved = service.upload(pdfFile("采购合同-改稿.pdf", 50), "c-1");

        // 改稿重审：同 id 新版本，旧版本行不删（增量 diff 的基线还在）。
        assertThat(saved.getId()).isEqualTo("c-1");
        assertThat(saved.getVersion()).isEqualTo(2);
        assertThat(saved.getStorageKey()).isEqualTo("c-1-v2.pdf");
    }

    @Test
    void uploadRejectsUnsupportedExtension() {
        MockMultipartFile exe = new MockMultipartFile(
                "file", "evil.exe", "application/octet-stream", new byte[] {1, 2, 3});
        assertThatThrownBy(() -> service.upload(exe, null))
                .isInstanceOf(ContractUploadException.class)
                .hasMessageContaining("仅支持 PDF/DOCX");
    }

    @Test
    void uploadRejectsEmptyFile() {
        MockMultipartFile empty = new MockMultipartFile(
                "file", "empty.pdf", "application/pdf", new byte[0]);
        assertThatThrownBy(() -> service.upload(empty, null))
                .isInstanceOf(ContractUploadException.class);
    }

    @Test
    void uploadRejectsOversizeFile() {
        // 服务实例上限 1024 字节；构造 2048 字节文件。
        MockMultipartFile big = new MockMultipartFile(
                "file", "big.pdf", "application/pdf", new byte[2048]);
        assertThatThrownBy(() -> service.upload(big, null))
                .isInstanceOf(ContractUploadException.class)
                .hasMessageContaining("大小上限");
    }

    @Test
    void loadContentRejectsTraversalStorageKey() {
        Contract poisoned = new Contract();
        poisoned.setId("c-evil");
        poisoned.setVersion(1);
        poisoned.setStorageKey("../../../etc/passwd");
        when(repository.findByIdAndVersion("c-evil", 1)).thenReturn(Optional.of(poisoned));

        assertThatThrownBy(() -> service.loadContent("c-evil", 1))
                .isInstanceOf(ContractUploadException.class)
                .hasMessageContaining("存储键非法");
    }

    @Test
    void loadContentThrowsWhenFileMissing() {
        Contract ghost = new Contract();
        ghost.setId("c-ghost");
        ghost.setVersion(1);
        ghost.setStorageKey("c-ghost-v1.pdf"); // 从未落盘
        when(repository.findByIdAndVersion("c-ghost", 1)).thenReturn(Optional.of(ghost));

        assertThatThrownBy(() -> service.loadContent("c-ghost", 1))
                .isInstanceOf(ContractNotFoundException.class);
    }

    @Test
    void searchFiltersByFilenameIgnoringCase() {
        Contract a = contract("c-a", 1, "采购合同.pdf");
        Contract b = contract("c-b", 1, "服务协议.docx");
        when(repository.findByFilenameContainingIgnoreCaseOrderByCreatedAtDesc("采购"))
                .thenReturn(List.of(a, b));

        List<Contract> found = service.search("采购", 10);
        assertThat(found).containsExactly(a, b);
    }

    @Test
    void currentThrowsWhenContractUnknown() {
        when(repository.findFirstByIdOrderByVersionDesc("nope")).thenReturn(Optional.empty());
        assertThatThrownBy(() -> service.current("nope"))
                .isInstanceOf(ContractNotFoundException.class);
    }

    @Test
    void recentLimitsResultSize() {
        when(repository.findAll(any(org.springframework.data.domain.Sort.class)))
                .thenReturn(List.of(contract("c-1", 1, "a.pdf"), contract("c-2", 1, "b.pdf")));
        assertThat(service.recent(1)).hasSize(1);
    }

    private static Contract contract(String id, int version, String filename) {
        Contract contract = new Contract();
        contract.setId(id);
        contract.setVersion(version);
        contract.setFilename(filename);
        contract.setContentType("application/pdf");
        contract.setSizeBytes(10);
        contract.setStorageKey(id + "-v" + version + ".pdf");
        contract.setCreatedAt(Instant.now());
        return contract;
    }

    private static MockMultipartFile pdfFile(String filename, int size) {
        return new MockMultipartFile("file", filename, "application/pdf", new byte[size]);
    }
}
