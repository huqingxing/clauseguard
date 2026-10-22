package com.clauseguard.health;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

/**
 * Stage 1 基础设施探针。仅返回存活状态，不含任何业务逻辑（铁律：不在 Controller 写业务）。
 */
@RestController
public class HealthController {

    @GetMapping("/healthz")
    public Map<String, String> healthz() {
        return Map.of("status", "ok");
    }
}
