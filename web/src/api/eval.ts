/** 评测看板客户端（Stage 12）：聚合口径在 Java 侧，前端只展示。 */
import type { EvalSummary } from '../types/api'
import { apiGet } from './http'

export function getEvalSummary(): Promise<EvalSummary> {
  return apiGet<EvalSummary>('/api/v1/eval/summary')
}
