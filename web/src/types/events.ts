/**
 * 事件契约的 TS 镜像（单一事实源是 contracts/events.schema.json）。
 *
 * 为什么手写一份而不是生成：版本表未引入 JSON Schema -> TS 的代码生成器（铁律 5），
 * 而前端只需要消费其中 3 个事件。字段与 schema 一一对应，改 schema 必须同步改这里；
 * 宁可少字段也不加 schema 里没有的字段（铁律 3：禁止私加字段）。
 */

import type { Finding, ReviewMode } from './api'

export type ClauseChangeStatus = 'changed' | 'added' | 'removed' | 'unchanged'

/** run.completed.incremental：本次运行的增量账本（Stage 11）。 */
export interface IncrementalInfo {
  mode: ReviewMode
  baseline_version?: number
  changed_count?: number
  added_count?: number
  removed_count?: number
  unchanged_count?: number
  reused_findings: number
  stale_findings?: number
  cache_hits?: number
  cache_misses?: number
  cache_hit_rate: number
  changed_clause_ids?: string[]
  added_clause_ids?: string[]
  removed_clause_ids?: string[]
}

export interface Usage {
  input_tokens: number
  output_tokens: number
  cost_cny: number
}

export interface Quality {
  verified_rate: number
  rework_count: number
  unverified_count: number
}

/** run.completed.degraded_detail：部分条款没审成时的明细（模型不可用/输出不可解析）。 */
export interface DegradedDetail {
  /** 一条都没审成的条款数（按条款去重，同一条款任一专家审成即不计入）。 */
  unreviewed_clauses: number
  /** 模型调用失败的审查单元数（条款×专家），用于区分调不通与输出不可解析。 */
  llm_errors: number
}

export interface Artifact {
  object_key: string
  download_url: string
  comments_count: number
  size_bytes: number
}

export interface RunCompletedEvent {
  seq: number
  ts: string
  type: 'run.completed'
  usage: Usage
  findings_count: number
  /** Stage 12：明细随事件下发（已剔除 stale），工作台据此直接渲染，免去再拉一次 REST。 */
  findings?: Finding[]
  degraded?: boolean
  /** 有条款没审成时才出现；缺省即「本次全部条款都审成了」。 */
  degraded_detail?: DegradedDetail | null
  quality: Quality
  artifact?: Artifact | null
  incremental?: IncrementalInfo | null
}

export interface InterruptItem {
  clause_id: string
  /**
   * 旧版 event：一条 finding 一条 item，单值风险类型。
   * 事件存储里还躺着这类历史快照，不能当它不存在。
   */
  risk_type?: string
  /**
   * 新版 event：同一条款的多条 finding 已在 Python 侧按 clause_id 聚合，
   * 这里给出全部风险类型（保序去重）。
   */
  risk_types?: string[]
  risk: string
  suggestion: string
  /** 聚合前的 finding 条数；旧版 event 没有这个字段。 */
  findings_merged?: number
  clause_text: string
}

export interface InterruptEvent {
  seq: number
  ts: string
  type: 'interrupt'
  reason: string
  payload?: {
    reason?: string
    contract_version?: number
    content_hash?: string
    items?: InterruptItem[]
  }
}

export interface NodeEvent {
  seq: number
  ts: string
  type: 'node.started' | 'node.finished' | 'node.failed'
  node?: string
  agent?: string
  duration_ms?: number
  tokens?: number
  code?: string
  message?: string
}

export interface MessageDeltaEvent {
  seq: number
  ts: string
  type: 'message.delta'
  agent?: string
  text?: string
}

export type AgentStreamEvent =
  | RunCompletedEvent
  | InterruptEvent
  | NodeEvent
  | MessageDeltaEvent
  | { seq: number; ts: string; type: string }

/**
 * 类型守卫：联合里有 `{ type: string }` 兜底成员，直接比较 type 无法把类型收窄到具体事件，
 * 只剩 `as` 一条路。集中在这里做一次断言，避免每个消费点各自 `as`（漂移风险更高）。
 */
export function isRunCompleted(event: AgentStreamEvent): event is RunCompletedEvent {
  return event.type === 'run.completed'
}

export function isInterrupt(event: AgentStreamEvent): event is InterruptEvent {
  return event.type === 'interrupt'
}

export function isNodeEvent(event: AgentStreamEvent): event is NodeEvent {
  return event.type === 'node.started' || event.type === 'node.finished' || event.type === 'node.failed'
}

export interface RunFailedEvent {
  seq: number
  ts: string
  type: 'run.failed'
  code?: string
  message?: string
}

export function isRunFailed(event: AgentStreamEvent): event is RunFailedEvent {
  return event.type === 'run.failed'
}

export interface ClauseRow {
  clause_id: string
  /** 条款编号（跨版本的稳定身份）；无编号时回退为空串。 */
  no: string
  text: string
  status: ClauseChangeStatus
  /** 该条款在本轮是否命中缓存复用（未花 token）。 */
  reused: boolean
  /** 历史结论所属条款已被删除：仅追溯，不进报告。 */
  stale: boolean
  risk_level?: string
}

/**
 * 从 run.completed.incremental 推导每条条款的变更状态。
 * 纯函数（可直接单测）：ID 命中 changed/added/removed 三类之一即取该类，否则 unchanged。
 */
export function clauseStatusOf(clauseId: string, info: IncrementalInfo | null | undefined): ClauseChangeStatus {
  if (!info || info.mode !== 'incremental') return 'unchanged'
  if (info.changed_clause_ids?.includes(clauseId)) return 'changed'
  if (info.added_clause_ids?.includes(clauseId)) return 'added'
  if (info.removed_clause_ids?.includes(clauseId)) return 'removed'
  return 'unchanged'
}

export const STATUS_LABEL: Record<ClauseChangeStatus, string> = {
  changed: '已修改',
  added: '新增',
  removed: '已删除',
  unchanged: '未变更',
}

/** 高风险用红、中风险用橙（与批注高亮同语义），stale 灰显。 */
export const STATUS_TYPE: Record<ClauseChangeStatus, 'danger' | 'warning' | 'info' | 'success'> = {
  changed: 'danger',
  added: 'warning',
  removed: 'info',
  unchanged: 'success',
}
