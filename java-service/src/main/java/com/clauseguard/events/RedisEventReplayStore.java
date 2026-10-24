package com.clauseguard.events;

import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.data.domain.Range;
import org.springframework.data.redis.connection.RedisStreamCommands.XAddOptions;
import org.springframework.data.redis.connection.stream.MapRecord;
import org.springframework.data.redis.core.StreamOperations;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Component;

import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Redis Stream 版事件重放存储。每个 review 一条 stream，MAXLEN=1000 保留最近 1000 条。
 * 断线重连时用 XRANGE 按 seq 补齐缺失事件。
 */
@Component
@ConditionalOnProperty(name = "clauseguard.event-store", havingValue = "redis", matchIfMissing = true)
public class RedisEventReplayStore implements EventReplayStore {

    private static final long MAXLEN = 1000L;
    private final StreamOperations<String, Object, Object> streamOps;

    public RedisEventReplayStore(StringRedisTemplate redisTemplate) {
        this.streamOps = redisTemplate.opsForStream();
    }

    private static String key(String reviewId) {
        return "review:" + reviewId + ":events";
    }

    @Override
    public void append(String reviewId, long seq, String type, String payload) {
        Map<String, Object> fields = new LinkedHashMap<>();
        fields.put("seq", Long.toString(seq));
        fields.put("type", type);
        fields.put("payload", payload);
        streamOps.add(key(reviewId), fields, XAddOptions.maxlen(MAXLEN).approximateTrimming(true));
    }

    @Override
    public List<StoredEvent> replaySince(String reviewId, long afterSeq) {
        List<MapRecord<String, Object, Object>> records = streamOps.range(key(reviewId), Range.unbounded());
        return records.stream()
                .map(MapRecord::getValue)
                .map(this::toStoredEvent)
                .filter(e -> e.seq() > afterSeq)
                .sorted(Comparator.comparingLong(StoredEvent::seq))
                .toList();
    }

    @Override
    public long currentMaxSeq(String reviewId) {
        return streamOps.range(key(reviewId), Range.unbounded()).stream()
                .map(MapRecord::getValue)
                .map(this::toStoredEvent)
                .mapToLong(StoredEvent::seq)
                .max()
                .orElse(-1L);
    }

    private StoredEvent toStoredEvent(Map<Object, Object> value) {
        long seq = Long.parseLong(String.valueOf(value.get("seq")));
        String type = String.valueOf(value.get("type"));
        String payload = String.valueOf(value.get("payload"));
        return new StoredEvent(seq, type, payload);
    }
}
