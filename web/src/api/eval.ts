/** 评测看板客户端（Stage 12）：聚合口径在 Java 侧，前端只展示。
 * 入口已迁到 /api/v1/admin/eval（需 ADMIN 角色），http.ts 的 ADMIN_PATH 自动附凭证。 */
import type { EvalSummary } from '../types/api'
import { apiGet } from './http'

export function getEvalSummary(): Promise<EvalSummary> {
  return apiGet<EvalSummary>('/api/v1/admin/eval/summary')
}
