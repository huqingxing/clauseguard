-- Stage 13：给 review 加「最后一次有活动的时间」，为阶段 14 的超时回收提供依据。
--
-- 为什么需要它：现在判断一条审查「卡了多久」只能用 created_at。但审批续跑的审查
-- created_at 可能是一周前，用它判超时会误杀正在走审批的合同。审批动作本身不是事件，
-- 不进 review_event，所以「活动」必须以事件为准——last_event_at = 最近一条 review_event.ts。
--
-- 不在本阶段加 NOT NULL：先允许 NULL，等回填验证完整（COUNT(*) WHERE last_event_at IS NULL = 0）
-- 后，再单独评估是否收紧。加 NOT NULL 前必须确认没有漏填的 review。
--
-- 写法保持 MySQL 与 H2(MySQL 兼容模式) 通用：
--   * 用 DATETIME(3)（毫秒精度，与事件 ts 对齐；existing 的 created_at/ts 是 TIMESTAMP 秒级，
--     新建列精度更高不会丢数据，只是更细）。
--   * 回填用相关子查询（按 review_id 取 MAX(ts)），不用 UPDATE...JOIN 派生表写法——
--     后者在 H2 上不被支持，而同一份迁移要在测试(H2)与生产(MySQL)都能跑。语义完全一致。

ALTER TABLE review ADD COLUMN last_event_at DATETIME(3) NULL;

-- 回填：有事件的 review 取各自最近一条事件的 ts
UPDATE review r
SET r.last_event_at = (SELECT MAX(e.ts) FROM review_event e WHERE e.review_id = r.id)
WHERE r.last_event_at IS NULL
  AND EXISTS (SELECT 1 FROM review_event e WHERE e.review_id = r.id);

-- 兜底：仍无事件（异常 / 事件表被清过）的 review 回落 created_at，
-- 宁可高估等待时长也绝不留下 NULL——NULL 会让阶段 14 的 `last_event_at < NOW() - N` 判据漏掉它。
UPDATE review SET last_event_at = created_at WHERE last_event_at IS NULL;
