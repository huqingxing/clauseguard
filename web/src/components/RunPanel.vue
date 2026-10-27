<script setup lang="ts">
/**
 * 右栏：运行态（状态 / 待审批入口 / 节点进度 / 摘要 / 事件流）。
 *
 * 直接读 run store 而不是层层传 props：这里展示的就是「当前这一笔运行」的全部侧面，
 * 拆成 6 个 props 只会让父组件多写一遍映射，还得担心漏传。
 */
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useRunStore } from '../stores/run'
import EventFeed from './EventFeed.vue'
import RunSummary from './RunSummary.vue'
import { REVIEW_STATUS_LABEL, REVIEW_STATUS_TYPE } from '../types/api'

const run = useRunStore()
const route = useRoute()

const connectionLabel = computed(() => {
  // 终态后连接是「正常结束」而不是「断开」：写成断开会让用户以为出了问题。
  const finished = run.status === 'completed' || run.status === 'failed'
  if (finished && run.connection === 'closed') return '已结束'
  switch (run.connection) {
    case 'open':
      return '已连接'
    case 'connecting':
      return '连接中'
    case 'reconnecting':
      return `重连中（第 ${run.attempts} 次）`
    default:
      return '已断开'
  }
})

const connectionTone = computed(() => {
  if (run.connection === 'open') return 'ok'
  if (run.connection === 'reconnecting' || run.connection === 'connecting') return 'warn'
  const finished = run.status === 'completed' || run.status === 'failed'
  return finished ? 'ok' : 'idle'
})

const approveTo = computed(() => ({
  name: 'approve',
  params: { id: run.reviewId || String(route.params.id ?? '') },
}))

/** 节点太多会撑爆右栏：只展示最近 14 个，总数另给。 */
const recentNodes = computed(() => run.nodes.slice(-14))
const hiddenNodes = computed(() => Math.max(0, run.nodes.length - recentNodes.value.length))
const finishedCount = computed(() => run.nodes.filter((node) => node.state === 'finished').length)

function nodeDuration(duration?: number): string {
  if (duration === undefined) return ''
  return duration >= 1000 ? `${(duration / 1000).toFixed(1)}s` : `${duration}ms`
}
</script>

<template>
  <div class="run-panel">
    <div class="status-row">
      <el-tag :type="REVIEW_STATUS_TYPE[run.status]" size="small" effect="dark">
        {{ REVIEW_STATUS_LABEL[run.status] }}
      </el-tag>
      <span class="conn" :class="connectionTone">
        <i class="dot" />{{ connectionLabel }}
      </span>
      <span v-if="run.reviewId" class="review-id">{{ run.reviewId.slice(0, 8) }}</span>
    </div>

    <el-alert
      v-if="run.status === 'awaiting_approval'"
      type="warning"
      :closable="false"
      show-icon
      class="alert"
    >
      <template #title>{{ run.pendingItems.length }} 条高风险条款待人工审批</template>
      <router-link :to="approveTo">
        <el-button type="warning" size="small" style="margin-top: 8px">去审批</el-button>
      </router-link>
    </el-alert>

    <el-alert
      v-if="run.failure"
      type="error"
      :closable="false"
      show-icon
      class="alert"
      :title="`运行失败：${run.failure.message}`"
    />

    <div v-if="run.nodes.length" class="nodes">
      <div class="section-head">
        <span>节点进度</span>
        <el-text type="info" size="small">{{ finishedCount }} / {{ run.nodes.length }}</el-text>
      </div>
      <el-text v-if="hiddenNodes" type="info" size="small" class="muted">前 {{ hiddenNodes }} 个节点已省略</el-text>
      <ul class="node-list">
        <li v-for="node in recentNodes" :key="node.node" class="node" :class="node.state">
          <i class="dot" />
          <span class="node-name">{{ node.node }}</span>
          <span v-if="node.agent" class="node-agent">{{ node.agent }}</span>
          <span class="node-meta">{{ nodeDuration(node.duration_ms) }}</span>
          <span v-if="node.tokens" class="node-meta">{{ node.tokens }}t</span>
        </li>
      </ul>
    </div>

    <RunSummary :completed="run.completed" :review-id="run.reviewId" />

    <div class="feed-block">
      <div class="section-head">
        <span>事件流</span>
        <el-text type="info" size="small">{{ run.events.length }} 条</el-text>
      </div>
      <EventFeed :events="run.events" />
    </div>
  </div>
</template>

<style scoped>
.run-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-height: 0;
  height: 100%;
  overflow: auto;
}

.status-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.conn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.conn .dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--el-color-info);
}

.conn.ok .dot {
  background: var(--el-color-success);
}

.conn.warn .dot {
  background: var(--el-color-warning);
}

.conn.idle .dot {
  background: var(--el-text-color-placeholder);
}

.review-id {
  margin-left: auto;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11px;
  color: var(--el-text-color-placeholder);
}

.alert {
  padding: 8px 10px;
}

.section-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 6px;
  font-size: 13px;
  font-weight: 600;
}

.node-list {
  margin: 4px 0 0;
  padding: 0;
  list-style: none;
}

.node {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 2px 0;
  font-size: 12px;
  color: var(--el-text-color-regular);
}

.node .dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--el-text-color-placeholder);
  flex: 0 0 6px;
}

.node.running .dot {
  background: var(--el-color-primary);
  animation: pulse 1.2s ease-in-out infinite;
}

.node.finished .dot {
  background: var(--el-color-success);
}

.node.failed .dot {
  background: var(--el-color-danger);
}

.node-name {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}

.node-agent {
  color: var(--el-text-color-secondary);
}

.node-meta {
  margin-left: auto;
  color: var(--el-text-color-placeholder);
  font-size: 11px;
}

.muted {
  color: var(--el-text-color-placeholder);
}

.feed-block {
  margin-top: auto;
}

@keyframes pulse {
  50% {
    opacity: 0.25;
  }
}
</style>
