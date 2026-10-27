/**
 * 管理端凭证的单测（Node 内置 test runner + 原生 TS 支持，零新增依赖）。
 *
 * 为什么这些分支值得测：凭证这一层出错不会报错，只会表现为「登录了还是 401」
 * 或者「换了个账号还拿着上一个人的凭证」。而 Basic 头的编码一旦写错，
 * 表现是后端说密码不对，前端却看不出是自己编码的问题。
 *
 * Node 里没有 window.sessionStorage，所以这些用例本身就在跑「存储不可用 → 退化为内存」
 * 这条分支（隐私模式同名分支）。
 */

import test from 'node:test'
import assert from 'node:assert/strict'

import {
  clearCredential,
  encodeBasic,
  getAuthHeader,
  getUsername,
  hasCredential,
  setCredential,
} from '../src/api/auth.ts'

test('Basic 头用标准 base64', () => {
  assert.equal(encodeBasic('admin', 'admin123'), 'Basic YWRtaW46YWRtaW4xMjM=')
})

test('非 Latin-1 账号名按 UTF-8 编码，不会让 btoa 抛错', () => {
  const header = encodeBasic('张三', '口令pw')
  assert.ok(header.startsWith('Basic '))

  const binary = atob(header.slice('Basic '.length))
  const bytes = Uint8Array.from(binary, (ch) => ch.charCodeAt(0))
  assert.equal(new TextDecoder().decode(bytes), '张三:口令pw')
})

test('没有凭证时不产生 Authorization 头', () => {
  clearCredential()
  assert.equal(getAuthHeader(), null)
  assert.equal(hasCredential(), false)
})

test('写入后能读回，且不把明文密码留在头里', () => {
  setCredential('admin', 'admin123')
  const header = getAuthHeader()

  assert.ok(header?.startsWith('Basic '))
  assert.equal(header?.includes('admin123'), false)
  assert.equal(getUsername(), 'admin')
  assert.equal(hasCredential(), true)
  clearCredential()
})

test('换账号会覆盖旧凭证，不会继续带着上一个人', () => {
  setCredential('alice', 'one')
  const first = getAuthHeader()
  setCredential('bob', 'two')

  assert.notEqual(getAuthHeader(), first)
  assert.equal(getUsername(), 'bob')
  clearCredential()
})

test('退出后账号与凭证一起清空', () => {
  setCredential('admin', 'admin123')
  clearCredential()

  assert.equal(getAuthHeader(), null)
  assert.equal(getUsername(), '')
  assert.equal(hasCredential(), false)
})
