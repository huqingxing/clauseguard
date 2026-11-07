-- 检索实验台专用表；单独脚本执行，不进 Java 的 db/migration。
-- 为什么独立：法条库属于知识库（Python 直连 Postgres），Java 的 Flyway 管的是业务库
-- MySQL，把建表塞进业务迁移会让两条数据链路耦合，且 vector 扩展在 MySQL 上不存在。
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS law_chunk (
    chunk_id     text PRIMARY KEY,
    law          text NOT NULL,
    article      text NOT NULL,
    chapter      text,
    text         text NOT NULL,
    strategy     text NOT NULL,
    embedding    vector(1024),
    meta         jsonb
);

-- HNSW 走余弦距离：文本嵌入的模长不可比，L2 会被长文本的系统性偏差带歪。
CREATE INDEX IF NOT EXISTS law_chunk_embedding_hnsw
    ON law_chunk USING hnsw (embedding vector_cosine_ops);

-- BM25 侧与融合按 strategy 过滤，避免五种切分策略混在同一召回池里互相稀释。
CREATE INDEX IF NOT EXISTS law_chunk_strategy_idx ON law_chunk (strategy);
