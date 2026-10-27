/**
 * REST 契约（contracts/openapi.yaml）的 TS 镜像。
 *
 * 字段名保持 snake_case，不做驼峰转换：转换层会和契约漂移，出错时反而找不到源头。
 * 只声明前端真正会读的字段——契约里 schema 可选（无 required）的字段这里一律标 `?`，
 * 让「后端没填」在类型上显式可见，而不是假装有值。
 */

export type RiskLevel = 'low' | 'medium' | 'high' | 'unverified'

export type ReviewStatus = 'queued' | 'running' | 'awaiting_approval' | 'completed' | 'failed'

export type ReviewMode = 'full' | 'incremental'

export type ApprovalDecision = 'approve' | 'reject' | 'edit'

export interface Citation {
  source_id: string
  title: string
  excerpt: string
  url?: string | null
}

/** 与 events.schema.json#/$defs/Finding 严格同构（run.completed.findings 是唯一事实源）。 */
export interface Finding {
  clause_id: string
  agent: string
  risk_type: string
  risk_level: RiskLevel
  summary: string
  suggestion?: string
  evidence?: string
  stale?: boolean
  citations?: Citation[]
}

export interface LastReview {
  review_id: string
  status: ReviewStatus
  /** 后端当前只在详情接口填这两项；列表接口可能为 null（契约里非 required）。 */
  mode?: ReviewMode | null
  findings_count?: number | null
}

export interface ContractSummary {
  contract_id: string
  filename: string
  version: number
  created_at: string
  last_review?: LastReview | null
}

export interface ContractDetail {
  contract_id: string
  filename: string
  version: number
  content_type: string
  size_bytes: number
  created_at: string
  content_url?: string
  last_review?: LastReview | null
}

export interface ContractDiff {
  contract_id: string
  baseline_version: number
  target_version: number
  changed: string[]
  added: string[]
  removed: string[]
  stale_findings?: number
}

export interface ReviewResult {
  review_id: string
  contract_id: string
  version?: number
  run_id?: string | null
  status: ReviewStatus
  created_at: string
  completed_at?: string | null
  findings: Finding[]
}

export interface FindingsPage {
  items: Finding[]
  page: number
  size: number
  total: number
}

/** 审批提交项：contract_version / content_hash 必须来自 interrupt 事件的快照（乐观锁）。 */
export interface ApproveRequest {
  clause_id: string
  decision: ApprovalDecision
  comment?: string
  edited_text?: string | null
  contract_version: string
  content_hash: string
}

export interface ApproveResponse {
  status: 'accepted' | 'conflict'
  decided_at: string
}

export interface EvalSummary {
  total_reviews: number
  completed_reviews: number
  failed_reviews: number
  totals: {
    input_tokens: number
    output_tokens: number
    cost_cny: number
    findings: number
  }
  averages: {
    verified_rate: number
    rework_count: number
    duration_ms: number
  }
  incremental?: {
    runs: number
    full_equivalent_tokens: number
    incremental_tokens: number
    saved_ratio: number
  }
}

export interface AdminHealth {
  java: { status: 'up' | 'down' }
  python: { status: 'up' | 'down'; detail?: Record<string, unknown> | null }
}

/** 待审批队列的一行（Stage 5，契约 ApprovalQueueItem）。 */
export interface ApprovalQueueItem {
  review_id: string
  contract_id: string
  contract_title: string
  /** 待审的高风险条款数（后端已按 clause_id 去重，不是风险点数）。 */
  risk_high_count: number
  created_at: string
  /** 进入待审批的时间：等待时长按它算，不按 created_at。 */
  waiting_since: string
}

export interface ApprovalQueuePage {
  items: ApprovalQueueItem[]
  page: number
  size: number
  total: number
}

export const RISK_LABEL: Record<RiskLevel, string> = {
  high: '高风险',
  medium: '中风险',
  low: '低风险',
  unverified: '引用待核',
}

/** 高风险红、中风险橙、低风险蓝、unverified 灰（引用校验降级不等于没风险）。 */
export const RISK_TAG_TYPE: Record<RiskLevel, 'danger' | 'warning' | 'primary' | 'info'> = {
  high: 'danger',
  medium: 'warning',
  low: 'primary',
  unverified: 'info',
}

export const REVIEW_STATUS_LABEL: Record<ReviewStatus, string> = {
  queued: '排队中',
  running: '审查中',
  awaiting_approval: '等待法务审批',
  completed: '已完成',
  failed: '失败',
}

export const REVIEW_STATUS_TYPE: Record<ReviewStatus, 'info' | 'warning' | 'danger' | 'success'> = {
  queued: 'info',
  running: 'info',
  awaiting_approval: 'warning',
  completed: 'success',
  failed: 'danger',
}
