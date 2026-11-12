-- Stage 12：合同上传与版本。schema 由 Flyway 拥有，JPA ddl-auto=none。
-- contract 一行 = 一个合同的一个版本：首次上传 version=1；同一 contract_id 再次上传
-- （前端「仅重审变更」场景）version+1，旧版本行保留——增量 diff 的基线快照与
-- 历史审查都按 (id, version) 定位。
-- storage_key 指向 Java 侧存储中的相对键（约定键形如 contracts/<id>/v1/source.pdf，
-- 修复前为 <uuid>-v1.pdf 的遗留扁平键，仍作只读回退），与磁盘路径的映射只存在于
-- ContractService，绝不由调用方拼路径（防路径穿越）。键约定见 contracts/storage-keys.md。
-- 写法保持 MySQL 与 H2(MySQL 兼容模式) 通用。

CREATE TABLE contract (
    id          VARCHAR(36)  NOT NULL,
    filename    VARCHAR(255) NOT NULL,
    content_type VARCHAR(128) NOT NULL,
    size_bytes  BIGINT       NOT NULL,
    storage_key VARCHAR(512) NOT NULL,
    version     INT          NOT NULL DEFAULT 1,
    created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id, version)
);

CREATE INDEX idx_contract_created ON contract (created_at);
