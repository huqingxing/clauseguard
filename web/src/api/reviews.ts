/**
 * 审查任务的 REST + SSE 客户端。
 *
 * 红线：只调 Java（/api/v1/reviews，vite 已代理到 8080），绝不直连 Python 8000。
 * 事件语义不在前端重新解释——原样解析后交给展示层，字段严格来自事件契约。
 */

import type { AgentStreamEvent } from '../types/events'
import type { ReviewMode } from '../types/events'

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

const BASE = '/api/v1/reviews'

export async function createReview(input: CreateReviewInput): Promise<CreateReviewResult> {
  const body: Record<string, unknown> = {
    contract_id: input.contract_id,
    version: input.version,
    mode: input.mode,
  }
  // base_version 只在显式指定时下发：0 会让 Java/编排层按 version-1 推断，
  // 显式传 0 与「让它推断」在语义上不同，故省略即推断。
  if (input.base_version !== undefined) body.base_version = input.base_version

  const response = await fetch(BASE, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!response.ok) {
    throw new Error(`创建审查任务失败: HTTP ${response.status}`)
  }
  return (await response.json()) as CreateReviewResult
}

/**
 * 订阅事件流。SSE 的 data 行即事件 JSON；断线由浏览器 EventSource 自动重连，
 * 重连带上 Last-Event-ID 走 Java 的重放逻辑（seq 续接，不重复投递）。
 */
export function subscribeReview(
  reviewId: string,
  onEvent: (event: AgentStreamEvent) => void,
  onError: (error: Event) => void,
): () => void {
  const source = new EventSource(`${BASE}/${reviewId}/stream`)
  source.onmessage = (message) => {
    try {
      onEvent(JSON.parse(message.data) as AgentStreamEvent)
    } catch {
      // 单条坏帧不应中断整条流（事件流允许偶发解析失败，重放存储仍是事实源）。
    }
  }
  source.onerror = onError
  return () => source.close()
}
