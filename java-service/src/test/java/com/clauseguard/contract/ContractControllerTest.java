package com.clauseguard.contract;

import com.clauseguard.api.model.ContractDiff;
import com.clauseguard.contract.domain.Contract;
import com.clauseguard.review.ReviewService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.context.annotation.Import;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.test.web.servlet.MockMvc;

import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.ArgumentMatchers.nullable;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.multipart;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * ContractController web 切片测试：上传 201、列表、详情、原文二进制（含中文文件名
 * Content-Disposition 编码）、diff 透传、404。全局异常映射在切片内一并覆盖。
 */
@WebMvcTest(ContractController.class)
@Import(com.clauseguard.security.SecurityConfig.class)
class ContractControllerTest {

    private static final String CONTRACT_ID = "00000000-0000-0000-0000-0000000000e1";

    @Autowired
    private MockMvc mockMvc;

    @MockBean
    private ContractService contractService;

    @MockBean
    private ReviewService reviewService;

    @Test
    void uploadReturns201WithContractIdAndVersion() throws Exception {
        Contract saved = contract(CONTRACT_ID, 1, "采购合同.pdf");
        when(contractService.upload(any(), nullable(String.class))).thenReturn(saved);

        mockMvc.perform(multipart("/api/v1/contracts")
                        .file(new MockMultipartFile("file", "采购合同.pdf",
                                "application/pdf", "%PDF-1.4 fake".getBytes(StandardCharsets.UTF_8))))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.contract_id").value(CONTRACT_ID))
                .andExpect(jsonPath("$.version").value(1))
                .andExpect(jsonPath("$.filename").value("采购合同.pdf"));
    }

    @Test
    void listReturnsContractSummaries() throws Exception {
        when(contractService.recent(20)).thenReturn(List.of(contract(CONTRACT_ID, 1, "采购合同.pdf")));

        mockMvc.perform(get("/api/v1/contracts"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items[0].contract_id").value(CONTRACT_ID))
                .andExpect(jsonPath("$.items[0].last_review").doesNotExist());
    }

    @Test
    void listWithKeywordDelegatesToSearch() throws Exception {
        when(contractService.search("采购", 20)).thenReturn(List.of());

        mockMvc.perform(get("/api/v1/contracts").param("keyword", "采购"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items").isEmpty());
    }

    @Test
    void detailReturnsContentUrlAndLastReview() throws Exception {
        when(contractService.current(CONTRACT_ID)).thenReturn(contract(CONTRACT_ID, 2, "采购合同.pdf"));

        mockMvc.perform(get("/api/v1/contracts/" + CONTRACT_ID))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.version").value(2))
                .andExpect(jsonPath("$.content_url")
                        .value("/api/v1/contracts/" + CONTRACT_ID + "/content"));
    }

    @Test
    void contentStreamsBytesWithUtf8Filename() throws Exception {
        Contract contract = contract(CONTRACT_ID, 1, "采购合同.pdf");
        when(contractService.current(CONTRACT_ID)).thenReturn(contract);
        when(contractService.findVersion(CONTRACT_ID, 1)).thenReturn(Optional.of(contract));
        when(contractService.loadContent(CONTRACT_ID, null))
                .thenReturn(new org.springframework.core.io.ByteArrayResource(
                        "%PDF-1.4 fake".getBytes(StandardCharsets.UTF_8)));

        mockMvc.perform(get("/api/v1/contracts/" + CONTRACT_ID + "/content"))
                .andExpect(status().isOk())
                .andExpect(header().string("Content-Disposition",
                        "inline; filename*=UTF-8''%E9%87%87%E8%B4%AD%E5%90%88%E5%90%8C.pdf"));
    }

    @Test
    void diffPassesThroughService() throws Exception {
        when(reviewService.contractDiff(eq(CONTRACT_ID), nullable(Integer.class)))
                .thenReturn(new ContractDiff()
                        .contractId(UUID.fromString(CONTRACT_ID))
                        .baselineVersion(1)
                        .targetVersion(2)
                        .changed(List.of("doc#0004"))
                        .added(List.of())
                        .removed(List.of("doc#0021"))
                        .staleFindings(1));

        mockMvc.perform(get("/api/v1/contracts/" + CONTRACT_ID + "/diff"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.baseline_version").value(1))
                .andExpect(jsonPath("$.changed[0]").value("doc#0004"))
                .andExpect(jsonPath("$.removed[0]").value("doc#0021"));
    }

    @Test
    void diffReturns404WhenContractUnknown() throws Exception {
        // diff 的 404 只留给「合同不存在」；版本没审过已经是空账本（200），
        // 这条测试钉住的是前者，免得以后有人再把空账本改回 404。
        when(contractService.current("00000000-0000-0000-0000-0000000000ff"))
                .thenThrow(new ContractNotFoundException("00000000-0000-0000-0000-0000000000ff"));

        mockMvc.perform(get("/api/v1/contracts/00000000-0000-0000-0000-0000000000ff/diff").param("target", "2"))
                .andExpect(status().isNotFound());
    }

    @Test
    void contentReturns404WhenContractUnknown() throws Exception {
        when(contractService.current("00000000-0000-0000-0000-0000000000ff"))
                .thenThrow(new ContractNotFoundException("00000000-0000-0000-0000-0000000000ff"));

        mockMvc.perform(get("/api/v1/contracts/00000000-0000-0000-0000-0000000000ff/content"))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("CONTRACT_NOT_FOUND"));
    }

    @Test
    void uploadRejectsUnsupportedTypeWith400() throws Exception {
        when(contractService.upload(any(), nullable(String.class)))
                .thenThrow(new ContractUploadException("仅支持 PDF/DOCX 文件"));

        mockMvc.perform(multipart("/api/v1/contracts")
                        .file(new MockMultipartFile("file", "evil.exe",
                                "application/octet-stream", new byte[] {1, 2, 3})))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("CONTRACT_UPLOAD_REJECTED"));
    }

    private static Contract contract(String id, int version, String filename) {
        Contract contract = new Contract();
        contract.setId(id);
        contract.setVersion(version);
        contract.setFilename(filename);
        contract.setContentType("application/pdf");
        contract.setSizeBytes(1024);
        contract.setStorageKey(id + "-v" + version + ".pdf");
        contract.setCreatedAt(Instant.parse("2026-10-25T10:00:00Z"));
        return contract;
    }
}
