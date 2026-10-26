package com.clauseguard.admin;

import com.clauseguard.api.model.AdminHealth;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * Stage 12：管理页健康聚合入口。业务在 AdminHealthService。
 */
@RestController
@RequestMapping("/api/v1/admin")
public class AdminController {

    private final AdminHealthService adminHealthService;

    public AdminController(AdminHealthService adminHealthService) {
        this.adminHealthService = adminHealthService;
    }

    @GetMapping("/health")
    public AdminHealth health() {
        return adminHealthService.health();
    }
}
