package com.clauseguard.mcp;

import org.springframework.ai.tool.ToolCallback;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * MCP 工具注册。把两个 mock 检索工具显式暴露为 ToolCallback Bean，
 * 由 Spring AI MCP Server 自动挂载（SSE / stdio 两种传输共用）。
 * 显式声明保证单一注册源，不依赖 @Tool 自动扫描。
 */
@Configuration
public class McpToolsConfig {

    @Bean
    public ToolCallback mockLawSearchCallback() {
        return new MockLawSearchTool();
    }

    @Bean
    public ToolCallback mockPolicySearchCallback() {
        return new MockPolicySearchTool();
    }
}
