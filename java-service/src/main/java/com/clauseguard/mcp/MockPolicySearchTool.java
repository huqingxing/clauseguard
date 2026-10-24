package com.clauseguard.mcp;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.ai.tool.ToolCallback;
import org.springframework.ai.tool.definition.DefaultToolDefinition;
import org.springframework.ai.tool.definition.ToolDefinition;

import java.util.ArrayList;
import java.util.List;

/**
 * MCP 工具（mock）：制度检索。当前返回固定样例，真实检索留待后续阶段接入。
 * 方法名与工具名统一为 mock_policy_search。
 * 注册见 McpToolsConfig（显式注册为 ToolCallback Bean，保证单一注册源）。
 *
 * 说明：Spring AI 1.1.x 已移除 MethodToolCallback，改为直接实现 ToolCallback 接口。
 */
public class MockPolicySearchTool implements ToolCallback {

    private static final String INPUT_SCHEMA = """
            {
              "type": "object",
              "properties": {
                "query": {"type": "string", "description": "检索关键词，如 报销审批"},
                "topK":  {"type": "integer", "description": "返回条数上限"}
              },
              "required": ["query"]
            }
            """;

    private final ToolDefinition definition = DefaultToolDefinition.builder()
            .name("mock_policy_search")
            .description("检索企业内部制度条款（当前为 mock 实现，返回样例命中）")
            .inputSchema(INPUT_SCHEMA)
            .build();

    private final ObjectMapper mapper = new ObjectMapper();

    @Override
    public ToolDefinition getToolDefinition() {
        return definition;
    }

    @Override
    public String call(String arguments) {
        JsonNode node;
        try {
            node = mapper.readTree(arguments == null ? "{}" : arguments);
        } catch (JsonProcessingException e) {
            throw new IllegalArgumentException("invalid tool arguments: " + arguments, e);
        }
        String query = node.path("query").asText("");
        int topK = node.path("topK").asInt(5);
        return writeJson(mock_policy_search(query, topK));
    }

    /**
     * 制度检索实现（mock）。方法名即工具名，保留以便 Inspector 与回归识别。
     */
    public List<PolicyHit> mock_policy_search(String query, int topK) {
        int n = Math.max(1, Math.min(topK, 5));
        List<PolicyHit> hits = new ArrayList<>();
        for (int i = 1; i <= n; i++) {
            hits.add(new PolicyHit(
                    "policy-" + i,
                    "《" + query + "管理制度》第 " + i + " 章",
                    "关于「" + query + "」的样例制度文本（mock）。",
                    0.85 - i * 0.1));
        }
        // TODO(后续阶段): 替换为真实检索（经 Java REST/MCP 访问制度库）
        return hits;
    }

    private String writeJson(Object value) {
        try {
            return mapper.writeValueAsString(value);
        } catch (JsonProcessingException e) {
            throw new IllegalStateException("serialize policy hits failed", e);
        }
    }
}
