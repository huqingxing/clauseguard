package com.clauseguard.events;

/**
 * 重放存储中的一条事件。seq 即 Last-Event-ID 的依据。
 */
public record StoredEvent(long seq, String type, String payload) {
}
