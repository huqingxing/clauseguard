<script setup lang="ts">
/**
 * 右栏事件流。
 *
 * 只做两件事：把事件按 seq 顺序铺开、贴近底部时自动跟随。
 * 用户往上翻看历史时不再自动滚动（否则看不到刚翻到的那条）——这是事件流最基本的礼貌。
 */
import { computed, nextTick, ref, watch } from 'vue'
import type { AgentStreamEvent } from '../types/events'
import { isInterrupt, isNodeEvent, isRunCompleted, isRunFailed } from '../types/events'

const props = defineProps<{ events: AgentStreamEvent[] }>()

const scroller = ref<HTMLElement | null>(null)
const pinned = ref(true)

const LABEL: Record<string, string> = {
  'run.started': '开始',
  'run.completed': '完成',
  'run.failed': '失败',
  'node.started': '节点开始',
  'node.finished': '节点结束',
  'node.failed': '节点失败',
  'message.delta': '输出',
  interrupt: '中断',
  'tool.call': '工具调用',
  'tool.result': '工具返回',
}

const rows = computed(() =>
  props.events.map((event) => {
    const label = LABEL[event.type] ?? event.type
    let detail = ''
    let tone: 'normal' | 'good' | 'warn' | 'bad' = 'normal'

    if (isNodeEvent(event)) {
      detail = [event.node, event.agent, event.duration_ms ? `${event.duration_ms}ms` : '', event.message]
        .filter(Boolean)
        .join(' · ')
      if (event.type === 'node.failed') tone = 'bad'
    } else if (isRunCompleted(event)) {
      detail = `${event.findings_count} 条发现 · ${event.usage.input_tokens + event.usage.output_tokens} tokens`
      tone = 'good'
    } else if (isInterrupt(event)) {
      // 提交者看到的是「有几条在等人拍板」，不是「我的待办」：措辞不能滑成审批者的口吻。
      detail = `${event.reason} · ${event.payload?.items?.length ?? 0} 条条款等待法务审批`
      tone = 'warn'
    } else if (isRunFailed(event)) {
      detail = event.message ?? event.code ?? ''
      tone = 'bad'
    } else if (event.type === 'message.delta') {
      const delta = event as { text?: string; agent?: string }
      detail = `${delta.agent ?? ''} ${delta.text ?? ''}`.trim().slice(0, 120)
    } else {
      detail = String((event as { node?: string; agent?: string }).node ?? (event as { agent?: string }).agent ?? '')
    }

    return { seq: event.seq, type: event.type, label, detail, tone }
  }),
)

function onScroll() {
  const element = scroller.value
  if (!element) return
  // 距底部 32px 内视为「贴底」，继续自动跟随；否则认为用户在翻历史。
  pinned.value = element.scrollHeight - element.scrollTop - element.clientHeight < 32
}

watch(
  () => props.events.length,
  async () => {
    if (!pinned.value) return
    await nextTick()
    const element = scroller.value
    if (element) element.scrollTop = element.scrollHeight
  },
)
</script>

<template>
  <div ref="scroller" class="feed" @scroll.passive="onScroll">
    <p v-if="!rows.length" class="empty">等待事件…</p>
    <div v-for="row in rows" :key="row.seq" class="row" :class="row.tone">
      <span class="seq">#{{ row.seq }}</span>
      <span class="label">{{ row.label }}</span>
      <span class="detail">{{ row.detail }}</span>
    </div>
  </div>
</template>

<style scoped>
.feed {
  height: 100%;
  max-height: 260px;
  overflow: auto;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11px;
  line-height: 1.7;
}

.empty {
  margin: 0;
  color: var(--el-text-color-placeholder);
}

.row {
  display: grid;
  grid-template-columns: 40px 62px minmax(0, 1fr);
  gap: 6px;
  padding: 1px 0;
  color: var(--el-text-color-regular);
}

.seq {
  color: var(--el-text-color-placeholder);
}

.label {
  color: var(--el-text-color-secondary);
}

.detail {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.row.good .label {
  color: var(--el-color-success);
}

.row.warn .label {
  color: var(--el-color-warning);
}

.row.bad .label {
  color: var(--el-color-danger);
}
</style>
