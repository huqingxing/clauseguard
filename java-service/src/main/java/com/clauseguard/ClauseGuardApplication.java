package com.clauseguard;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.scheduling.annotation.EnableScheduling;

/**
 * 业务与工具服务入口。只保留启动类；数据库、MCP、SSE 相关装配
 * 在后续阶段按模块追加，避免 Stage 0 引入无法验证的配置。
 *
 * <p>@EnableScheduling（Stage 14）：审查看门狗需要一个固定间隔的后台扫描。
 * 全服务目前只有这一个定时任务，调度线程因此只开了一个的默认池。
 */
@SpringBootApplication
@EnableScheduling
public class ClauseGuardApplication {

    public static void main(String[] args) {
        SpringApplication.run(ClauseGuardApplication.class, args);
    }
}
