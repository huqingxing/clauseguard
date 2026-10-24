package com.clauseguard.events;

/**
 * Python SSE 事件的通用信封。仅抽取契约公共字段 + 保留原始 JSON，
 * 不在 Java 侧复制 events.schema.json 的逐类型模型（铁律：禁止私加/手搓字段）。
 */
public record AgentEvent(long seq, String ts, String type, String rawJson) {
}
