<script setup lang="ts">
/**
 * 中栏：风险发现清单。
 *
 * 默认把「高风险」排在最前、并按等级过滤——一份 23 条发现的报告，用户真正要看的是
 * 其中 2 条高风险。筛选维度只用契约里已有的字段（risk_level / agent / risk_type），
 * 不新造分类。
 */
import { computed, ref } from 'vue'
import type { Finding, RiskLevel } from '../types/api'
import { RISK_LABEL, RISK_TAG_TYPE } from '../types/api'

const props = defineProps<{ findings: Finding[]; loading?: boolean }>()
const emit = defineEmits<{ locate: [finding: Finding] }>()

const LEVEL_ORDER: RiskLevel[] = ['high', 'medium', 'unverified', 'low']

const activeLevels = ref<RiskLevel[]>([...LEVEL_ORDER])
const activeAgents = ref<string[]>([])
const keyword = ref('')
const expanded = ref<Set<string>>(new Set())

const counts = computed(() => {
  const map = new Map<RiskLevel, number>()
  for (const finding of props.findings) {
    map.set(finding.risk_level, (map.get(finding.risk_level) ?? 0) + 1)
  }
  return map
})

const agents = computed(() => {
  const set = new Set<string>()
  for (const finding of props.findings) if (finding.agent) set.add(finding.agent)
  return Array.from(set).sort()
})

const visible = computed(() => {
  const text = keyword.value.trim().toLowerCase()
  return props.findings
    .filter((finding) => activeLevels.value.includes(finding.risk_level))
    .filter((finding) => !activeAgents.value.length || activeAgents.value.includes(finding.agent))
    .filter((finding) => {
      if (!text) return true
      const haystack = `${finding.clause_id} ${finding.summary} ${finding.evidence ?? ''} ${finding.risk_type}`
      return haystack.toLowerCase().includes(text)
    })
    .sort((a, b) => LEVEL_ORDER.indexOf(a.risk_level) - LEVEL_ORDER.indexOf(b.risk_level))
})

/** 展开态用 key 记录而不是下标：筛选变化重排时下标会串位，展开的卡片会跳。 */
function keyOf(finding: Finding): string {
  return `${finding.clause_id}:${finding.risk_type}:${finding.summary.slice(0, 24)}`
}

function toggle(finding: Finding) {
  const next = new Set(expanded.value)
  const key = keyOf(finding)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  expanded.value = next
}

function isExpanded(finding: Finding): boolean {
  return expanded.value.has(keyOf(finding))
}
</script>

<template>
  <div class="panel">
    <div class="filters">
      <el-checkbox-group v-model="activeLevels" size="small">
        <el-checkbox-button v-for="level in LEVEL_ORDER" :key="level" :value="level">
          {{ RISK_LABEL[level] }} {{ counts.get(level) ?? 0 }}
        </el-checkbox-button>
      </el-checkbox-group>

      <el-select
        v-model="activeAgents"
        multiple
        collapse-tags
        clearable
        placeholder="全部专家"
        size="small"
        class="agent-select"
      >
        <el-option v-for="agent in agents" :key="agent" :label="agent" :value="agent" />
      </el-select>

      <el-input v-model="keyword" size="small" placeholder="搜索条款/结论" clearable class="search" />
    </div>

    <div class="count">
      共 {{ findings.length }} 条，当前显示 {{ visible.length }} 条
    </div>

    <div v-loading="loading" class="list">
      <el-empty v-if="!visible.length" description="没有符合条件的发现" :image-size="72" />

      <article
        v-for="finding in visible"
        :key="keyOf(finding)"
        class="card"
        :class="`level-${finding.risk_level}`"
      >
        <header class="card-head">
          <el-tag :type="RISK_TAG_TYPE[finding.risk_level]" size="small" effect="dark">
            {{ RISK_LABEL[finding.risk_level] }}
          </el-tag>
          <span class="risk-type">{{ finding.risk_type }}</span>
          <el-text type="info" size="small" class="agent">{{ finding.agent }}</el-text>
          <el-text type="info" size="small" class="clause muted">{{ finding.clause_id }}</el-text>
        </header>

        <p class="summary">{{ finding.summary }}</p>

        <blockquote v-if="finding.evidence" class="evidence">
          {{ finding.evidence }}
          <el-button link type="primary" size="small" @click="emit('locate', finding)">
            在原文定位
          </el-button>
        </blockquote>

        <p v-if="finding.suggestion" class="suggestion">
          <span class="label">对策</span>{{ finding.suggestion }}
        </p>

        <div v-if="finding.citations?.length" class="citations">
          <el-button link size="small" @click="toggle(finding)">
            {{ isExpanded(finding) ? '收起引用' : `引用（${finding.citations.length}）` }}
          </el-button>
          <div v-if="isExpanded(finding)" class="citation-list">
            <div v-for="citation in finding.citations" :key="citation.source_id" class="citation">
              <div class="citation-title">
                {{ citation.title }}
                <el-link v-if="citation.url" :href="citation.url" target="_blank" type="primary" size="small">
                  原文
                </el-link>
              </div>
              <p class="citation-excerpt">{{ citation.excerpt }}</p>
            </div>
          </div>
        </div>
      </article>
    </div>
  </div>
</template>

<style scoped>
.panel {
  display: flex;
  flex-direction: column;
  min-height: 0;
  height: 100%;
}

.filters {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.agent-select {
  width: 160px;
}

.search {
  width: 160px;
}

.count {
  margin: 8px 0;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.list {
  flex: 1;
  min-height: 120px;
  overflow: auto;
  padding-right: 4px;
}

.card {
  padding: 10px 12px;
  margin-bottom: 10px;
  border-left: 3px solid var(--el-border-color);
  border-radius: 6px;
  background: var(--el-bg-color);
  box-shadow: 0 1px 2px rgb(0 0 0 / 4%);
}

.level-high {
  border-left-color: var(--el-color-danger);
}

.level-medium {
  border-left-color: var(--el-color-warning);
}

.level-low {
  border-left-color: var(--el-color-primary);
}

.level-unverified {
  border-left-color: var(--el-color-info);
}

.card-head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.risk-type {
  font-size: 12px;
  color: var(--el-text-color-regular);
}

.agent {
  margin-left: auto;
}

.clause {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11px;
}

/* el-text 的 type 只接受语义色，占位灰用类控制。 */
.muted {
  color: var(--el-text-color-placeholder);
}

.summary {
  margin: 8px 0 6px;
  font-size: 13px;
  line-height: 1.6;
  color: var(--el-text-color-primary);
}

.evidence {
  margin: 0 0 8px;
  padding: 8px 10px;
  border-radius: 4px;
  background: var(--el-color-warning-light-9);
  border-left: 2px solid var(--el-color-warning);
  font-size: 12px;
  line-height: 1.6;
  color: var(--el-text-color-regular);
}

.suggestion {
  margin: 0 0 6px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--el-text-color-regular);
}

.label {
  display: inline-block;
  margin-right: 6px;
  padding: 0 5px;
  border-radius: 3px;
  background: var(--el-color-success-light-9);
  color: var(--el-color-success);
  font-size: 11px;
}

.citation-list {
  margin-top: 6px;
  padding-left: 8px;
  border-left: 2px solid var(--el-border-color-light);
}

.citation-title {
  display: flex;
  gap: 8px;
  align-items: center;
  font-size: 12px;
  font-weight: 600;
}

.citation-excerpt {
  margin: 2px 0 8px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--el-text-color-secondary);
}
</style>
