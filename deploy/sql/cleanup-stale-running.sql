-- 一次性数据修复脚本（不是 Flyway migration，绝不能放进 db/migration）
--
-- 背景：本机 6379 上跑的是 Redis 3.2.100（D:\develop\redis），不支持 Streams。
-- Java 用默认 event-store=redis 启动后，SSE 端点 500：
--   io.lettuce.core.RedisCommandExecutionException: ERR unknown command 'XRANGE'
-- 审查请求已经写进 review 表（status=RUNNING）才去开事件流，于是这些审查永远
-- 等不到终态事件，永远停在 RUNNING —— 前端看到的是「排队中」转圈到天荒地老。
--
-- 为什么是 UPDATE 而不是 DELETE：
--   1. 可追溯：失败也是发生过的事实，删掉就查不出「这台机器曾被低版本 Redis 卡住过」；
--   2. 安全：review 挂了 findings 是根据它索引的，DELETE 要处理级联，
--      而这条 SQL 的使用者往往是凌晨两点排障的人，不该给他一把会误伤的刀。
--
-- 为什么不写进 Flyway：
--   它只适用于本机（判据是「RUNNING 且超过 30 分钟」）。别人的环境里可能真有一笔
--   跑了 40 分钟的大合同，migration 一执行就把正在跑的任务标成失败。
--
-- 执行时间：2026-10-27 22:1x
-- 执行前快照（复核用，6 条，全部 age > 490 分钟，远超 30 分钟阈值）：
--   ba2731f8-7b2e-4628-b141-b4882f8d8d22   v1  RUNNING  2026-10-27 01:26:12  (1244 min)  run-6b9c2381
--   0c16ffe0-5876-45d8-b93f-68b7efdbf79e   v1  RUNNING  2026-10-27 09:31:45  ( 758 min)  run-ca7e683a
--   5c9b2d16-18aa-4862-b0b8-af9f0c7be153   v1  RUNNING  2026-10-27 13:53:15  ( 497 min)  run-d14e9f40
--   89046791-ba50-4cd0-9a12-66d5f1fb41f8   v1  RUNNING  2026-10-27 13:54:09  ( 496 min)  run-669e1793
--   affcb969-72ae-423b-81fa-9f61c301e9f1   v1  RUNNING  2026-10-27 13:55:12  ( 495 min)  run-b7f0f666
--   805650cc-e9ad-40ee-81f6-7670eb9f5799   v1  RUNNING  2026-10-27 13:57:02  ( 493 min)  run-4ab9b7af
--
-- 同一时刻库里还有 22 条 COMPLETED、16 条 AWAITING_APPROVAL，均不在本脚本范围内。
-- 状态枚举（ReviewStatus）：QUEUED / RUNNING / AWAITING_APPROVAL / COMPLETED / FAILED

-- 第 1 步：先看清楚要动谁（不改数据）
SELECT id, version, status, created_at,
       TIMESTAMPDIFF(MINUTE, created_at, NOW()) AS age_min
FROM review
WHERE status = 'RUNNING'
  AND created_at < NOW() - INTERVAL 30 MINUTE
ORDER BY created_at;

-- 第 2 步：标成失败态。一条 UPDATE 带完整 WHERE，不用 id IN (...) 硬编码 ——
-- 硬编码的列表第二天就会过期，而 WHERE 条件自己会跟着时间走。
UPDATE review
SET status = 'FAILED'
WHERE status = 'RUNNING'
  AND created_at < NOW() - INTERVAL 30 MINUTE;

-- 第 3 步：核对结果（期望 RUNNING 归零，FAILED 出现 6 条）
SELECT status, COUNT(*) AS n FROM review GROUP BY status ORDER BY n DESC;
