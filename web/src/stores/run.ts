/**
 * 一次审查运行的实时状态（Stage 12 工作台的单一数据源）。
 *
 * 为什么放 store 而不是组件里：同一笔运行会在三个页面出现——工作台（看原文+流）、
 * 审查详情（看结果）、审批页（手机上逐条批）。路由切换会卸载组件，放组件里就得每次重连
 * 重放；放 store 后切页面只切视图，流不断。
 */

import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import {
  getReview,
  subscribeReview,
  type ConnectionState,
  type ReviewStream,
} from '../api/reviews'
import type { Finding, ReviewResult, ReviewStatus } from '../types/api'
import type { AgentStreamEvent, InterruptEvent, RunCompletedEvent } from '../types/events'
import { isInterrupt, isNodeEvent, isRunCompleted, isRunFailed } from '../types/events'

export interface NodeTrace {
  node: string
  agent?: string
  state: 'running' | 'finished' | 'failed'
  duration_ms?: number
  tokens?: number
  message?: string
}

export interface RunFailure {
  code?: string
  message: string
}

/**
 * 「连不上服务」的判定阈值（重连次数）。
 *
 * 为什么是 3：退避间隔 1s→2s→4s，第 3 次失败时已经过去约 7 秒。取 1 会把「请求正好撞上重启」
 * 误报成故障；取 5 要等 31 秒才给提示，而用户通常在第 10 秒就自己刷新页面了——
 * 提示必须赶在那之前出现，否则等于没有。
 */
const OFFLINE_ATTEMPTS = 3

export const useRunStore = defineStore('run', () => {
  const reviewId = ref('')
  const connection = ref<ConnectionState>('closed')
  const attempts = ref(0)
  const events = ref<AgentStreamEvent[]>([])
  const completed = ref<RunCompletedEvent | null>(null)
  const interrupt = ref<InterruptEvent | null>(null)
  const failure = ref<RunFailure | null>(null)
  const snapshot = ref<ReviewResult | null>(null)
  const snapshotLoading = ref(false)

  let stream: ReviewStream | null = null

  /** 终态以事件为准；未收到终态时退回快照里的服务端状态。 */
  const status = computed<ReviewStatus>(() => {
    if (failure.value) return 'failed'
    if (completed.value) return 'completed'
    if (interrupt.value) return 'awaiting_approval'
    if (events.value.length || connection.value === 'open') return 'running'
    return snapshot.value?.status ?? 'queued'
  })

  /** 明细优先取事件（实时且完整），其次取快照（刷新后重放前先有内容）。 */
  const findings = computed<Finding[]>(() => completed.value?.findings ?? snapshot.value?.findings ?? [])

  const pendingItems = computed(() => interrupt.value?.payload?.items ?? [])

  /**
   * 服务不可达：重连若干次仍然一条事件都没收到。
   *
   * 为什么需要它：`failure` 只在收到 run.failed 事件时才置位，而「后端根本没起来」
   * 时永远不会收到事件——界面就一直停在「排队中 / 重连中（第 N 次）」，
   * 不刷新、不说原因，看上去像卡死。这里把这种状态显式暴露出去。
   */
  const offline = computed(
    () =>
      attempts.value >= OFFLINE_ATTEMPTS &&
      connection.value !== 'open' &&
      !completed.value &&
      !failure.value,
  )

  const nodes = computed<NodeTrace[]>(() => {
    const byNode = new Map<string, NodeTrace>()
    for (const event of events.value) {
      if (!isNodeEvent(event) || !event.node) continue
      if (event.type === 'node.started') {
        byNode.set(event.node, { node: event.node, agent: event.agent, state: 'running' })
      } else if (event.type === 'node.finished') {
        byNode.set(event.node, {
          node: event.node,
          agent: event.agent,
          state: 'finished',
          duration_ms: event.duration_ms,
          tokens: event.tokens,
        })
      } else {
        byNode.set(event.node, {
          node: event.node,
          agent: event.agent,
          state: 'failed',
          message: event.message ?? event.code,
        })
      }
    }
    return Array.from(byNode.values())
  })

  function reset(nextId: string) {
    reviewId.value = nextId
    events.value = []
    completed.value = null
    interrupt.value = null
    failure.value = null
    snapshot.value = null
  }

  /**
   * 供测试与「换一笔审查」使用：清空全部运行态。
   * lastSeq 在 subscribeReview 内部按实例维护，重建实例即重置。
   */
  function stop() {
    stream?.close()
    stream = null
    connection.value = 'closed'
    attempts.value = 0
  }

  async function refreshSnapshot(id = reviewId.value) {
    if (!id) return
    snapshotLoading.value = true
    try {
      snapshot.value = await getReview(id)
    } catch {
      // 快照只用于首屏与兜底：拿不到不阻断实时流（流本身能重建全部内容）。
      snapshot.value = null
    } finally {
      snapshotLoading.value = false
    }
  }

  /**
   * 接管一笔运行：幂等。同一 reviewId 且连接仍活着时直接返回，
   * 避免「工作台 → 审批页 → 工作台」来回切时反复全量重放。
   */
  async function attach(id: string) {
    if (reviewId.value === id && (connection.value === 'open' || connection.value === 'connecting')) {
      return
    }
    stop()
    reset(id)
    void refreshSnapshot(id)

    stream = subscribeReview(id, {
      onEvent: (event) => {
        events.value.push(event)
        if (isRunCompleted(event)) {
          completed.value = event
          interrupt.value = null
        } else if (isInterrupt(event)) {
          interrupt.value = event
        } else if (isRunFailed(event)) {
          failure.value = { code: event.code, message: event.message ?? '运行失败' }
        }
      },
      onState: (state, attempt) => {
        connection.value = state
        attempts.value = attempt
      },
    })
  }

  /** 审批后 Java 会把状态切回 running 并续跑，同一 store 上重置终态标记即可继续收流。 */
  function markResumed() {
    interrupt.value = null
    failure.value = null
    completed.value = null
  }

  return {
    reviewId,
    connection,
    attempts,
    events,
    completed,
    interrupt,
    failure,
    snapshot,
    snapshotLoading,
    status,
    findings,
    pendingItems,
    offline,
    nodes,
    attach,
    stop,
    refreshSnapshot,
    markResumed,
    reset,
  }
})
