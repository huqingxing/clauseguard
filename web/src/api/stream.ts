/**
 * 事件流：重连策略、seq 去重、订阅生命周期——不碰 REST，也不直接依赖 DOM。
 *
 * 为什么从 reviews.ts 搬过来：subscribeReview 里最容易写错的两件事（退避多久、断点从哪续）
 * 藏在 EventSource 后面，过去只能靠肉眼看浏览器网络面板。抽到这里后 EventSource 由调用方注入，
 * 用 Node 内置 test runner 就能把「先 200 再断」这类行为跑出来，不必引入测试框架或重试库。
 */

import type { AgentStreamEvent } from '../types/events'

const BASE_DELAY_MS = 1000
const MAX_DELAY_MS = 30_000
/** 2^5 = 32 倍已超过 30s 上限，再往上只是无意义的乘法。 */
const MAX_EXPONENT = 5

/** 收到终态事件即停止订阅：服务端在终态后会 complete()，不主动关会立刻触发一次重连。 */
const TERMINAL_TYPES = new Set(['run.completed', 'run.failed'])

const DEFAULT_BASE_PATH = '/api/v1/reviews'

/**
 * 第 n 次重连前等待多久：1s → 2s → 4s → 8s → 16s → 30s（此后恒定 30s）。
 *
 * 为什么要退避：下游熔断时 /stream 返回 503，浏览器对 EventSource 会静默重试，
 * 固定间隔重试会在熔断期间持续打满网关；30s 封顶保证恢复后最迟半分钟回到正常。
 */
export function retryDelayMs(attempt: number): number {
  const exponent = Math.min(Math.max(attempt - 1, 0), MAX_EXPONENT)
  return Math.min(MAX_DELAY_MS, BASE_DELAY_MS * 2 ** exponent)
}

/**
 * 是否丢弃该事件：是否已经被处理过（按 seq 单调性判断）。
 *
 * 为什么必须去重：重连时前端会带上 since 断点，但服务端仍可能把边界上的一条再发一次
 * （例如重放与实时扇出同时命中）。不去重就会把同一个 node.finished 计两次、摘要翻倍。
 *
 * 没有 seq 的帧（契约之外）一律放行，交由展示层忽略，不让畸形帧卡住整条流。
 */
export function shouldSkipSeq(seq: number | undefined, lastSeq: number): boolean {
  return typeof seq === 'number' && seq <= lastSeq
}

export type ConnectionState = 'connecting' | 'open' | 'reconnecting' | 'closed'

export interface ReviewStreamHandlers {
  onEvent: (event: AgentStreamEvent) => void
  onState?: (state: ConnectionState, attempt: number) => void
}

export interface ReviewStream {
  close: () => void
}

/** EventSource 里我们真正用到的那几个成员：注入替身时不必伪造整个 DOM 接口。 */
export interface StreamSource {
  onopen: (() => void) | null
  onmessage: ((message: { data: string }) => void) | null
  onerror: (() => void) | null
  close: () => void
}

export interface SubscribeOptions {
  /** 默认 /api/v1/reviews，只为测试与联调留出口。 */
  basePath?: string
  /** 默认 new EventSource；测试注入假实现，生产不需要传。 */
  createSource?: (url: string) => StreamSource
}

/**
 * 订阅事件流，带 seq 去重与指数退避重连。
 *
 * 两个必须自己做的原因：
 * 1) 去重 + 断点：手动重建 EventSource 不会自动带 Last-Event-ID，Java 会从 seq 0 全量回放；
 *    这里把 lastSeq 放进 `?since=`，让服务端只补缺失的那一段。
 * 2) 重连：下游熔断时 stream 返回 503，浏览器默认重试不给用户任何信号；
 *    这里把状态透出界面，并按 1s→30s 退避。
 */
export function subscribeReview(
  reviewId: string,
  handlers: ReviewStreamHandlers,
  options: SubscribeOptions = {},
): ReviewStream {
  const basePath = options.basePath ?? DEFAULT_BASE_PATH
  const createSource =
    options.createSource ?? ((url: string) => new EventSource(url) as unknown as StreamSource)

  let source: StreamSource | null = null
  let closed = false
  let attempt = 0
  let timer: ReturnType<typeof setTimeout> | null = null
  let lastSeq = -1

  const emit = (state: ConnectionState) => handlers.onState?.(state, attempt)

  const close = () => {
    if (closed) return
    closed = true
    if (timer) {
      clearTimeout(timer)
      timer = null
    }
    source?.close()
    source = null
    emit('closed')
  }

  /** 断点只在真的看到事件之后才有意义：lastSeq=-1 时保持老 URL，服务端从头回放。 */
  const url = () => {
    const base = `${basePath}/${reviewId}/stream`
    return lastSeq >= 0 ? `${base}?since=${lastSeq}` : base
  }

  const open = () => {
    if (closed) return
    emit(attempt === 0 ? 'connecting' : 'reconnecting')
    const es = createSource(url())
    source = es

    es.onopen = () => {
      // 注意：这里不重置 attempt。HTTP 200 只说明「连上了」，不代表「收到了东西」——
      // 服务端先 200 再立刻断时，onopen 每次都成功，在这里清零会让退避退化成恒定 1s，
      // 熔断期间等于每秒打一次网关。
      emit('open')
    }

    es.onmessage = (message) => {
      let event: AgentStreamEvent
      try {
        event = JSON.parse(message.data) as AgentStreamEvent
      } catch {
        // 单条坏帧不应中断整条流（重放存储仍是事实源）。
        return
      }
      if (shouldSkipSeq(event.seq, lastSeq)) return
      if (typeof event.seq === 'number') {
        lastSeq = event.seq
        // 真正推进了才清零：重连后收到的第一条新事件说明这段连接是有效的，
        // 下一次断开应该重新从 1s 开始，而不是继承上一轮的 30s。
        attempt = 0
      }
      handlers.onEvent(event)
      if (TERMINAL_TYPES.has(event.type)) close()
    }

    es.onerror = () => {
      es.close()
      if (source === es) source = null
      if (closed) return
      attempt += 1
      emit('reconnecting')
      timer = setTimeout(open, retryDelayMs(attempt))
    }
  }

  open()
  return { close }
}
