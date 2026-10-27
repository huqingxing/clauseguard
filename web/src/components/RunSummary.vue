<script setup lang="ts">
/**
 * 运行摘要（Stage 11 建立，Stage 12 改为经 Java 取产物）。
 * 报告必须写明「本次是 full 还是 incremental、复用了多少条」。
 * 没有 incremental 字段时按 full 展示而不是留白——留白会让人分不清「全量」与「没数据」。
 */
import { computed } from 'vue'
import { artifactUrl } from '../api/reviews'
import type { RunCompletedEvent } from '../types/events'

const props = defineProps<{
  completed: RunCompletedEvent | null
  /** 传 reviewId 即走 Java 的 302 端点（稳定入口），否则退回事件里的预签名地址。 */
  reviewId?: string
}>()

const mode = computed(() => props.completed?.incremental?.mode ?? 'full')
const isIncremental = computed(() => mode.value === 'incremental')
const tokenTotal = computed(() => {
  const usage = props.completed?.usage
  return usage ? usage.input_tokens + usage.output_tokens : 0
})
const hitRate = computed(() => props.completed?.incremental?.cache_hit_rate ?? 0)

const artifactHref = computed(() => {
  if (!props.completed?.artifact) return ''
  if (props.reviewId) return artifactUrl(props.reviewId)
  return props.completed.artifact.download_url
})

const rows = computed(() => {
  const event = props.completed
  if (!event) return []
  const info = event.incremental
  return [
    { label: '本次模式', value: isIncremental.value ? '增量重审（仅变更条款）' : '全量重审' },
    { label: '基线版本', value: isIncremental.value ? `v${info?.baseline_version ?? 0}` : '—' },
    {
      label: '变更 / 新增 / 删除',
      value: isIncremental.value
        ? `${info?.changed_count ?? 0} / ${info?.added_count ?? 0} / ${info?.removed_count ?? 0}`
        : '—',
    },
    { label: '复用条数', value: `${info?.reused_findings ?? 0} 条（未消耗 token）` },
    { label: '缓存命中率', value: `${(hitRate.value * 100).toFixed(1)}%` },
    { label: 'stale 历史结论', value: `${info?.stale_findings ?? 0} 条（保留追溯，不进报告）` },
    { label: '发现数（不含 stale）', value: `${event.findings_count} 条` },
    {
      label: 'token 消耗',
      value: `${tokenTotal.value}（输入 ${event.usage.input_tokens} / 输出 ${event.usage.output_tokens}）`,
    },
    { label: '估算成本', value: `¥${event.usage.cost_cny.toFixed(4)}` },
    {
      label: '校验通过率',
      value: `${(event.quality.verified_rate * 100).toFixed(1)}%（打回 ${event.quality.rework_count} 轮）`,
    },
  ]
})
</script>

<template>
  <div v-if="completed" class="summary">
    <div class="head">
      <span class="title">运行摘要</span>
      <el-tag :type="isIncremental ? 'warning' : 'info'" effect="dark" size="small">
        {{ isIncremental ? 'incremental' : 'full' }}
      </el-tag>
      <el-tag v-if="completed.degraded" type="danger" size="small" effect="plain">已降级</el-tag>
    </div>

    <el-descriptions :column="1" border size="small">
      <el-descriptions-item v-for="row in rows" :key="row.label" :label="row.label">
        {{ row.value }}
      </el-descriptions-item>
    </el-descriptions>

    <div v-if="completed.artifact" class="artifact">
      <el-link :href="artifactHref" type="primary" target="_blank">
        下载带批注的 Word（{{ completed.artifact.comments_count }} 条批注）
      </el-link>
    </div>
  </div>
</template>

<style scoped>
.head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.title {
  font-size: 13px;
  font-weight: 600;
}

.artifact {
  margin-top: 10px;
}
</style>
