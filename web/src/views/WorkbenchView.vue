<script setup lang="ts">
/**
 * 路由 `/contracts/:id`：三栏审查工作台。
 *
 * 左：原文（唯一能看到「他到底写了什么」的地方）
 * 中：风险发现 / 条款变更（可筛选、可定位回原文）
 * 右：运行态（节点、摘要、事件流、等待审批的状态说明——审批动作不在这里）
 *
 * 运行态放 store：刷新页面时用 `?review=<id>` 恢复到同一笔运行，
 * 事件流从 seq 0 重放并去重，所以工作台的「进度」在刷新后不会从零开始。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { getContract, getContractDiff } from '../api/contracts'
import { createReview } from '../api/reviews'
import ClauseDiffList from '../components/ClauseDiffList.vue'
import DocViewer from '../components/DocViewer.vue'
import FindingsPanel from '../components/FindingsPanel.vue'
import RunPanel from '../components/RunPanel.vue'
import UploadCard from '../components/UploadCard.vue'
import { useRunStore } from '../stores/run'
import type { ContractDetail, ContractDiff, Finding, ReviewMode } from '../types/api'
import type { ClauseRow } from '../types/events'
import { clauseStatusOf } from '../types/events'

const props = defineProps<{ id: string }>()

const route = useRoute()
const router = useRouter()
const run = useRunStore()

const contract = ref<ContractDetail | null>(null)
const loading = ref(false)
const error = ref('')
const starting = ref(false)
const activeTab = ref<'findings' | 'diff'>('findings')
const diff = ref<ContractDiff | null>(null)
const viewer = ref<InstanceType<typeof DocViewer> | null>(null)
const showUploadDialog = ref(false)

const canIncremental = computed(() => (contract.value?.version ?? 1) > 1)
const running = computed(() => run.status === 'running' && run.events.length > 0)
/** 已完成的这一轮到底跑的是什么模式——由事件决定，不能拿「下次要跑的模式」冒充。 */
const ranIncremental = computed(() => run.completed?.incremental?.mode === 'incremental')

/** 条款变更的正文来源只有 findings.evidence（事件流不单独下发条款文本，不编造）。 */
const evidenceByClause = computed(() => {
  const map = new Map<string, string>()
  for (const finding of run.findings) {
    if (finding.evidence && !map.has(finding.clause_id)) map.set(finding.clause_id, finding.evidence)
  }
  return map
})

const clauseRows = computed<ClauseRow[]>(() => {
  const info = run.completed?.incremental
  const ids = Array.from(
    new Set([
      ...(info?.changed_clause_ids ?? []),
      ...(info?.added_clause_ids ?? []),
      ...(diff.value?.changed ?? []),
      ...(diff.value?.added ?? []),
      ...(diff.value?.removed ?? []),
    ]),
  )
  return ids.map((clauseId) => ({
    clause_id: clauseId,
    no: clauseId,
    text: evidenceByClause.value.get(clauseId) ?? '',
    status: clauseStatusOf(clauseId, info),
    reused: false,
    stale: (info?.removed_clause_ids ?? diff.value?.removed ?? []).includes(clauseId),
  }))
})

async function loadContract() {
  loading.value = true
  error.value = ''
  try {
    contract.value = await getContract(props.id)
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载合同失败'
  } finally {
    loading.value = false
  }
}

async function loadDiff() {
  if (!canIncremental.value) {
    diff.value = null
    return
  }
  try {
    diff.value = await getContractDiff(props.id, contract.value?.version)
  } catch {
    // diff 端点依赖「最近一次审查的增量账本」；没有账本时 404 是正常情况，不打扰用户。
    diff.value = null
  }
}

async function start(selected: ReviewMode) {
  if (!contract.value) return
  starting.value = true
  try {
    const created = await createReview({
      contract_id: contract.value.contract_id,
      version: contract.value.version,
      mode: selected,
    })
    // 先写 query 再 attach：刷新/分享链接都能回到同一笔运行。
    await router.replace({ query: { review: created.review_id } })
    await run.attach(created.review_id)
    ElMessage.success(`已发起${selected === 'incremental' ? '增量' : '全量'}审查`)
  } catch (err) {
    ElMessage.error(`发起审查失败：${err instanceof Error ? err.message : '未知错误'}`)
  } finally {
    starting.value = false
  }
}

function locate(finding: Finding) {
  const hit = viewer.value?.locate(finding.evidence ?? '')
  if (!hit) {
    ElMessage.warning('原文里没有定位到该片段（PDF 无文本层，请用浏览器内查找）')
  }
}

async function onUploaded() {
  showUploadDialog.value = false
  await loadContract()
  await loadDiff()
  ElMessage.info('已切换到新版本：现在可以用「增量重审」只跑变更条款')
}

async function bootstrap() {
  await loadContract()
  await loadDiff()
  const reviewId = route.query.review
  if (typeof reviewId === 'string' && reviewId) {
    await run.attach(reviewId)
  }
}

watch(
  () => props.id,
  () => {
    void bootstrap()
  },
)

// 同一笔运行完成且是增量运行时，补拉一次 diff（账本由本轮的 run.completed 生成）。
watch(
  () => run.status,
  (status) => {
    if (status === 'completed') void loadDiff()
  },
)

onMounted(bootstrap)
</script>

<template>
  <div class="page">
    <header class="page-head">
      <div class="title-block">
        <h1>{{ contract?.filename ?? '加载中…' }}</h1>
        <p class="sub">
          <template v-if="contract">
            <el-tag size="small" effect="plain">v{{ contract.version }}</el-tag>
            <span class="meta">{{ contract.content_type }}</span>
            <span class="meta">{{ Math.round(contract.size_bytes / 1024) }} KB</span>
            <span class="meta mono">{{ contract.contract_id.slice(0, 8) }}</span>
          </template>
        </p>
      </div>

      <div class="actions">
        <el-button size="small" @click="showUploadDialog = true">上传新版本</el-button>
        <el-button
          size="small"
          :loading="starting"
          :disabled="!contract"
          @click="start('full')"
        >
          全量审查
        </el-button>
        <el-tooltip
          :disabled="canIncremental"
          content="版本号为 1 时没有基线可比，增量会退化成全量"
          placement="bottom"
        >
          <span>
            <el-button
              type="primary"
              size="small"
              :loading="starting"
              :disabled="!contract || !canIncremental"
              @click="start('incremental')"
            >
              增量重审
            </el-button>
          </span>
        </el-tooltip>
      </div>
    </header>

    <el-alert v-if="error" type="error" :closable="false" :title="error" show-icon />

    <div v-loading="loading" class="grid">
      <section class="col doc">
        <div class="col-head">原文</div>
        <DocViewer ref="viewer" :contract="contract" />
      </section>

      <section class="col middle">
        <el-tabs v-model="activeTab" class="tabs">
          <el-tab-pane :label="`风险发现（${run.findings.length}）`" name="findings">
            <FindingsPanel :findings="run.findings" @locate="locate" />
          </el-tab-pane>
          <el-tab-pane :label="`条款变更（${clauseRows.length}）`" name="diff">
            <ClauseDiffList :rows="clauseRows" :incremental="ranIncremental" />
          </el-tab-pane>
        </el-tabs>
      </section>

      <section class="col right">
        <div class="col-head">
          <span>运行</span>
          <el-text v-if="running" type="info" size="small">实时</el-text>
        </div>
        <RunPanel />
      </section>
    </div>

    <el-dialog v-model="showUploadDialog" title="上传新版本（改稿重审）" width="420px">
      <p class="dialog-hint">
        同一合同下版本号自增。上传后「增量重审」可用，只重审变更条款，未变更条款复用缓存结论。
      </p>
      <UploadCard
        v-if="contract"
        :contract-id="contract.contract_id"
        button-text="选择新版本文件"
        :hint="`当前 v${contract.version}，上传后变为 v${contract.version + 1}`"
        @uploaded="onUploaded"
      />
    </el-dialog>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-height: calc(100vh - 56px);
}

.page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}

.page-head h1 {
  margin: 0 0 4px;
  font-size: 18px;
  word-break: break-all;
}

.sub {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0;
  flex-wrap: wrap;
}

.meta {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}

.actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.grid {
  display: grid;
  grid-template-columns: minmax(0, 1.2fr) minmax(0, 1.1fr) minmax(0, 0.9fr);
  gap: 12px;
  min-height: 560px;
  align-items: stretch;
}

.col {
  display: flex;
  flex-direction: column;
  min-height: 0;
  padding: 10px 12px;
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 10px;
}

.col-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
  font-size: 13px;
  font-weight: 600;
  color: var(--el-text-color-regular);
}

.doc {
  min-height: 520px;
}

.middle {
  min-height: 520px;
}

.tabs {
  display: flex;
  flex-direction: column;
  min-height: 0;
  height: 100%;
}

.tabs :deep(.el-tabs__content) {
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.tabs :deep(.el-tab-pane) {
  height: 100%;
}

.right {
  min-height: 520px;
}

.dialog-hint {
  margin: 0 0 12px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  line-height: 1.6;
}

@media (max-width: 1180px) {
  .grid {
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  }

  /* 右栏在中等屏下沉到第二行横跨两列，比挤成三列更可读。 */
  .right {
    grid-column: 1 / -1;
    min-height: 0;
  }
}

@media (max-width: 767px) {
  .grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .col {
    min-height: 0;
  }

  .doc,
  .middle {
    min-height: 380px;
  }
}
</style>
