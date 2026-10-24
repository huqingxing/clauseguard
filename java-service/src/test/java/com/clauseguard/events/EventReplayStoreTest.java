package com.clauseguard.events;

import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 事件重放存储集成测试（断线续传核心逻辑）。
 * 使用内存实现验证 Last-Event-ID 补齐语义；Redis 实现保持相同语义（见 RedisEventReplayStore）。
 */
class EventReplayStoreTest {

    @Test
    void replaySinceReturnsOnlyEventsAfterGivenSeqInOrder() {
        EventReplayStore store = new InMemoryEventReplayStore();
        for (int i = 1; i <= 5; i++) {
            store.append("r1", i, "node.started", "{\"seq\":" + i + "}");
        }

        List<StoredEvent> all = store.replaySince("r1", -1);
        assertThat(all).hasSize(5);
        assertThat(all.stream().mapToLong(StoredEvent::seq).toArray()).containsExactly(1L, 2L, 3L, 4L, 5L);

        List<StoredEvent> after2 = store.replaySince("r1", 2);
        assertThat(after2.stream().mapToLong(StoredEvent::seq).toArray()).containsExactly(3L, 4L, 5L);
    }

    @Test
    void currentMaxSeqReflectsLatestAndMinusOneWhenEmpty() {
        EventReplayStore store = new InMemoryEventReplayStore();
        assertThat(store.currentMaxSeq("missing")).isEqualTo(-1);

        store.append("r2", 7, "run.completed", "{}");
        store.append("r2", 9, "run.failed", "{}");
        assertThat(store.currentMaxSeq("r2")).isEqualTo(9);
    }

    @Test
    void honorsMaxLengthWindowToPreventUnboundedGrowth() {
        // 内存实现与 Redis Stream MAXLEN=1000 同义；此处用小窗口验证逻辑一致性
        EventReplayStore store = new InMemoryEventReplayStore(3);
        for (int i = 1; i <= 5; i++) {
            store.append("r3", i, "message.delta", "{}");
        }
        List<StoredEvent> replay = store.replaySince("r3", -1);
        assertThat(replay.stream().mapToLong(StoredEvent::seq).toArray()).containsExactly(3L, 4L, 5L);
        assertThat(store.currentMaxSeq("r3")).isEqualTo(5);
    }
}
