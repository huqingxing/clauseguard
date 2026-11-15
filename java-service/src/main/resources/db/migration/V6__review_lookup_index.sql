-- 阶段 12 引入合同列表/详情后，每个合同都要「取最近一次审查」。
-- review 表原本只有主键，contract_id 上没有索引：列表 20 条就是 20 次按 contract_id 的全表扫，
-- 详情页还要再扫一次。联合索引把「按合同过滤 + 按创建时间倒序取第一条」一条路走完。
CREATE INDEX idx_review_contract_created ON review (contract_id, created_at);

-- 阶段 13/14 的回填、看门狗判超时都要按 review_id 取 MAX(ts)。
-- 现有 idx_review_event_review_seq 是 (review_id, seq)，ts 不在索引里，MAX(ts) 必须回表；
-- 补 (review_id, ts) 后这个聚合可以只走索引。
CREATE INDEX idx_review_event_review_ts ON review_event (review_id, ts);
