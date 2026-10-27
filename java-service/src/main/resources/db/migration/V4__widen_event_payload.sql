-- Stage 5：事件载荷与待审快照改用 MEDIUMTEXT。
--
-- 为什么必须改：MySQL 的 TEXT 上限是 65535 **字节**，不是字符。中文 UTF-8 一个字 3 字节，
-- 而 run.completed 的 payload 里带着全部 findings（条款原文 + 引用摘录 + 建议），
-- 审批续跑后的那次 run.completed 实测超过上限，插入直接失败：
--   Data truncation: Data too long for column 'payload'
-- 后果不是「少一条日志」：状态已经被改成 COMPLETED，而事件没落库，
-- 用户端重放看不到终态，页面会永远停在「等待法务审批中」。
--
-- pending_payload 同理：interrupt 快照随高风险条款数量线性增长，TEXT 上限迟早会撞上。
-- 用 MEDIUMTEXT（16MB）而不是 LONGTEXT：单条事件到不了 16MB，再大就是上游出了问题，
-- 让它在写入时失败比默默占满磁盘更好。

ALTER TABLE review_event MODIFY payload MEDIUMTEXT NOT NULL;
ALTER TABLE review MODIFY pending_payload MEDIUMTEXT NULL;
