-- Stage 1 初始化：业务库骨架。仅建库与排序集，不建业务表（避免提前写业务逻辑）。
CREATE DATABASE IF NOT EXISTS `clauseguard`
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;
