package com.clauseguard.contract;

import com.clauseguard.contract.domain.Contract;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.core.io.FileSystemResource;
import org.springframework.core.io.Resource;
import org.springframework.stereotype.Service;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.StandardCopyOption;
import java.time.Instant;
import java.util.List;
import java.util.Locale;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;

/**
 * Stage 12：合同文件的存取与版本管理。
 *
 * 为什么存本地盘而不是直连 MinIO：Java 侧不引 MinIO SDK（版本表外依赖，铁律 5），
 * 演示规模下本地目录足够；存储键与物理路径的映射收敛在本类，Python 与前端都只经
 * REST 通道拿内容（铁律 1）。生产化替换为对象存储时仅改本类实现。
 */
@Service
public class ContractService {

    private static final Logger logger = LoggerFactory.getLogger(ContractService.class);

    /** 只接受 PDF/DOCX：与 Python 解析器 load_document 的能力集严格一致，避免上传即失败。 */
    private static final Set<String> ALLOWED_EXTENSIONS = Set.of("pdf", "docx");

    private final ContractRepository repository;
    private final Path uploadRoot;
    private final long maxSizeBytes;

    public ContractService(
            ContractRepository repository,
            @Value("${clauseguard.contract.upload-dir:./uploads}") String uploadDir,
            @Value("${clauseguard.contract.max-size-bytes:20971520}") long maxSizeBytes)
            throws IOException {
        this.repository = repository;
        this.uploadRoot = Paths.get(uploadDir).toAbsolutePath().normalize();
        this.maxSizeBytes = maxSizeBytes;
        Files.createDirectories(this.uploadRoot);
    }

    /**
     * 上传合同。existingContractId 为空 → 新合同 version=1；非空且已存在 → 新版本
     * （前端「仅重审变更」上传改稿）。返回持久化后的合同行。
     */
    public Contract upload(MultipartFile file, String existingContractId) {
        if (file == null || file.isEmpty()) {
            throw new ContractUploadException("上传文件为空");
        }
        String extension = extensionOf(file.getOriginalFilename());
        if (!ALLOWED_EXTENSIONS.contains(extension)) {
            throw new ContractUploadException(
                    "仅支持 PDF/DOCX 文件，收到: " + (file.getOriginalFilename() == null ? "(无名)" : file.getOriginalFilename()));
        }
        if (file.getSize() > maxSizeBytes) {
            throw new ContractUploadException(
                    String.format(Locale.ROOT, "文件超过大小上限 %d 字节（实际 %d）", maxSizeBytes, file.getSize()));
        }

        String contractId = (existingContractId != null && !existingContractId.isBlank()
                && repository.findFirstByIdOrderByVersionDesc(existingContractId).isPresent())
                ? existingContractId
                : UUID.randomUUID().toString();
        int nextVersion = repository.findFirstByIdOrderByVersionDesc(contractId)
                .map(c -> c.getVersion() + 1)
                .orElse(1);

        Contract contract = new Contract();
        contract.setId(contractId);
        contract.setVersion(nextVersion);
        contract.setFilename(sanitizeFilename(file.getOriginalFilename()));
        contract.setContentType(contentTypeOf(extension));
        contract.setSizeBytes(file.getSize());
        contract.setStorageKey(storageKeyFor(contractId, nextVersion, extension));
        contract.setCreatedAt(Instant.now());
        writeFile(file, contract.getStorageKey());
        Contract saved = repository.save(contract);
        logger.info("合同上传落库: id={} version={} key={} size={}",
                saved.getId(), saved.getVersion(), saved.getStorageKey(), saved.getSizeBytes());
        return saved;
    }

    /** 当前版本；不存在抛 404 语义的业务异常。 */
    public Contract current(String contractId) {
        return repository.findFirstByIdOrderByVersionDesc(contractId)
                .orElseThrow(() -> new ContractNotFoundException(contractId));
    }

    /** 最近上传的合同（含全部历史版本行，按创建时间倒序）。 */
    public List<Contract> recent(int limit) {
        return repository.findAll(org.springframework.data.domain.Sort.by(
                        org.springframework.data.domain.Sort.Direction.DESC, "createdAt"))
                .stream()
                .limit(Math.max(1, limit))
                .toList();
    }

    /** 按文件名模糊过滤（大小写不敏感），同样按创建时间倒序。 */
    public List<Contract> search(String keyword, int limit) {
        return repository.findByFilenameContainingIgnoreCaseOrderByCreatedAtDesc(keyword)
                .stream()
                .limit(Math.max(1, limit))
                .toList();
    }

    public Optional<Contract> findCurrent(String contractId) {
        return repository.findFirstByIdOrderByVersionDesc(contractId);
    }

    public Optional<Contract> findVersion(String contractId, int version) {
        return repository.findByIdAndVersion(contractId, version);
    }

    /**
     * 读取合同版本内容。storage_key 只允许单段文件名（UUID 生成），出现路径分隔符
     * 即视为存储键被污染，直接拒绝——防路径穿越。
     */
    public Resource loadContent(String contractId, Integer version) {
        Contract contract = version == null
                ? current(contractId)
                : repository.findByIdAndVersion(contractId, version)
                        .orElseThrow(() -> new ContractNotFoundException(contractId + " v" + version));
        String key = contract.getStorageKey();
        if (key.contains("/") || key.contains("\\") || key.contains("..")) {
            throw new ContractUploadException("存储键非法: " + key);
        }
        Path path = uploadRoot.resolve(key).normalize();
        if (!path.startsWith(uploadRoot) || !Files.isRegularFile(path)) {
            throw new ContractNotFoundException(contractId + " 的文件不存在");
        }
        return new FileSystemResource(path);
    }

    private void writeFile(MultipartFile file, String storageKey) {
        Path target = uploadRoot.resolve(storageKey).normalize();
        if (!target.startsWith(uploadRoot)) {
            throw new ContractUploadException("存储键非法: " + storageKey);
        }
        try (InputStream in = file.getInputStream()) {
            Files.copy(in, target, StandardCopyOption.REPLACE_EXISTING);
        } catch (IOException ex) {
            throw new ContractUploadException("写入合同文件失败: " + ex.getMessage());
        }
    }

    private static String storageKeyFor(String contractId, int version, String extension) {
        return contractId + "-v" + version + "." + extension;
    }

    private static String extensionOf(String filename) {
        if (filename == null || filename.isBlank()) {
            return "";
        }
        int dot = filename.lastIndexOf('.');
        if (dot < 0 || dot == filename.length() - 1) {
            return "";
        }
        return filename.substring(dot + 1).toLowerCase(Locale.ROOT);
    }

    private static String contentTypeOf(String extension) {
        return "pdf".equals(extension)
                ? "application/pdf"
                : "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
    }

    /** 去掉路径成分与危险字符：文件名只用于展示，不进存储键。 */
    private static String sanitizeFilename(String original) {
        if (original == null || original.isBlank()) {
            return "unnamed";
        }
        String name = Paths.get(original).getFileName().toString();
        return name.replaceAll("[\\p{Cntrl}]", "").trim();
    }
}
