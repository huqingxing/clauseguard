/**
 * 审查任务的 REST + SSE 客户端（Stage 12）。
 *
 * 红线：只调 Java（/api/v1/reviews，vite 已代理到 8080），绝不直连 Python 8000。
 * 事件语义不在前端重新解释——原样解析后交给展示层，字段严格来自 events.schema.json。
 */

import type { AgentStreamEvent } from '../types/events'
import type {
  ApproveRequest,
  ApproveResponse,
  FindingsPage,
  ReviewMode,
  ReviewResult,
} from '../types/api'
import { apiGet, apiPost } from './http'

const BASE = '/api/v1/reviews'

/** 收到终态事件即停止订阅：服务端在终态后会 complete()，不主动关会立刻触发一次重连。 */
const TERMINAL_TYPES = new Set(['run.completed', 'run.failed'])

export interface CreateReviewInput {
  contract_id: string
  version: number
  mode: ReviewMode
  base_version?: number
}

export interface CreateReviewResult {
  review_id: string
  stream_url: string
}

export async function createReview(input: CreateReviewInput): Promise<CreateReviewResult> {
  const body: Record<string, unknown> = {
    contract_id: input.contract_id,
    version: input.version,
    mode: input.mode,
  }
  // base_version 只在显式指定时下发：0 会让 Java/编排层按 version-1 推断，
  // 显式传 0 与「让它推断」在语义上不同，故省略即推断。
  if (input.base_version !== undefined) body.base_version = input.base_version
  return apiPost<CreateReviewResult>(BASE, body)
}

export function getReview(reviewId: string): Promise<ReviewResult> {
  return apiGet<ReviewResult>(`${BASE}/${reviewId}`)
}

export function listFindings(reviewId: string, page = 0, size = 20): Promise<FindingsPage> {
  return apiGet<FindingsPage>(`${BASE}/${reviewId}/findings?page=${page}&size=${size}`)
}

/** 审批是图的一部分：提交后 Java 落审批记录并转发 Python resume，续跑事件仍在同一条流上。 */
export async function approveReview(
  reviewId: string,
  approvals: ApproveRequest[],
  operator: string,
): Promise<ApproveResponse> {
  // HTTP 头只能承载 ISO-8859-1：中文审批人姓名必须先编码，否则浏览器在 fetch 前就抛
  // "String contains non ISO-8859-1 code point"，请求根本发不出去（Java 侧对应解码）。
  return apiPost<ApproveResponse>(`${BASE}/${reviewId}/approve`, approvals, {
    'X-Operator': encodeURIComponent(operator),
  })
}

/**
 * 批注产物下载地址。走 Java 的 302 而不是事件的 download_url——
 * 事件里的 URL 是 MinIO 预签名地址（限时、且直连对象存储），Java 端点才是稳定入口。
 */
export function artifactUrl(reviewId: string): string {
  return `${BASE}/${reviewId}/artifact`
}

export type ConnectionState = 'connecting' | 'open' | 'reconnecting' | 'closed'

export interface ReviewStreamHandlers {
  onEvent: (event: AgentStreamEvent) => void
  onState?: (state: ConnectionState, attempt: number) => void
}

export interface ReviewStream {
  close: () => void
}

/**
 * 订阅事件流，带 seq 去重与指数退避重连。
 *
 * 两个必须自己做的原因：
 * 1) 去重：手动重建 EventSource 不会带 Last-Event-ID，Java 会从头全量回放，
 *    不去重就会重复渲染（同一 node.finished 计两次）。
 * 2) 重连：下游熔断时 stream 返回 503，浏览器默认重试不给用户任何信号；
 *    这里把状态透出界面，并按 1s→30s 退避。
 */
export function subscribeReview(reviewId: string, handlers: ReviewStreamHandlers): ReviewStream {
  let source: EventSource | null = null
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

  const open = () => {
    if (closed) return
    emit(attempt === 0 ? 'connecting' : 'reconnecting')
    const es = new EventSource(`${BASE}/${reviewId}/stream`)
    source = es

    es.onopen = () => {
      attempt = 0
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
      if (typeof event.seq === 'number') {
        if (event.seq <= lastSeq) return
        lastSeq = event.seq
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
      const delay = Math.min(30_000, 1000 * 2 ** Math.min(attempt - 1, 5))
      timer = setTimeout(open, delay)
    }
  }

  open()
  return { close }
}
