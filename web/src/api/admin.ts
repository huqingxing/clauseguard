/**
 * 运维健康客户端（Stage 12）。
 *
 * 前端不直连 Python：Python 的 /healthz 由 Java 代理探测后聚合返回，
 * 一屏看全双栈。任一依赖 down 也是 200，由 body 表达——不做「HTTP 码即状态」的耦合。
 */
import type { AdminHealth, ApprovalQueuePage } from '../types/api'
import { apiGet } from './http'

export function getAdminHealth(): Promise<AdminHealth> {
  return apiGet<AdminHealth>('/api/v1/admin/health')
}

/**
 * 待审批队列（Stage 5）。路径在 /api/v1/admin 下，凭证由 http.ts 统一附加，
 * 这里不碰 Authorization——否则每个 admin 调用点都要各写一遍，漏一个就是 401。
 */
export function getApprovalQueue(page = 0, size = 20): Promise<ApprovalQueuePage> {
  const params = new URLSearchParams({ page: String(page), size: String(size) })
  return apiGet<ApprovalQueuePage>(`/api/v1/admin/approvals?${params}`)
}
