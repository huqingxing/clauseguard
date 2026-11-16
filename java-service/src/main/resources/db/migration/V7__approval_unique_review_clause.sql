-- Stage 9 补强：approval 的 (review_id, clause_id) 唯一约束。
--
-- 与状态 CAS 的分工：CAS 兜的是「两个请求同时从 AWAITING_APPROVAL 抢同一份待审快照」，
-- 同一个赢家仍然可以把同一个 clause 写两遍——单次请求里 approvals 数组带了重复 clause_id，
-- 或者历史上「一条审批一个事务」时代留下的重复行，CAS 全都拦不住。唯一约束才是最后一道兜底。
--
-- 执行前提：表内不得已存在重复对。加约束前必须先跑
--   SELECT review_id, clause_id, COUNT(*) c FROM approval
--   GROUP BY review_id, clause_id HAVING c > 1;
-- 有结果就先人工处置（保留 id 最大的那条，或按业务口径打标记），空结果才能迁移。
-- 迁移里不写 DELETE：本机有重复不代表别的环境也有，写死 DML 只会误删。
--
-- 写法保持 MySQL 与 H2(MySQL 兼容模式) 通用；不加 IF NOT EXISTS，让重复执行直接失败。
--
-- 顺带说明：唯一索引的最左前缀就是 review_id，V1 建的 idx_approval_review 从此冗余。
-- 这里保留不动——删它只省一点写放大，换不来查询收益，不值得为它多担一次迁移风险。

ALTER TABLE approval
    ADD CONSTRAINT uk_approval_review_clause UNIQUE (review_id, clause_id);
