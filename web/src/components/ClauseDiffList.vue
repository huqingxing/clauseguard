<script setup lang="ts">
/**
 * 变更条款列表（Stage 11）：按 diff 结果高亮 changed/added/removed，
 * stale 的历史结论灰显但仍展示——红线要求「保留可追溯」，不是从界面上消失。
 */
import { computed } from 'vue'
import type { ClauseRow } from '../types/events'
import { STATUS_LABEL, STATUS_TYPE } from '../types/events'

const props = defineProps<{
  rows: ClauseRow[]
  incremental: boolean
}>()

const changedRows = computed(() => props.rows.filter((row) => row.status !== 'unchanged'))
const highlightHint = computed(() =>
  props.incremental
    ? `本轮仅重审 ${changedRows.value.length} 条变更条款，其余走缓存复用`
    : '本轮为全量重审（未查缓存），全部条款均已重新审查',
)
</script>

<template>
  <el-card shadow="never">
    <template #header>
      <div class="header">
        <span>条款变更</span>
        <el-text type="info" size="small">{{ highlightHint }}</el-text>
      </div>
    </template>

    <el-empty v-if="!rows.length" description="暂无条款" />

    <el-scrollbar v-else max-height="420px">
      <div
        v-for="row in rows"
        :key="row.clause_id"
        class="clause-row"
        :class="[`status-${row.status}`, { stale: row.stale, reused: row.reused }]"
      >
        <div class="clause-meta">
          <el-tag :type="STATUS_TYPE[row.status]" size="small" effect="dark">
            {{ STATUS_LABEL[row.status] }}
          </el-tag>
          <span class="clause-no">{{ row.no || row.clause_id }}</span>
          <el-tag v-if="row.reused" type="info" size="small" effect="plain">缓存复用</el-tag>
          <el-tag v-if="row.stale" type="info" size="small" effect="plain">历史结论（条款已删除）</el-tag>
          <el-tag v-if="row.risk_level === 'high'" type="danger" size="small" effect="plain">
            高风险
          </el-tag>
        </div>
        <p class="clause-text">{{ row.text }}</p>
      </div>
    </el-scrollbar>
  </el-card>
</template>

<style scoped>
.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.clause-row {
  padding: 10px 12px;
  margin-bottom: 8px;
  border-left: 4px solid transparent;
  border-radius: 4px;
  background: var(--el-fill-color-light);
}

/* 变更=红、新增=橙、删除=灰蓝、未变更=绿：与批注高亮的语义一致。 */
.status-changed {
  border-left-color: var(--el-color-danger);
  background: var(--el-color-danger-light-9);
}

.status-added {
  border-left-color: var(--el-color-warning);
  background: var(--el-color-warning-light-9);
}

.status-removed {
  border-left-color: var(--el-color-info);
  background: var(--el-color-info-light-9);
}

.status-unchanged {
  border-left-color: var(--el-color-success-light-5);
}

/* stale 只作追溯展示：整体灰显，明确「不在本轮报告里」。 */
.clause-row.stale {
  opacity: 0.55;
  text-decoration: line-through;
}

.clause-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}

.clause-no {
  font-weight: 600;
  font-size: 13px;
}

.clause-text {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--el-text-color-regular);
  word-break: break-all;
}
</style>
