/**
 * 审查任务的 REST + SSE 客户端（Stage 12）。
 *
 * 红线：只调 Java（/api/v1/reviews，vite 已代理到 8080），绝不直连 Python 8000。
 * 事件语义不在前端重新解释——原样解析后交给展示层，字段严格来自 events.schema.json。
 */

import type {
  ApproveRequest,
  ApproveResponse,
  FindingsPage,
  ReviewMode,
  ReviewResult,
} from '../types/api'
import { apiGet, apiPost } from './http'

const BASE = '/api/v1/reviews'

/**
 * 订阅逻辑住在 stream.ts：它要在 Node 里被直接测（注入假 EventSource），
 * 而这个文件拖着 http.ts 的运行时依赖，测试没法干净地 import。
 * 这里只做转发，调用方（stores/run.ts）的 import 路径不变。
 */
export { subscribeReview } from './stream'
export type { ConnectionState, ReviewStream, ReviewStreamHandlers } from './stream'

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

/**
 * 审批是图的一部分：提交后 Java 落审批记录并转发 Python resume，续跑事件仍在同一条流上。
 *
 * 审批写入入口已迁到 /api/v1/admin/reviews（需 ADMIN 角色），http.ts 的 ADMIN_PATH 会自动
 * 附加 Basic 凭证。操作人不再由前端传：后端取认证主体（登录账号）写入审批记录，
 * 避免「客户端自称谁就能以谁名义签字」的审计漏洞。
 */
export async function approveReview(
  reviewId: string,
  approvals: ApproveRequest[],
): Promise<ApproveResponse> {
  return apiPost<ApproveResponse>(`/api/v1/admin/reviews/${reviewId}/approve`, approvals)
}

/**
 * 批注产物下载地址。走 Java 的 302 而不是事件的 download_url——
 * 事件里的 URL 是 MinIO 预签名地址（限时、且直连对象存储），Java 端点才是稳定入口。
 */
export function artifactUrl(reviewId: string): string {
  return `${BASE}/${reviewId}/artifact`
}
