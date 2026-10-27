package com.clauseguard.events;

import jakarta.annotation.PostConstruct;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.boot.autoconfigure.data.redis.RedisProperties;
import org.springframework.data.domain.Range;
import org.springframework.data.redis.connection.RedisStreamCommands.XAddOptions;
import org.springframework.data.redis.connection.stream.MapRecord;
import org.springframework.data.redis.core.StreamOperations;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.RedisConnectionFailureException;
import org.springframework.data.redis.RedisSystemException;
import org.springframework.stereotype.Component;

import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Redis Stream 版事件重放存储。每个 review 一条 stream，MAXLEN=1000 保留最近 1000 条。
 * 断线重连时用 XRANGE 按 seq 补齐缺失事件。
 *
 * 启动期能力探测（fail-fast）：{@link #probeStreamsCapability()} 在 bean 初始化时执行一次真实
 * 的 XADD + XRANGE。不是 PING——PING 通只能证明端口有人听，证明不了 Streams 命令可用。
 * 低版本 Redis（如 Windows 版 3.2，不支持 Streams）会在这里就抛
 * {@code ERR unknown command 'XADD'}，应用直接启动失败，而不是等第一个用户打开页面才 500。
 * 探测成功后删除探针 key，不留脏数据。
 */
@Component
@ConditionalOnProperty(name = "clauseguard.event-store", havingValue = "redis", matchIfMissing = true)
public class RedisEventReplayStore implements EventReplayStore {

    private static final Logger log = LoggerFactory.getLogger(RedisEventReplayStore.class);
    private static final long MAXLEN = 1000L;
    private static final String PROBE_KEY = "review:__startup_probe__:clauseguard";

    private final StreamOperations<String, Object, Object> streamOps;
    private final StringRedisTemplate redisTemplate;
    private final RedisProperties redisProperties;

    public RedisEventReplayStore(StringRedisTemplate redisTemplate, RedisProperties redisProperties) {
        this.redisTemplate = redisTemplate;
        this.redisProperties = redisProperties;
        this.streamOps = redisTemplate.opsForStream();
    }

    /**
     * 启动期同步探测：连不上或版本不支持 Streams 时直接让上下文启动失败。
     * 放在 @PostConstruct（而不是异步线程或懒探测）是为了让失败停在 boot 阶段，
     * 不会漂到第一个 SSE 请求才暴露。测试环境因 RedisAutoConfiguration 被排除而不构造本 bean，
     * 探针随之不触发——等价于「仅在真实 redis 配置下启用」。
     */
    @PostConstruct
    public void probeStreamsCapability() {
        String endpoint = redisProperties.getHost() + ":" + redisProperties.getPort();
        try {
            Map<String, Object> fields = new LinkedHashMap<>();
            fields.put("seq", "0");
            fields.put("type", "startup.probe");
            fields.put("payload", "{}");
            streamOps.add(PROBE_KEY, fields);          // 真实写能力：低版本 Redis 在此即报 unknown command
            streamOps.range(PROBE_KEY, Range.unbounded()); // 真实读能力：XLEN/XRANGE 链路
            try {
                redisTemplate.delete(PROBE_KEY);       // 清理探针 key，不污染 stream
            } catch (RuntimeException cleanup) {
                log.warn("启动探针 key 清理失败（不影响启动）：{}", cleanup.getMessage());
            }
            log.info("事件存储 Redis Stream 能力探测通过：XADD/XRANGE 可用（{}）", endpoint);
        } catch (RedisConnectionFailureException e) {
            throw new IllegalStateException(
                    "事件存储初始化失败：无法连接 Redis（" + endpoint + "）。\n" +
                    "  请确认 Redis 已启动并监听该端口；Streams 能力探测需要先能连上。\n" +
                    "  临时降级（重启即丢、断线续传失效）可加启动参数 --clauseguard.event-store=in-memory，但不能当作默认。",
                    e);
        } catch (RedisSystemException e) {
            String msg = e.getCause() != null && e.getCause().getMessage() != null
                    ? e.getCause().getMessage() : e.getMessage();
            if (msg != null && msg.contains("unknown command")) {
                throw new IllegalStateException(
                        "事件存储初始化失败：Redis 不支持 Streams 命令（" + msg.trim() + "）。\n" +
                        "  本机 " + endpoint + " 上很可能跑着 Redis < 5.0（如 Windows 版 3.2）。\n" +
                        "  请用 redis-cli 从宿主机执行 `XADD` 确认端口后面到底是谁——别靠 PING 判断。\n" +
                        "  升级到 Redis >= 5.0（推荐 redis-stack）；或临时降级加 --clauseguard.event-store=in-memory（不可作默认）。",
                        e);
            }
            throw new IllegalStateException(
                    "事件存储初始化失败：Redis 探测异常（" + endpoint + "）：" + msg, e);
        }
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
