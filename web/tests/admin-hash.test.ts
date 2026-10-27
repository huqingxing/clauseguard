/**
 * 管理端老链接迁移的单测（零新增依赖，仍是 node:test + 原生 TS）。
 *
 * 为什么这条值得测：`#eval` 落到 catch-all 不会报错，只会静默停在运维健康屏——
 * 用户以为书签坏了，控制台却一片干净，出了问题也没地方查。
 */

import test from 'node:test'
import assert from 'node:assert/strict'

import { toHashRoute } from '../src/admin/legacy-hash.ts'

test('阶段 3 的老链接 #eval 补前导斜杠', () => {
  assert.equal(toHashRoute('#eval'), '#/eval')
})

test('已经是新格式的 hash 原样返回', () => {
  assert.equal(toHashRoute('#/eval'), '#/eval')
  assert.equal(toHashRoute('#/approvals/abc-123'), '#/approvals/abc-123')
})

test('空 hash 交给路由的默认跳转，不在这里编一个目标', () => {
  assert.equal(toHashRoute(''), '')
  assert.equal(toHashRoute('#'), '#')
})

test('带查询串的老链接只补斜杠，不吞参数', () => {
  assert.equal(toHashRoute('#eval?tab=cost'), '#/eval?tab=cost')
})

test('多段路径老链接（未来从别处接入）也能迁移', () => {
  assert.equal(toHashRoute('#approvals/r-1'), '#/approvals/r-1')
})

test('非 hash 输入不动：这个函数只负责 hash 这一段', () => {
  assert.equal(toHashRoute('/admin?x=1'), '/admin?x=1')
})
