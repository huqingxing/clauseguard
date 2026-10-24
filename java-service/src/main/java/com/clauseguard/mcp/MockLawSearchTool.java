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
 * MCP 工具（mock）：法条检索。当前返回固定样例，真实检索留待后续阶段接入。
 * 方法名与工具名统一为 mock_law_search，便于 Inspector 识别与回归。
 * 注册见 McpToolsConfig（显式注册为 ToolCallback Bean，保证单一注册源）。
 *
 * 说明：Spring AI 1.1.x 已移除 MethodToolCallback，改为直接实现 ToolCallback 接口；
 * 工具名/描述/入参 Schema 由 getToolDefinition() 提供，call() 负责执行与结果序列化。
 */
public class MockLawSearchTool implements ToolCallback {

    private static final String INPUT_SCHEMA = """
            {
              "type": "object",
              "properties": {
                "query": {"type": "string", "description": "检索关键词，如 违约责任"},
                "topK":  {"type": "integer", "description": "返回条数上限"}
              },
              "required": ["query"]
            }
            """;

    private final ToolDefinition definition = DefaultToolDefinition.builder()
            .name("mock_law_search")
            .description("检索相关法律法规条文（当前为 mock 实现，返回样例命中）")
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
        return writeJson(mock_law_search(query, topK));
    }

    /**
     * 法条检索实现（mock）。方法名即工具名，保留以便 Inspector 与回归识别。
     */
    public List<LawHit> mock_law_search(String query, int topK) {
        int n = Math.max(1, Math.min(topK, 5));
        List<LawHit> hits = new ArrayList<>();
        for (int i = 1; i <= n; i++) {
            hits.add(new LawHit(
                    "law-" + i,
                    "《中华人民共和国合同法》第 " + (100 + i) + " 条",
                    "关于「" + query + "」的样例法条文本（mock）。",
                    0.9 - i * 0.1));
        }
        // TODO(后续阶段): 替换为真实检索（经 Java REST/MCP 访问法规库，绝不直连外部库）
        return hits;
    }

    private String writeJson(Object value) {
        try {
            return mapper.writeValueAsString(value);
        } catch (JsonProcessingException e) {
            throw new IllegalStateException("serialize law hits failed", e);
        }
    }
}
