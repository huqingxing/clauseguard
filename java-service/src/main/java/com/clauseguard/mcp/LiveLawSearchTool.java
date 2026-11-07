package com.clauseguard.mcp;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.ai.tool.ToolCallback;
import org.springframework.ai.tool.definition.DefaultToolDefinition;
import org.springframework.ai.tool.definition.ToolDefinition;

import java.util.List;

/**
 * MCP 工具 law.search：转发 Python 的真实法条检索，替代阶段 6 之前的 mock 样例。
 *
 * 返回结构与原契约完全一致（[{id, article, text, score}]，id 即 chunk_id），
 * 所以引用白名单仍只需校验「本次返回集合」；参数（strategy/top_k/融合）不在 Java
 * 出现第二份，全部由 python-agent/config/retrieval.yaml 决定。
 */
public class LiveLawSearchTool implements ToolCallback {

    private static final String INPUT_SCHEMA = """
            {
              "type": "object",
              "properties": {
                "query": {"type": "string", "description": "检索关键词，如 违约金过分高于损失"},
                "topK":  {"type": "integer", "description": "可选；不传则用 retrieval.yaml 的 top_k"}
              },
              "required": ["query"]
            }
            """;

    private final ToolDefinition definition = DefaultToolDefinition.builder()
            .name("law.search")
            .description("检索真实法律法规条文（转发 Python /v1/retrieval/search，参数来自检索实验台结论）")
            .inputSchema(INPUT_SCHEMA)
            .build();

    private final PythonRetrievalClient client;
    private final ObjectMapper mapper = new ObjectMapper();

    public LiveLawSearchTool(PythonRetrievalClient client) {
        this.client = client;
    }

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
        // -1 = 调用方没指定，交给 Python 的 YAML；显式 0/负数同样按「未指定」处理。
        int topK = node.has("topK") ? node.path("topK").asInt(-1) : -1;
        return writeJson(law_search(query, topK));
    }

    /** 直接暴露给单测/Inspector 之外调用方的同义入口，避免测试去拼 JSON 参数。 */
    public List<LawHit> law_search(String query, int topK) {
        return client.search(query, topK);
    }

    private String writeJson(Object value) {
        try {
            return mapper.writeValueAsString(value);
        } catch (JsonProcessingException e) {
            throw new IllegalStateException("serialize law hits failed", e);
        }
    }
}
