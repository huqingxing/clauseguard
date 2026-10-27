/**
 * 事件流纯函数的单测（Node 内置 test runner + 原生 TS 支持，零新增依赖）。
 *
 * 为什么放在 web/tests 而不是 src 旁边：tsconfig 的 include 只覆盖 src，
 * 测试文件不需要被 vue-tsc 类型检查（它没有 @types/node），也不需要被打进产物。
 *
 * 覆盖的两类分支正是线上最容易静默出错的地方：
 * - 重连退避：写错指数会在熔断期间持续打满网关，且界面上看不出来；
 * - seq 去重：写错比较方向会让重连后计数翻倍，或把新事件全丢掉。
 */

import test from 'node:test'
import assert from 'node:assert/strict'

import { retryDelayMs, shouldSkipSeq } from '../src/api/stream.ts'

test('退避按 1s→2s→4s→8s→16s 递增', () => {
  assert.equal(retryDelayMs(1), 1_000)
  assert.equal(retryDelayMs(2), 2_000)
  assert.equal(retryDelayMs(3), 4_000)
  assert.equal(retryDelayMs(4), 8_000)
  assert.equal(retryDelayMs(5), 16_000)
})

test('退避在 30s 封顶，且不因次数增长而溢出', () => {
  assert.equal(retryDelayMs(6), 30_000)
  assert.equal(retryDelayMs(7), 30_000)
  assert.equal(retryDelayMs(100), 30_000)
  assert.ok(Number.isFinite(retryDelayMs(10_000)))
})

test('退避对非法次数做兜底（0 或负数不得返回 0 导致热循环）', () => {
  assert.equal(retryDelayMs(0), 1_000)
  assert.equal(retryDelayMs(-3), 1_000)
})

test('首帧不丢：lastSeq 初始 -1 时 seq=1 放行', () => {
  assert.equal(shouldSkipSeq(1, -1), false)
})

test('重连回放去重：已处理过的 seq 一律丢弃', () => {
  assert.equal(shouldSkipSeq(5, 5), true)
  assert.equal(shouldSkipSeq(4, 5), true)
  assert.equal(shouldSkipSeq(1, 5), true)
})

test('新帧放行：seq 大于 lastSeq', () => {
  assert.equal(shouldSkipSeq(6, 5), false)
})

test('无 seq 的帧放行（不发生 NaN 比较，也不阻塞整条流）', () => {
  assert.equal(shouldSkipSeq(undefined, 5), false)
})
