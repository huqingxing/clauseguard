package com.clauseguard.mcp;

import org.junit.jupiter.api.Test;
import org.springframework.ai.tool.ToolCallback;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * MCP 工具单元测试：校验两个 mock 工具可被正确装配（名称/参数/执行），
 * 等价于 Inspector 调用路径的核心逻辑，无需启动完整容器。
 */
class MockSearchToolsTest {

    @Test
    void lawSearchExposesCorrectNameAndReturnsHits() {
        ToolCallback callback = new MockLawSearchTool();
        assertThat(callback.getToolDefinition().name()).isEqualTo("mock_law_search");

        String result = callback.call("{\"query\":\"违约责任\",\"topK\":2}");
        assertThat(result).contains("law-1").contains("law-2").contains("合同法");
    }

    @Test
    void policySearchExposesCorrectNameAndReturnsHits() {
        ToolCallback callback = new MockPolicySearchTool();
        assertThat(callback.getToolDefinition().name()).isEqualTo("mock_policy_search");

        String result = callback.call("{\"query\":\"报销审批\",\"topK\":3}");
        assertThat(result).contains("policy-1").contains("policy-3");
    }

    @Test
    void respectsTopKBound() {
        ToolCallback callback = new MockLawSearchTool();
        String result = callback.call("{\"query\":\"x\",\"topK\":99}");
        // 上限为 5，不应返回超过 5 条
        assertThat(result).contains("law-5").doesNotContain("law-6");
    }
}
