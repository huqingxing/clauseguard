-- Stage 4：审查领域表。schema 由 Flyway 拥有，JPA ddl-auto=none。
-- 写法保持 MySQL 与 H2(MySQL 兼容模式) 通用，便于无 Docker 的本地启动。

CREATE TABLE review (
    id          VARCHAR(36)  NOT NULL PRIMARY KEY,
    contract_id VARCHAR(36)  NOT NULL,
    version     INT          NOT NULL DEFAULT 1,
    status      VARCHAR(32)  NOT NULL,
    created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE review_event (
    id         BIGINT       NOT NULL AUTO_INCREMENT PRIMARY KEY,
    review_id  VARCHAR(36)  NOT NULL,
    seq        INT          NOT NULL,
    type       VARCHAR(64)  NOT NULL,
    payload    TEXT         NOT NULL,
    ts         TIMESTAMP    NULL,
    INDEX idx_review_event_review_seq (review_id, seq)
);

CREATE TABLE approval (
    id          BIGINT       NOT NULL AUTO_INCREMENT PRIMARY KEY,
    review_id   VARCHAR(36)  NOT NULL,
    clause_id   VARCHAR(64)  NOT NULL,
    decision    VARCHAR(16)  NOT NULL,
    comment     TEXT         NULL,
    operator    VARCHAR(64)  NULL,
    created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_approval_review (review_id)
);
