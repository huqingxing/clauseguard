/**
 * 运维健康客户端（Stage 12）。
 *
 * 前端不直连 Python：Python 的 /healthz 由 Java 代理探测后聚合返回，
 * 一屏看全双栈。任一依赖 down 也是 200，由 body 表达——不做「HTTP 码即状态」的耦合。
 */
import type { AdminHealth } from '../types/api'
import { apiGet } from './http'

export function getAdminHealth(): Promise<AdminHealth> {
  return apiGet<AdminHealth>('/api/v1/admin/health')
}
