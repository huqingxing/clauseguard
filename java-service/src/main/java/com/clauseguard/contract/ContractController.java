package com.clauseguard.contract;

import com.clauseguard.api.model.ContractDetail;
import com.clauseguard.api.model.ContractDetailLastReview;
import com.clauseguard.api.model.ContractDiff;
import com.clauseguard.api.model.ContractSummary;
import com.clauseguard.api.model.ContractSummaryLastReview;
import com.clauseguard.api.model.ContractUploadResponse;
import com.clauseguard.api.model.ListContracts200Response;
import com.clauseguard.contract.domain.Contract;
import com.clauseguard.review.ReviewRepository;
import com.clauseguard.review.ReviewService;
import com.clauseguard.review.domain.Review;
import org.springframework.core.io.Resource;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;

import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Locale;
import java.util.UUID;

/**
 * Stage 12：合同上传与版本内容入口。
 *
 * 为什么上传走 Java 而不是前端直传对象存储：前端只认 Java（禁止直连 Python/存储），
 * Java 也不引 MinIO SDK（版本表外依赖）——先落 Java 侧存储，Python ingest 与工作台
 * 原文渲染都经 /content 端点取文件（铁律 1 的 REST 通道）。
 * 本类只做受理与整形，业务规则（校验/版本/存储键）全在 ContractService。
 */
@RestController
@RequestMapping("/api/v1/contracts")
public class ContractController {

    private final ContractService contractService;
    private final ReviewRepository reviewRepository;
    private final ReviewService reviewService;

    public ContractController(
            ContractService contractService,
            ReviewRepository reviewRepository,
            ReviewService reviewService) {
        this.contractService = contractService;
        this.reviewRepository = reviewRepository;
        this.reviewService = reviewService;
    }

    /**
     * 上传合同。contract_id 表单项可选：传入且已存在 → 新版本（改稿重审）；
     * 缺省 → 新合同 version=1。
     */
    @PostMapping(consumes = MediaType.MULTIPART_FORM_DATA_VALUE)
    public ResponseEntity<ContractUploadResponse> upload(
            @RequestParam("file") MultipartFile file,
            @RequestParam(value = "contract_id", required = false) String contractId) {
        Contract saved = contractService.upload(file, contractId);
        return ResponseEntity.status(201).body(new ContractUploadResponse()
                .contractId(UUID.fromString(saved.getId()))
                .version(saved.getVersion())
                .filename(saved.getFilename())
                .contentType(saved.getContentType())
                .sizeBytes((int) Math.min(saved.getSizeBytes(), Integer.MAX_VALUE)));
    }

    /** 合同列表：最近创建在前，可按文件名过滤；附最近一次审查状态（无则 null）。 */
    @GetMapping
    public ListContracts200Response list(
            @RequestParam(value = "keyword", required = false) String keyword,
            @RequestParam(value = "limit", required = false, defaultValue = "20") int limit) {
        List<Contract> contracts = keyword == null || keyword.isBlank()
                ? contractService.recent(limit)
                : contractService.search(keyword, limit);
        List<ContractSummary> items = contracts.stream()
                .map(this::toSummary)
                .toList();
        return new ListContracts200Response().items(items);
    }

    @GetMapping("/{id}")
    public ResponseEntity<ContractDetail> detail(@PathVariable("id") String id) {
        Contract contract = contractService.current(id);
        ContractDetail detail = new ContractDetail()
                .contractId(UUID.fromString(contract.getId()))
                .filename(contract.getFilename())
                .version(contract.getVersion())
                .contentType(contract.getContentType())
                .sizeBytes((int) Math.min(contract.getSizeBytes(), Integer.MAX_VALUE))
                .createdAt(contract.getCreatedAt().atOffset(ZoneOffset.UTC))
                .contentUrl("/api/v1/contracts/" + contract.getId() + "/content");
        reviewRepository.findFirstByContractIdOrderByCreatedAtDesc(id)
                .ifPresent(review -> detail.setLastReview(toDetailLastReview(review)));
        return ResponseEntity.ok(detail);
    }

    /**
     * 合同原文二进制：工作台左栏（pdf.js/docx-preview）与 Python ingest 的数据源。
     * 版本缺省返回当前版本；Content-Disposition 带 RFC 5987 编码，中文文件名不乱码。
     */
    @GetMapping("/{id}/content")
    public ResponseEntity<Resource> content(
            @PathVariable("id") String id,
            @RequestParam(value = "version", required = false) Integer version) {
        Contract contract = version == null
                ? contractService.current(id)
                : contractService.findVersion(id, version)
                        .orElseThrow(() -> new ContractNotFoundException(id + " v" + version));
        Resource resource = contractService.loadContent(id, version);
        String encoded = URLEncoder.encode(contract.getFilename(), StandardCharsets.UTF_8)
                .replace("+", "%20");
        return ResponseEntity.ok()
                .contentType(MediaType.parseMediaType(contract.getContentType()))
                .header(HttpHeaders.CONTENT_DISPOSITION,
                        "inline; filename*=UTF-8''" + encoded)
                .body(resource);
    }

    /**
     * 版本 diff：数据来自该合同最近一次审查的 incremental 账本（Python 单一事实源），
     * 本端点只透传整形，不重新计算条款差异。
     */
    @GetMapping("/{id}/diff")
    public ContractDiff diff(
            @PathVariable("id") String id,
            @RequestParam(value = "target", required = false) Integer targetVersion) {
        return reviewService.contractDiff(id, targetVersion);
    }

    private ContractSummary toSummary(Contract contract) {
        ContractSummary summary = new ContractSummary()
                .contractId(UUID.fromString(contract.getId()))
                .filename(contract.getFilename())
                .version(contract.getVersion())
                .createdAt(contract.getCreatedAt().atOffset(ZoneOffset.UTC));
        reviewRepository.findFirstByContractIdOrderByCreatedAtDesc(contract.getId())
                .ifPresent(review -> summary.setLastReview(new ContractSummaryLastReview()
                        .reviewId(UUID.fromString(review.getId()))
                        .status(ContractSummaryLastReview.StatusEnum.fromValue(
                                review.getStatus().name().toLowerCase(Locale.ROOT)))));
        return summary;
    }

    private static ContractDetailLastReview toDetailLastReview(Review review) {
        return new ContractDetailLastReview()
                .reviewId(UUID.fromString(review.getId()))
                .status(ContractDetailLastReview.StatusEnum.fromValue(
                        review.getStatus().name().toLowerCase(Locale.ROOT)));
    }
}
