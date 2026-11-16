-- 审批留痕的最小依据：一条 risk 为 NULL 的审批记录，在合规意义上等于「没批过」，
-- 但系统显示已通过 —— 这种记录宁可写不进去，也不能悄悄留在审计表里。
-- 代码层（ApprovalApplier）已经做到「快照里没有该条款或没有 risk 就整单 422」，
-- 这一层是给绕过应用直接写 SQL 的人兜的底。
--
-- 上线前先查存量（本机执行时 0 行）：
--   SELECT COUNT(*) FROM approval WHERE risk IS NULL;
-- 若不为 0，必须先人工补数据再执行本迁移；严格模式下 ALTER 会直接失败并中断发布。
ALTER TABLE approval
    MODIFY COLUMN risk VARCHAR(32) NOT NULL;
