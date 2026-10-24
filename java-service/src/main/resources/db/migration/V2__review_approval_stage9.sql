-- Stage 9：人工审批断点 + 断点恢复所需字段。
-- review 增加 run_id（Python 生成的运行 ID，用于 resume 定位 thread_id）、
-- contract_version / content_hash（审批乐观锁基线，来自 interrupt 事件）、
-- pending_payload（interrupt 事件完整 payload JSON，供审批回显与基线校验）。
-- approval 增加 before_wording / after_wording / risk（前后措辞 + 风险档留痕）。
-- 写法保持 MySQL 与 H2(MySQL 兼容模式) 通用。

ALTER TABLE review ADD COLUMN run_id VARCHAR(36) NULL;
ALTER TABLE review ADD COLUMN contract_version VARCHAR(16) NULL;
ALTER TABLE review ADD COLUMN content_hash VARCHAR(128) NULL;
ALTER TABLE review ADD COLUMN pending_payload TEXT NULL;

ALTER TABLE approval ADD COLUMN before_wording TEXT NULL;
ALTER TABLE approval ADD COLUMN after_wording TEXT NULL;
ALTER TABLE approval ADD COLUMN risk VARCHAR(32) NULL;
