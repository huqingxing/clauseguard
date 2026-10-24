package com.clauseguard.events;

import java.util.List;

/**
 * 事件重放存储：支撑断线续传。生产用 Redis Stream（保留最近 1000 条），
 * 本地无 Redis 时用内存实现。二者语义一致，便于测试。
 */
public interface EventReplayStore {

    /**
     * 追加一条事件。实现需保证保留窗口（如 Redis Stream MAXLEN=1000）。
     */
    void append(String reviewId, long seq, String type, String payload);

    /**
     * 返回 seq 严格大于 afterSeq 的事件，按 seq 升序。afterSeq=-1 表示全量回放。
     */
    List<StoredEvent> replaySince(String reviewId, long afterSeq);

    /**
     * 当前已存储的最大 seq；无事件返回 -1。
     */
    long currentMaxSeq(String reviewId);
}
