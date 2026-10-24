package com.clauseguard.events;

import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;

import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArrayList;

/**
 * 内存版事件重放存储。保留窗口与 Redis 实现一致（最近 1000 条），
 * 供本地无 Redis 启动与测试使用。
 */
@Component
@ConditionalOnProperty(name = "clauseguard.event-store", havingValue = "in-memory")
public class InMemoryEventReplayStore implements EventReplayStore {

    private final Map<String, CopyOnWriteArrayList<StoredEvent>> store = new ConcurrentHashMap<>();
    private final int maxLen;

    public InMemoryEventReplayStore() {
        this(1000);
    }

    public InMemoryEventReplayStore(int maxLen) {
        this.maxLen = maxLen;
    }

    @Override
    public void append(String reviewId, long seq, String type, String payload) {
        CopyOnWriteArrayList<StoredEvent> list =
                store.computeIfAbsent(reviewId, k -> new CopyOnWriteArrayList<>());
        list.add(new StoredEvent(seq, type, payload));
        if (list.size() > maxLen) {
            list.subList(0, list.size() - maxLen).clear();
        }
    }

    @Override
    public List<StoredEvent> replaySince(String reviewId, long afterSeq) {
        return store.getOrDefault(reviewId, new CopyOnWriteArrayList<>())
                .stream()
                .filter(e -> e.seq() > afterSeq)
                .sorted(Comparator.comparingLong(StoredEvent::seq))
                .toList();
    }

    @Override
    public long currentMaxSeq(String reviewId) {
        return store.getOrDefault(reviewId, new CopyOnWriteArrayList<>())
                .stream()
                .mapToLong(StoredEvent::seq)
                .max()
                .orElse(-1L);
    }
}
