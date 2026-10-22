package com.clauseguard;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

/**
 * 业务与工具服务入口。只保留启动类；数据库、MCP、SSE 相关装配
 * 在后续阶段按模块追加，避免 Stage 0 引入无法验证的配置。
 */
@SpringBootApplication
public class ClauseGuardApplication {

    public static void main(String[] args) {
        SpringApplication.run(ClauseGuardApplication.class, args);
    }
}
