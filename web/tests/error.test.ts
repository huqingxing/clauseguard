/**
 * 失败提示的人话翻译（零新增依赖，node:test + 原生 TS）。
 *
 * 为什么值得测：把 java 进程 kill 掉之后，界面上原来显示裸的「HTTP 500」——
 * 同一串字在「后端没起来」「网关 502」「熔断 503」三种情况下出现，
 * 用户没法据此判断该去找谁。翻译规则写错一格，就等于又退回到裸状态码。
 */

import test from 'node:test'
import assert from 'node:assert/strict'

import { describeApiError } from '../src/utils/error.ts'

/**
 * 这里按形状造错误而不 import ApiError：api/http.ts 的 import 链（'./auth' 不带扩展名）
 * 在 node --test 的原生 ESM 下解析不了，测试一旦 import 它就整个崩掉。
 * 而 describeApiError 按 status/code 两个字段判断，本来就不依赖具体类。
 */
function apiError(status: number, code: string, message: string): Error {
  return Object.assign(new Error(message), { status, code })
}

/** http.ts 拿不到结构化错误体时的形态：code 是 `HTTP_<status>`，message 是原始响应片段。 */
function bare(status: number): Error {
  return apiError(status, `HTTP_${status}`, `HTTP ${status}`)
}

test('网关 502/503/504 说成「连不上后端」，而不是报状态码', () => {
  for (const status of [502, 503, 504]) {
    assert.match(describeApiError(bare(status), '兜底'), /连不上后端服务/)
  }
})

test('500 说明是后端出错并带状态码', () => {
  assert.equal(describeApiError(bare(500), '兜底'), '后端出错了（HTTP 500），请稍后重试')
})

test('后端给了结构化 message 时原话优先，不自己编', () => {
  const error = apiError(409, 'CONTRACT_CHANGED', '合同已更新到 v3，请重新发起审查')
  assert.equal(describeApiError(error, '兜底'), '合同已更新到 v3，请重新发起审查')
})

test('401/403/404 各自说清是谁的问题', () => {
  assert.match(describeApiError(bare(401), '兜底'), /重新登录/)
  assert.match(describeApiError(bare(403), '兜底'), /权限/)
  assert.match(describeApiError(bare(404), '兜底'), /没找到/)
})

test('fetch 直接 reject（进程没了）也给出人话，实在没有就用兜底', () => {
  assert.equal(describeApiError(new Error('Failed to fetch'), '兜底'), 'Failed to fetch')
  assert.equal(describeApiError('不是 Error', '兜底'), '兜底')
  assert.equal(describeApiError(undefined, '兜底'), '兜底')
})
