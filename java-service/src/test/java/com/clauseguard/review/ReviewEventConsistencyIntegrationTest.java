package com.clauseguard.review;

import ch.qos.logback.classic.Level;
import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import com.clauseguard.events.AgentEvent;
import com.clauseguard.events.EventReplayStore;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfSystemProperty;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.jdbc.AutoConfigureTestDatabase;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;

import java.sql.Timestamp;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.anyCollection;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.doAnswer;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

/**
 * 状态 / 事件一致性的真实 MySQL 集成验收。
 *
 * <p>为什么默认跳过：仓库的单测口径是「不启 MySQL/Redis 也能跑」（见 src/test/resources/application.yml）。
 * 这类用例只在显式验收时打开：
 * <pre>
 * mvn -Dtest=ReviewEventConsistencyIntegrationTest -Dclauseguard.db.integration=true test
 * </pre>
 *
 * <p>失败注入不改表结构、不动历史数据：构造一条超过 MEDIUMTEXT 上限（16,777,215 字节）的
 * run.completed payload，MySQL STRICT_TRANS_TABLES 会以
 * "Data too long for column 'payload'" 拒绝插入——这正是 Stage 5 TEXT 上限触发的同一类失败。
 * 不采用「临时把列改窄」是因为现库里已有 100KB 的历史 payload，把列改回 TEXT 会破坏它们。
 */
@SpringBootTest(
        webEnvironment = SpringBootTest.WebEnvironment.NONE,
        properties = {
                "spring.datasource.url=jdbc:mysql://localhost:3306/clauseguard?useUnicode=true&characterEncoding=utf8&serverTimezone=Asia/Shanghai",
                "spring.datasource.username=clauseguard",
                "spring.datasource.password=clauseguard",
                "spring.datasource.driver-class-name=com.mysql.cj.jdbc.Driver",
                "spring.jpa.hibernate.ddl-auto=none",
                "spring.flyway.enabled=false",
                "clauseguard.event-store=in-memory",
                // 测试切片 application.yml 会整体盖掉主配置的 application.yml，缺这些占位符会启动失败
                "clauseguard.python.base-url=http://localhost:8000",
                "clauseguard.python.response-timeout=30s",
                "clauseguard.retrieval.mode=mock",
                "clauseguard.review.watchdog.enabled=false",
                "spring.ai.mcp.server.enabled=false",
                "management.tracing.enabled=false"
        })
@AutoConfigureTestDatabase(replace = AutoConfigureTestDatabase.Replace.NONE)
@EnabledIfSystemProperty(named = "clauseguard.db.integration", matches = "true")
class ReviewEventConsistencyIntegrationTest {

    private static final String TERMINAL_COMPLETED = "run.completed";

    @Autowired
    private ReviewService reviewService;
    @Autowired
    private ReviewRepository reviewRepository;
    @Autowired
    private ReviewEventRepository eventRepository;
    @Autowired
    private EventReplayStore replayStore;
    @Autowired
    private JdbcTemplate jdbc;
    @Autowired
    private PlatformTransactionManager transactionManager;
    @Autowired
    private ObjectMapper mapper;

    private static final List<String> LOGGERS = List.of(
            ReviewService.class.getName(),
            "com.clauseguard.review.ReviewEventWriter",
            ReviewWatchdog.class.getName());

    private final List<String> createdIds = new ArrayList<>();
    private ListAppender<ILoggingEvent> logAppender;

    @BeforeEach
    void captureLogs() {
        logAppender = new ListAppender<>();
        logAppender.start();
        for (String name : LOGGERS) {
            ((Logger) LoggerFactory.getLogger(name)).addAppender(logAppender);
        }
    }

    @AfterEach
    void cleanup() {
        for (String id : createdIds) {
            jdbc.update("DELETE FROM review_event WHERE review_id = ?", id);
            jdbc.update("DELETE FROM review WHERE id = ?", id);
        }
        createdIds.clear();
        for (String name : LOGGERS) {
            ((Logger) LoggerFactory.getLogger(name)).detachAppender(logAppender);
        }
        logAppender.stop();
    }

    /**
     * 阶段 1 复现 / 阶段 4 验收共用：终态事件写失败时，不允许留下
     * 「状态已 COMPLETED、终态事件不存在」这种不可解释组合。
     */
    @Test
    void terminalEventWriteFailureNeverLeavesStatusWithoutEvent() {
        String id = newId();
        insertReview(id, "RUNNING");
        AgentEvent terminal = new AgentEvent(99, "2026-11-12T00:00:00Z", TERMINAL_COMPLETED,
                oversizedCompletedPayload(99));

        String failure = "none";
        try {
            reviewService.handleAgentEvent(id, terminal);
        } catch (RuntimeException ex) {
            failure = rootMessage(ex);
        }

        DbState state = query(id);
        printEvidence("T-FAIL", id, failure, state);

        boolean diverged = "COMPLETED".equals(state.status) && state.terminalEvents == 0;
        assertThat(diverged)
                .as("状态前进但终态事件缺失（Stage 5 结构性缺陷）：%s", state)
                .isFalse();
        // 若最终停在终态，则重放存储里必须能拿到终态（客户端 ?since=0 的前提）
        if (isTerminal(state.status)) {
            assertThat(state.terminalEvents).isPositive();
            assertThat(replayStore.replaySince(id, -1))
                    .anySatisfy(e -> assertThat(e.type()).isIn("run.completed", "run.failed"));
        }
    }

    /** 正常路径：一次审查后 status 与最后一条事件类型一致，且 ?since=0 能拿到终态。 */
    @Test
    void normalPathStatusMatchesLastEventAndReplayHasTerminal() {
        String id = newId();
        insertReview(id, "RUNNING");
        reviewService.handleAgentEvent(id, new AgentEvent(1, "2026-11-12T00:00:01Z", "run.started",
                "{\"seq\":1,\"ts\":\"2026-11-12T00:00:01Z\",\"type\":\"run.started\","
                        + "\"run_id\":\"run-normal\",\"trace_id\":\"trace-normal\"}"));
        reviewService.handleAgentEvent(id, new AgentEvent(2, "2026-11-12T00:00:02Z", TERMINAL_COMPLETED,
                smallCompletedPayload(2)));

        DbState state = query(id);
        printEvidence("T-OK", id, "none", state);

        assertThat(state.status).isEqualTo("COMPLETED");
        assertThat(state.lastEventType).isEqualTo(TERMINAL_COMPLETED);
        assertThat(state.events).isEqualTo(2);
        assertThat(replayStore.replaySince(id, 0))
                .anySatisfy(e -> assertThat(e.type()).isEqualTo(TERMINAL_COMPLETED));
        // last_event_at 与事件 ts 对齐（阶段 13 口径不能被单事务改造破坏）
        assertThat(state.lastEventAt).isEqualTo("2026-11-12 00:00:02");
    }

    /**
     * 阶段 4 补偿：造一条「状态 COMPLETED、终态事件缺失」的历史脏数据（正是 Stage 5 的形态），
     * 让已有看门狗补写一条终态事件，且第二轮不重复补、WARN 里带修复次数。
     *
     * <p>扫描范围用 mock 限定到本用例的 review，避免看门狗顺手把演示库里其它历史缺口也修掉
     * （真实库里确实存在这类旧数据）；事件表、事务管理器、重放存储都是真实的。
     */
    @Test
    void watchdogRepairsTerminalGapOnceAndReplaySinceZeroSeesIt() {
        String id = newId();
        insertReview(id, "COMPLETED");
        jdbc.update("INSERT INTO review_event (review_id, seq, type, payload, ts) VALUES (?,?,?,?,?)",
                id, 1, "node.started", "{\"seq\":1,\"type\":\"node.started\"}",
                Timestamp.valueOf("2026-11-12 00:00:01"));

        // 只把扫描范围缩到本用例的 review（Mockito 不能 spy Spring Data 代理）；
        // 时间戳推进委托给真实 Repository，保证真实 DB 里的 last_event_at 也一起被断言。
        ReviewRepository scoped = mock(ReviewRepository.class);
        when(scoped.findTerminalReviews(anyCollection()))
                .thenReturn(List.<Object[]>of(new Object[]{id, "COMPLETED"}));
        doAnswer(inv -> {
            reviewRepository.updateLastEventAt(inv.getArgument(0), inv.getArgument(1));
            return null;
        }).when(scoped).updateLastEventAt(anyString(), anyString());
        ReviewWatchdog watchdog = new ReviewWatchdog(
                scoped, eventRepository, replayStore, reviewService, mapper, Duration.ofMinutes(30));
        TransactionTemplate tx = new TransactionTemplate(transactionManager);

        ReviewWatchdog.RepairResult first = tx.execute(status -> watchdog.repairTerminalGaps());
        DbState state = query(id);
        printEvidence("T-REPAIR", id, "none", state);

        assertThat(first.dbWritten()).isEqualTo(1);
        assertThat(first.totalRepaired()).isGreaterThanOrEqualTo(1);
        assertThat(state.terminalEvents).isEqualTo(1);
        assertThat(state.lastEventType).isEqualTo(TERMINAL_COMPLETED);
        assertThat(replayStore.replaySince(id, 0))
                .anySatisfy(e -> assertThat(e.type()).isEqualTo(TERMINAL_COMPLETED));
        assertThat(logAppender.list).anySatisfy(e -> {
            assertThat(e.getLevel().toInt()).isGreaterThanOrEqualTo(Level.WARN.toInt());
            assertThat(e.getFormattedMessage()).contains("累计修复次数");
        });

        ReviewWatchdog.RepairResult second = tx.execute(status -> watchdog.repairTerminalGaps());
        assertThat(second.dbWritten()).isZero();
        assertThat(query(id).terminalEvents).isEqualTo(1);
    }

    // ---- helpers ----

    private String newId() {
        String id = "repro-" + UUID.randomUUID().toString().substring(0, 8);
        createdIds.add(id);
        return id;
    }

    private void insertReview(String id, String status) {
        jdbc.update("INSERT INTO review (id, contract_id, version, status, created_at) VALUES (?,?,?,?,?)",
                id, "c-repro", 1, status, Timestamp.from(Instant.now()));
    }

    /** 超过 MEDIUMTEXT 上限（16,777,215 字节）——必然被 MySQL 拒绝写入。 */
    private static String oversizedCompletedPayload(long seq) {
        String bulk = "x".repeat(17_000_000);
        return "{\"seq\":" + seq + ",\"ts\":\"2026-11-12T00:00:00Z\",\"type\":\"run.completed\","
                + "\"usage\":{\"input_tokens\":1,\"output_tokens\":1,\"cost_cny\":0.1},"
                + "\"findings_count\":1,\"quality\":{\"verified_rate\":1.0,\"rework_count\":0,\"unverified_count\":0},"
                + "\"findings\":[{\"clause_id\":\"c1\",\"agent\":\"a\",\"risk_type\":\"r\","
                + "\"risk_level\":\"low\",\"summary\":\"s\",\"evidence\":\"" + bulk + "\"}]}";
    }

    private static String smallCompletedPayload(long seq) {
        return "{\"seq\":" + seq + ",\"ts\":\"2026-11-12T00:00:02Z\",\"type\":\"run.completed\","
                + "\"usage\":{\"input_tokens\":1,\"output_tokens\":1,\"cost_cny\":0.1},"
                + "\"findings_count\":0,\"quality\":{\"verified_rate\":1.0,\"rework_count\":0,\"unverified_count\":0}}";
    }

    private DbState query(String id) {
        String status = jdbc.queryForObject("SELECT status FROM review WHERE id = ?", String.class, id);
        Integer events = jdbc.queryForObject("SELECT COUNT(*) FROM review_event WHERE review_id = ?",
                Integer.class, id);
        Integer terminal = jdbc.queryForObject(
                "SELECT COUNT(*) FROM review_event WHERE review_id = ? AND type IN ('run.completed','run.failed')",
                Integer.class, id);
        List<String> types = jdbc.queryForList(
                "SELECT type FROM review_event WHERE review_id = ? ORDER BY seq", String.class, id);
        String lastType = types.isEmpty() ? "-" : types.get(types.size() - 1);
        String lastAt = jdbc.queryForObject(
                "SELECT DATE_FORMAT(last_event_at, '%Y-%m-%d %H:%i:%s') FROM review WHERE id = ?",
                String.class, id);
        return new DbState(status, events, terminal, lastType, lastAt);
    }

    private void printEvidence(String tag, String id, String failure, DbState state) {
        System.out.printf("%n===== %s | review=%s =====%n", tag, id);
        System.out.printf("DB  review.status          = %s%n", state.status);
        System.out.printf("DB  review_event 总行数     = %d%n", state.events);
        System.out.printf("DB  终态事件行数            = %d%n", state.terminalEvents);
        System.out.printf("DB  最后一条事件 type       = %s%n", state.lastEventType);
        System.out.printf("DB  review.last_event_at   = %s%n", state.lastEventAt);
        System.out.printf("ETL 写入异常               = %s%n", failure);
        System.out.printf("SSE replaySince(-1)        = %s%n",
                replayStore.replaySince(id, -1).stream().map(e -> e.seq() + ":" + e.type()).toList());
        for (ILoggingEvent e : logAppender.list) {
            if (e.getLevel().toInt() >= Level.WARN.toInt()) {
                System.out.printf("LOG %-5s %s%n", e.getLevel(), e.getFormattedMessage());
            }
        }
        System.out.println("===== END =====");
    }

    private static boolean isTerminal(String status) {
        return "COMPLETED".equals(status) || "FAILED".equals(status);
    }

    private static String rootMessage(Throwable t) {
        Throwable cur = t;
        while (cur.getCause() != null) {
            cur = cur.getCause();
        }
        String msg = cur.getMessage();
        return (msg == null ? cur.getClass().getSimpleName() : msg).replaceAll("\\s+", " ");
    }

    /** DB 查询结果快照，仅用于日志与断言。 */
    private record DbState(String status, int events, int terminalEvents,
                           String lastEventType, String lastEventAt) {
    }
}
