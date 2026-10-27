<script setup lang="ts">
/**
 * 管理端 `/eval`（独立入口 admin.html#eval）：评测看板。
 *
 * 数据来自 review_event 的聚合（不建新表），所以口径必须写在页面上：
 * 这些是「已跑过的审查」的真实用量，不是离线评测集（后者在 docs/benchmark.md）。
 * 两者混着看会得出错误结论，界面上就不该让人混。
 */
import { computed, onMounted, ref } from 'vue'
import { getEvalSummary } from '../api/eval'
import type { EvalSummary } from '../types/api'
import { describeApiError } from '../utils/error'

const summary = ref<EvalSummary | null>(null)
const loading = ref(false)
const error = ref('')

async function load() {
  loading.value = true
  error.value = ''
  try {
    summary.value = await getEvalSummary()
  } catch (err) {
    error.value = describeApiError(err, '加载评测汇总失败')
  } finally {
    loading.value = false
  }
}

const successRate = computed(() => {
  const data = summary.value
  if (!data || !data.total_reviews) return 0
  return data.completed_reviews / data.total_reviews
})

const avgCost = computed(() => {
  const data = summary.value
  if (!data || !data.completed_reviews) return 0
  return data.totals.cost_cny / data.completed_reviews
})

const avgTokens = computed(() => {
  const data = summary.value
  if (!data || !data.completed_reviews) return 0
  return Math.round((data.totals.input_tokens + data.totals.output_tokens) / data.completed_reviews)
})

onMounted(load)
</script>

<template>
  <div class="page">
    <header class="page-head">
      <div>
        <h1>评测看板</h1>
        <p class="sub">口径：聚合已发生的审查运行（review_event），不含离线评测集</p>
      </div>
      <el-button size="small" :loading="loading" @click="load">刷新</el-button>
    </header>

    <el-alert v-if="error" type="error" :closable="false" show-icon :title="error" />

    <div v-loading="loading" class="content">
      <div v-if="summary" class="cards">
        <div class="metric">
          <span class="metric-label">审查总数</span>
          <span class="metric-value">{{ summary.total_reviews }}</span>
          <span class="metric-foot">
            完成 {{ summary.completed_reviews }} · 失败 {{ summary.failed_reviews }} ·
            成功率 {{ (successRate * 100).toFixed(1) }}%
          </span>
        </div>
        <div class="metric">
          <span class="metric-label">累计发现</span>
          <span class="metric-value">{{ summary.totals.findings }}</span>
          <span class="metric-foot">已完成运行汇总（不含 stale）</span>
        </div>
        <div class="metric">
          <span class="metric-label">单次平均 token</span>
          <span class="metric-value">{{ avgTokens.toLocaleString() }}</span>
          <span class="metric-foot">
            输入 {{ summary.totals.input_tokens.toLocaleString() }} / 输出
            {{ summary.totals.output_tokens.toLocaleString() }}
          </span>
        </div>
        <div class="metric">
          <span class="metric-label">单次平均成本</span>
          <span class="metric-value">¥{{ avgCost.toFixed(4) }}</span>
          <span class="metric-foot">累计 ¥{{ summary.totals.cost_cny.toFixed(4) }}</span>
        </div>
        <div class="metric">
          <span class="metric-label">校验通过率</span>
          <span class="metric-value">{{ (summary.averages.verified_rate * 100).toFixed(1) }}%</span>
          <span class="metric-foot">Critic 打回 {{ summary.averages.rework_count.toFixed(1) }} 轮/次</span>
        </div>
        <div class="metric">
          <span class="metric-label">平均时长</span>
          <span class="metric-value">{{ (summary.averages.duration_ms / 1000).toFixed(1) }}s</span>
          <span class="metric-foot">各节点 duration_ms 之和的均值</span>
        </div>
      </div>

      <el-card v-if="summary?.incremental" shadow="never" class="incremental">
        <template #header>增量重审收益</template>
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="增量运行次数">
            {{ summary.incremental.runs }}
          </el-descriptions-item>
          <el-descriptions-item label="省下的 token 比例">
            {{ (summary.incremental.saved_ratio * 100).toFixed(1) }}%
          </el-descriptions-item>
          <el-descriptions-item label="全量等价 token">
            {{ summary.incremental.full_equivalent_tokens.toLocaleString() }}
          </el-descriptions-item>
          <el-descriptions-item label="实际消耗 token">
            {{ summary.incremental.incremental_tokens.toLocaleString() }}
          </el-descriptions-item>
        </el-descriptions>
        <p v-if="!summary.incremental.runs" class="empty-hint">
          还没有增量运行。在工作台里先把合同传成 v2，再用「增量重审」跑一次即可产生数据。
        </p>
      </el-card>

      <p class="note">
        离线评测（召回 / 误报 / 引用准确率 / 压测 P95）见
        <code>docs/benchmark.md</code> —— 那套数字衡量的是管线与语料，与本页的运行时用量不是同一口径。
      </p>
    </div>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.page-head h1 {
  margin: 0 0 4px;
  font-size: 20px;
}

.sub {
  margin: 0;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.content {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 12px;
}

.metric {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 14px 16px;
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 10px;
}

.metric-label {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.metric-value {
  font-size: 24px;
  font-weight: 600;
  line-height: 1.2;
}

.metric-foot {
  color: var(--el-text-color-placeholder);
  font-size: 11px;
}

.incremental {
  border-radius: 10px;
}

.empty-hint {
  margin: 10px 0 0;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.note {
  margin: 0;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  line-height: 1.7;
}

code {
  padding: 1px 4px;
  border-radius: 3px;
  background: var(--el-fill-color);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
</style>
