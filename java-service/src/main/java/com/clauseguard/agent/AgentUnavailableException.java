package com.clauseguard.agent;

/**
 * Python 编排服务不可用（熔断打开或调用失败）。映射到 HTTP 503（AGENT_UNAVAILABLE）。
 */
public class AgentUnavailableException extends RuntimeException {

    public AgentUnavailableException(String message) {
        super(message);
    }

    public AgentUnavailableException(String message, Throwable cause) {
        super(message, cause);
    }
}
