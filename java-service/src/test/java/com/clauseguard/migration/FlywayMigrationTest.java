package com.clauseguard.migration;

import org.flywaydb.core.Flyway;
import org.flywaydb.core.api.MigrationInfo;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;

import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

/**
 * 迁移的实机校验：断言「代码里的迁移文件」和「数据库里的实际 schema」是一回事。
 *
 * <p>为什么需要这个测试：本项目所有 {@code @SpringBootTest} 要么是 Web 切片（不加载数据源），
 * 要么仓储全 mock，唯一连库的集成测试还显式关了 Flyway。结果就是——迁移文件提交了、
 * 测试全绿、但迁移从没被任何一次测试执行过。V6 就这么在仓库里躺了一轮，本地库始终停在 v5，
 * 索引根本不存在。这种缺陷不会让任何断言变红，只会在别人按图索骥查性能时炸出来。
 *
 * <p>为什么命名 {@code *Test} 而不是 {@code *IT}：surefire 默认只收 {@code *Test}，
 * 叫 IT 就得再引 failsafe，而「默认不跑」正是这类测试最怕的状态。宁可每次多花两秒连库。
 *
 * <p>为什么不 clean + 从零重建：本项目的库账号只有 {@code clauseguard.*} 权限，
 * 建不了独立 schema；在业务库上 clean 等于把开发数据全删了。所以改成「校验现状」——
 * 对「迁移没跑」这个失败模式，校验 pending 比从头建库更直接，且零破坏。
 *
 * <p>无库环境（纯前端/文档改动）用 {@link org.junit.jupiter.api.Assumptions} 跳过，
 * 但 CI 必须提供一个可达的库，否则这个测试等于不存在。
 */
@Tag("migration")
class FlywayMigrationTest {

    private static final String URL = System.getProperty(
            "clauseguard.test.db.url",
            System.getenv().getOrDefault(
                    "CLAUSEGUARD_TEST_DB_URL",
                    "jdbc:mysql://localhost:3306/clauseguard"
                            + "?useUnicode=true&characterEncoding=utf8&serverTimezone=Asia/Shanghai"));
    // 优先环境变量：CI 里用 env 传参最确定——mvn -D 的 user properties 是否传给 fork 出来的
    // 测试 JVM 依赖 surefire 版本行为，env 没有这个不确定性。
    private static final String USER = System.getenv().getOrDefault(
            "CLAUSEGUARD_TEST_DB_USER",
            System.getProperty("clauseguard.test.db.user", "clauseguard"));
    private static final String PASSWORD = System.getenv().getOrDefault(
            "CLAUSEGUARD_TEST_DB_PASSWORD",
            System.getProperty("clauseguard.test.db.password", "clauseguard"));

    /** V1~V8 建出来的关键对象，少任何一个都说明迁移没跑完或被人手工改过。 */
    private static final List<String> REQUIRED_INDEXES = List.of(
            "idx_review_event_review_seq",   // V1
            "idx_approval_review",           // V1
            "idx_review_contract_created",   // V6
            "idx_review_event_review_ts",    // V6
            "uk_approval_review_clause"      // V7
    );

    @Test
    void noPendingMigrationsAndChecksumsMatch() {
        assumeReachable();
        Flyway flyway = flyway();
        // checksum 对不上（有人改了已执行的迁移文件）在这里就抛，不用等启动失败。
        flyway.validate();

        MigrationInfo[] pending = flyway.info().pending();
        assertThat(Arrays.stream(pending).map(i -> i.getVersion() + " " + i.getDescription()))
                .as("数据库落后于代码：有迁移文件没被应用。先跑 mvn flyway:migrate")
                .isEmpty();
    }

    @Test
    void schemaHasEveryIndexTheMigrationsCreate() throws Exception {
        assumeReachable();
        List<String> actual = new ArrayList<>();
        try (Connection c = connect();
             PreparedStatement ps = c.prepareStatement(
                     "SELECT DISTINCT INDEX_NAME FROM information_schema.STATISTICS "
                             + "WHERE TABLE_SCHEMA = DATABASE() AND INDEX_NAME <> 'PRIMARY'")) {
            try (ResultSet rs = ps.executeQuery()) {
                while (rs.next()) {
                    actual.add(rs.getString(1));
                }
            }
        }
        // 只断言「该有的都在」，不禁止多出来的：手工加的临时索引不该让这里变红。
        assertThat(actual).containsAll(REQUIRED_INDEXES);
    }

    @Test
    void approvalCarriesRiskTypesColumn() throws Exception {
        assumeReachable();
        try (Connection c = connect();
             PreparedStatement ps = c.prepareStatement(
                     "SELECT COUNT(*) FROM information_schema.COLUMNS "
                             + "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'approval' "
                             + "AND COLUMN_NAME = 'risk_types'")) {
            try (ResultSet rs = ps.executeQuery()) {
                rs.next();
                // 没有这列，聚合后的审批记录就只剩一个 risk 档位，审计里看不出覆盖了几类风险。
                assertThat(rs.getInt(1)).as("V8 的 risk_types 列不存在").isEqualTo(1);
            }
        }
    }

    @Test
    void uniqueConstraintActuallyRejectsDuplicateApproval() throws Exception {
        assumeReachable();
        try (Connection c = connect()) {
            c.setAutoCommit(false);
            String reviewId = "migration-test-" + System.nanoTime();
            try (PreparedStatement ps = c.prepareStatement(
                    "INSERT INTO approval (review_id, clause_id, decision, operator) VALUES (?,?,?,?)")) {
                insert(ps, reviewId, "c1");
                // 同 (review_id, clause_id) 再来一条：约束必须拒绝，否则 V7 形同没加。
                assertThatThrownBy(() -> insert(ps, reviewId, "c1"))
                        .isInstanceOf(SQLException.class)
                        .hasMessageContaining("uk_approval_review_clause");
            } finally {
                // 无论断言是否通过都不留脏数据：审批表是审计表，测试痕迹比没测更糟。
                c.rollback();
                c.setAutoCommit(true);
            }
        }
    }

    private static void insert(PreparedStatement ps, String reviewId, String clauseId) throws SQLException {
        ps.setString(1, reviewId);
        ps.setString(2, clauseId);
        ps.setString(3, "approve");
        ps.setString(4, "migration-test");
        ps.executeUpdate();
    }

    private static Flyway flyway() {
        return Flyway.configure()
                .dataSource(URL, USER, PASSWORD)
                .locations("classpath:db/migration")
                .load();
    }

    private static Connection connect() throws SQLException {
        return DriverManager.getConnection(URL, USER, PASSWORD);
    }

    private static void assumeReachable() {
        boolean ok;
        try (Connection c = connect(); Statement s = c.createStatement()) {
            s.execute("SELECT 1");
            ok = true;
        } catch (SQLException e) {
            ok = false;
        }
        assumeTrue(ok, "MySQL 不可达（" + URL + "），跳过迁移校验；CI 必须提供可用的库");
    }
}
