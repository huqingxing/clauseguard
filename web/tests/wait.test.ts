/**
 * 等待时长的口径（零新增依赖，node:test + 原生 TS）。
 *
 * 为什么值得测：队列里「超过 2 小时标黄」是一条要被人信的提示。
 * 差一分钟没标黄、或者时区解析失败显示成「NaN 天」，在截图上看不出来，
 * 但在真实队列里就是「这笔到底该不该催」的判断错位。
 */

import test from 'node:test'
import assert from 'node:assert/strict'

import { OVERDUE_MS, formatWait, isOverdue, waitMillis } from '../src/utils/wait.ts'

const NOW = Date.parse('2026-10-27T12:00:00Z')

function minutesAgo(minutes: number): string {
  return new Date(NOW - minutes * 60_000).toISOString()
}

test('不足一分钟显示「刚刚」', () => {
  assert.equal(formatWait(minutesAgo(0), NOW), '刚刚')
  assert.equal(formatWait(new Date(NOW - 30_000).toISOString(), NOW), '刚刚')
})

test('一小时内只显示分钟', () => {
  assert.equal(formatWait(minutesAgo(1), NOW), '1 分钟')
  assert.equal(formatWait(minutesAgo(59), NOW), '59 分钟')
})

test('超过一小时显示小时与分钟', () => {
  assert.equal(formatWait(minutesAgo(60), NOW), '1 小时')
  assert.equal(formatWait(minutesAgo(125), NOW), '2 小时 5 分')
})

test('超过一天只保留两级单位', () => {
  assert.equal(formatWait(minutesAgo(24 * 60), NOW), '1 天')
  assert.equal(formatWait(minutesAgo(24 * 60 + 4 * 60), NOW), '1 天 4 小时')
})

test('2 小时是标黄的分界线：正好 2 小时不算超时', () => {
  assert.equal(isOverdue(minutesAgo(120), NOW), false)
  assert.equal(isOverdue(minutesAgo(121), NOW), true)
  assert.equal(OVERDUE_MS, 7_200_000)
})

test('服务器比本机快几秒时夹到 0，不显示负数', () => {
  const future = new Date(NOW + 5_000).toISOString()
  assert.equal(waitMillis(future, NOW), 0)
  assert.equal(formatWait(future, NOW), '刚刚')
})

test('非法时间戳不产生 NaN', () => {
  assert.equal(waitMillis('not-a-date', NOW), 0)
  assert.equal(formatWait('', NOW), '刚刚')
  assert.equal(isOverdue('not-a-date', NOW), false)
})
