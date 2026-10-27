package com.clauseguard.admin;

import com.clauseguard.api.model.AdminHealth;
import com.clauseguard.api.model.ApprovalQueuePage;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * Stage 12：管理页健康聚合入口。业务在 AdminHealthService。
 *
 * <p>Stage 5：新增待审批队列。挂在 /api/v1/admin 下就自动继承阶段 2 的 ADMIN 鉴权，
 * 不用为它另写一条过滤规则——少一处「记得加权限」，就少一处裸奔。
 */
@RestController
@RequestMapping("/api/v1/admin")
public class AdminController {

    private final AdminHealthService adminHealthService;
    private final ApprovalQueueService approvalQueueService;

    public AdminController(AdminHealthService adminHealthService, ApprovalQueueService approvalQueueService) {
        this.adminHealthService = adminHealthService;
        this.approvalQueueService = approvalQueueService;
    }

    @GetMapping("/health")
    public AdminHealth health() {
        return adminHealthService.health();
    }

    @GetMapping("/approvals")
    public ApprovalQueuePage approvals(
            @RequestParam(name = "status", required = false) String status,
            @RequestParam(name = "page", defaultValue = "0") int page,
            @RequestParam(name = "size", defaultValue = "20") int size) {
        return approvalQueueService.list(status, page, size);
    }
}
