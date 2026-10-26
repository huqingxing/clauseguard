package com.clauseguard.admin;

import com.clauseguard.agent.PythonAgentClient;
import com.clauseguard.api.model.AdminHealth;
import com.clauseguard.api.model.AdminHealthPython;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.stereotype.Service;

import java.util.Optional;

/**
 * Stage 12：双栈健康聚合。
 *
 * 为什么由 Java 代理探测 Python：前端禁止直连 Python 端口（红线），管理页要一屏看全
 * 双栈状态，只能由 Java 代为探测后聚合。Python 不可达记 down 并附 null 详情，
 * 不因此让本端点失败——单栈故障不应掩盖另一栈的状态。
 */
@Service
public class AdminHealthService {

    private final PythonAgentClient agentClient;
    private final ObjectMapper mapper;

    public AdminHealthService(PythonAgentClient agentClient, ObjectMapper mapper) {
        this.agentClient = agentClient;
        this.mapper = mapper;
    }

    public AdminHealth health() {
        Optional<String> body = agentClient.probeHealth();
        AdminHealthPython python = new AdminHealthPython()
                .status(body.isPresent() ? AdminHealthPython.StatusEnum.UP : AdminHealthPython.StatusEnum.DOWN);
        body.ifPresent(raw -> {
            try {
                // JsonNode 本身就是 Object，Jackson 会按 JSON 结构序列化，无需再转 Map。
                python.setDetail(mapper.readTree(raw));
            } catch (Exception ex) {
                // 探测到但响应不可解析：仍记 up（进程活着），详情留空。
            }
        });
        return new AdminHealth()
                .java(new com.clauseguard.api.model.AdminHealthJava()
                        .status(com.clauseguard.api.model.AdminHealthJava.StatusEnum.UP))
                .python(python);
    }
}
