package com.clauseguard.mcp;

import org.springframework.ai.tool.ToolCallback;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * MCP 工具注册。law.search 默认转发 Python 真实检索（live）；设
 * clauseguard.retrieval.mode=mock 时回落固定样例，供无库/无密钥的离线演示。
 *
 * 为什么用「条件注册一个 Bean」而不是「工具内部判 mode」：MCP 客户端看到的工具名与
 * 描述必须与实际行为一致，live 下还挂着 mock_law_search 会让 Inspector 使用者误判
 * 数据来源。policy.search 本阶段未接真实库，保持原有 mock 不变。
 */
@Configuration
public class McpToolsConfig {

    private final String mode;

    public McpToolsConfig(@Value("${clauseguard.retrieval.mode:live}") String mode) {
        this.mode = mode;
    }

    @Bean
    public ToolCallback lawSearchCallback(PythonRetrievalClient client) {
        if ("mock".equalsIgnoreCase(mode.trim())) {
            return new MockLawSearchTool();
        }
        return new LiveLawSearchTool(client);
    }

    @Bean
    public ToolCallback mockPolicySearchCallback() {
        return new MockPolicySearchTool();
    }
}
