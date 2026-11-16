-- Stage 9 补强：审批记录带上「这条决定覆盖了哪些风险类型」。
--
-- 背景：interrupt 的待审项已按 clause_id 聚合（一条条款一条待审项），被合并的
-- risk_type 列表随 payload 下发。但审批表此前只有 risk（high 等档位），一条决定
-- 覆盖了两个性质不同的风险（如 payment_risk + disclaimer_risk）时，审计里只剩
-- 一个 high —— 事后没人说得清法务到底审了几类问题。
--
-- 存成逗号分隔的短串而不是 JSON 列：它们只用于展示与审计留痕，不参与任何查询
-- 条件，也不做包含查询；JSON 类型在 MySQL/H2 上的写法差异反而会拖累迁移。
--
-- 可空：V8 之前的历史审批记录没有这个信息，不编造。
--
-- 写法保持 MySQL 与 H2(MySQL 兼容模式) 通用；不加 IF NOT EXISTS。

ALTER TABLE approval ADD COLUMN risk_types VARCHAR(255) NULL;
