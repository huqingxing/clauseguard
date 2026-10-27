/**
 * 事件流单测（Node 内置 test runner + 原生 TS 支持，零新增依赖）。
 *
 * 为什么放在 web/tests 而不是 src 旁边：tsconfig 的 include 只覆盖 src，
 * 测试文件不需要被 vue-tsc 类型检查（它没有 @types/node），也不需要被打进产物。
 *
 * 覆盖的三类分支正是线上最容易静默出错的地方：
 * - 重连退避：写错指数会在熔断期间持续打满网关，且界面上看不出来；
 * - 断点：重连不带 since 就每次全量回放，去重一旦写错就是计数翻倍；
 * - seq 去重：写错比较方向会让重连后计数翻倍，或把新事件全丢掉。
 *
 * 订阅本身也在 Node 里跑：EventSource 由 subscribeReview 的 options 注入替身，
 * 定时器用 node:test 的 mock timers 推进——不引入 jsdom、不引入第三方重试库。
 */

import test from 'node:test'
import assert from 'node:assert/strict'

import { retryDelayMs, shouldSkipSeq, subscribeReview } from '../src/api/stream.ts'

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

/** EventSource 的替身：只实现 subscribeReview 真正用到的那几个成员。 */
class FakeSource {
  onopen: (() => void) | null = null
  onmessage: ((message: { data: string }) => void) | null = null
  onerror: (() => void) | null = null
  closed = false
  url: string

  // 不用构造函数参数属性：node --test 的 TS 剥离不支持那种语法（会被当成枚举级特性拒绝）。
  constructor(url: string) {
    this.url = url
  }

  close() {
    this.closed = true
  }

  fireOpen() {
    this.onopen?.()
  }

  /** 契约里 seq 是必需字段（events.schema.json），这里造最小合法帧。 */
  send(seq: number, type = 'node.started') {
    this.onmessage?.({
      data: JSON.stringify({ seq, ts: '2026-10-27T00:00:00Z', type, node: 'ingest', agent: 'ingest' }),
    })
  }

  fireError() {
    this.onerror?.()
  }
}

function harness(t) {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const sources: FakeSource[] = []
  const states: Array<[string, number]> = []
  const seen: number[] = []
  const stream = subscribeReview(
    'r-1',
    {
      onEvent: (event) => seen.push(event.seq as number),
      onState: (state, attempt) => states.push([state, attempt]),
    },
    {
      createSource: (url) => {
        const source = new FakeSource(url)
        sources.push(source)
        return source
      },
    },
  )
  return { sources, states, seen, stream }
}

test('open 后没有事件再断：退避继续累加，不退化成恒定 1s', (t) => {
  const { sources } = harness(t)

  assert.equal(sources.length, 1)
  sources[0].fireOpen()
  sources[0].fireError()

  // 第一次断：1s 后重连
  t.mock.timers.tick(999)
  assert.equal(sources.length, 1)
  t.mock.timers.tick(1)
  assert.equal(sources.length, 2)

  // 第二次：服务端又是「先 200 再断」——onopen 成功但没有事件，等待必须是 2s
  sources[1].fireOpen()
  sources[1].fireError()
  t.mock.timers.tick(1_999)
  assert.equal(sources.length, 2)
  t.mock.timers.tick(1)
  assert.equal(sources.length, 3)

  // 第三次：4s
  sources[2].fireOpen()
  sources[2].fireError()
  t.mock.timers.tick(3_999)
  assert.equal(sources.length, 3)
  t.mock.timers.tick(1)
  assert.equal(sources.length, 4)
})

test('收到新事件后才把退避清零', (t) => {
  const { sources, states, seen } = harness(t)

  sources[0].fireError()
  t.mock.timers.tick(1_000)
  assert.equal(sources.length, 2)

  sources[1].fireOpen()
  sources[1].send(1)
  assert.deepEqual(seen, [1])
  sources[1].fireError()

  // 有事件落地 → 下一轮重新从 1s 开始，而不是接着 2s
  t.mock.timers.tick(1_000)
  assert.equal(sources.length, 3)

  sources[2].fireOpen()
  sources[2].fireError()
  t.mock.timers.tick(1_999)
  assert.equal(sources.length, 3)
  t.mock.timers.tick(1)
  assert.equal(sources.length, 4)

  // 清零后再累计：第 1 次是 1（重置后重新开始），紧接着第 2 次是 2——不是继承上一轮的 2/3。
  assert.deepEqual(states.at(-1), ['reconnecting', 2])
})

test('重连带 since 断点；没收到事件时不带', (t) => {
  const { sources } = harness(t)

  assert.equal(sources[0].url, '/api/v1/reviews/r-1/stream')

  sources[0].send(1)
  sources[0].send(2)
  sources[0].fireError()
  t.mock.timers.tick(1_000)

  assert.equal(sources[1].url, '/api/v1/reviews/r-1/stream?since=2')
})

test('重放回来的旧事件被丢弃，也不清零退避', (t) => {
  const { sources, seen } = harness(t)

  sources[0].send(5)
  sources[0].fireError()
  t.mock.timers.tick(1_000)

  // 服务端边界上又把 seq=5 发了一次：不能重复渲染，也不能当成「连接有效」
  sources[1].send(5)
  assert.deepEqual(seen, [5])
  sources[1].fireError()
  t.mock.timers.tick(1_999)
  assert.equal(sources.length, 2)
  t.mock.timers.tick(1)
  assert.equal(sources.length, 3)
})

test('终态事件后不再重连', (t) => {
  const { sources } = harness(t)

  sources[0].send(7, 'run.completed')
  sources[0].fireError()
  t.mock.timers.tick(30_000)
  assert.equal(sources.length, 1)
})
