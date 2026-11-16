/**
 * 未审条款提示的文案规则（node:test + 原生 TS，零新增依赖）。
 *
 * 为什么值得测：这段文案是唯一告诉用户「结果不完整」的地方。写错一格的后果不是难看，
 * 而是让「模型整段不可用」看起来像「合同很干净」——真实出过两次（401 与 403 各一次）。
 */

import test from 'node:test'
import assert from 'node:assert/strict'

import { unreviewedNotice } from '../src/utils/degraded.ts'

test('全部审成时不出现提示', () => {
  assert.equal(unreviewedNotice(null), '')
  assert.equal(unreviewedNotice(undefined), '')
  assert.equal(unreviewedNotice({ unreviewed_clauses: 0, llm_errors: 0 }), '')
})

test('模型调用失败覆盖到每一条未审条款时，明说原因', () => {
  assert.equal(
    unreviewedNotice({ unreviewed_clauses: 3, llm_errors: 3 }),
    '本次有 3 条因模型不可用未审查，结果可能不完整',
  )
  // 单元数可能大于条款数（同一条款被多位专家审、都失败），仍是「模型不可用」。
  assert.equal(
    unreviewedNotice({ unreviewed_clauses: 3, llm_errors: 5 }),
    '本次有 3 条因模型不可用未审查，结果可能不完整',
  )
})

test('部分失败来自输出不可解析时，不替失败原因下结论', () => {
  assert.equal(
    unreviewedNotice({ unreviewed_clauses: 3, llm_errors: 1 }),
    '本次有 3 条未完成审查，结果可能不完整',
  )
  assert.equal(
    unreviewedNotice({ unreviewed_clauses: 2, llm_errors: 0 }),
    '本次有 2 条未完成审查，结果可能不完整',
  )
})
