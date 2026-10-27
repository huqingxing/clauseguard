<script setup lang="ts">
/**
 * 审查控制台（Stage 11）：发起审查 + 事件流 + 变更条款高亮 + 运行摘要。
 *
 * 关键交互：「仅重审变更」开关 —— 打开即 mode=incremental，Java 把版本号一并透传，
 * 编排层只重审相对基线变更/新增的条款，未变更条款走缓存复用。
 * 开关只在 version>1 时可用：首版没有基线可比，增量会退化为全量（有意义但不省 token，
 * 直接禁用比让用户点了才发现更诚实）。
 */
import { computed, onBeforeUnmount, ref } from 'vue'
import { ElMessage } from 'element-plus'
import type { UploadFile } from 'element-plus'
import { uploadContract } from '../api/contracts'
import { createReview, subscribeReview } from '../api/reviews'
import ClauseDiffList from './ClauseDiffList.vue'
import RunSummary from './RunSummary.vue'
import type {
  AgentStreamEvent,
  ClauseRow,
  InterruptEvent,
  ReviewMode,
  RunCompletedEvent,
} from '../types/events'
import { clauseStatusOf } from '../types/events'

const contractId = ref('')
const version = ref(2)
const incrementalOnly = ref(true)
const running = ref(false)
const uploading = ref(false)
const uploadedName = ref('')
const completed = ref<RunCompletedEvent | null>(null)
const interrupt = ref<InterruptEvent | null>(null)
const feed = ref<AgentStreamEvent[]>([])
let unsubscribe: (() => void) | null = null

const mode = computed<ReviewMode>(() => (incrementalOnly.value ? 'incremental' : 'full'))
const incrementalDisabled = computed(() => version.value <= 1)

const clauseRows = computed<ClauseRow[]>(() => {
  const info = completed.value?.incremental
  const textById = new Map(
    (interrupt.value?.payload?.items ?? []).map((item) => [item.clause_id, item.clause_text]),
  )
  const riskById = new Map(
    (interrupt.value?.payload?.items ?? []).map((item) => [item.clause_id, item.risk]),
  )
  // removed 条款不在当前版本里，事件流只有 ID；原文留空（不编造）。
  const ids = [
    ...(info?.changed_clause_ids ?? []),
    ...(info?.added_clause_ids ?? []),
    ...(info?.removed_clause_ids ?? []),
  ]
  const uniq = Array.from(new Set(ids))
  return uniq.map((clauseId) => ({
    clause_id: clauseId,
    no: clauseId,
    text: textById.get(clauseId) ?? '',
    status: clauseStatusOf(clauseId, info),
    reused: false,
    stale: (info?.removed_clause_ids ?? []).includes(clauseId),
    risk_level: riskById.get(clauseId),
  }))
})

/**
 * 选中文件即上传（auto-upload=false + on-change，避免 el-upload 自己发请求时
 * 拿不到统一错误处理）。成功后把 contract_id / version 回填到下面的表单，
 * 用户不必再手抄一个 UUID。
 */
async function onPick(file: UploadFile) {
  const raw = file.raw
  if (!raw) return
  uploading.value = true
  try {
    const uploaded = await uploadContract(raw)
    contractId.value = uploaded.contract_id
    version.value = uploaded.version
    uploadedName.value = `${uploaded.filename}（${Math.round(uploaded.size_bytes / 1024)} KB）`
    // 首版没有基线可比：增量会退化成全量，开关必须关掉而不是让用户点了才发现。
    if (uploaded.version <= 1) incrementalOnly.value = false
    ElMessage.success(`上传成功，已填入合同 ID（version=${uploaded.version}）`)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '上传失败')
  } finally {
    uploading.value = false
  }
}

async function start() {
  if (!contractId.value.trim()) {
    ElMessage.warning('请先填写合同 ID')
    return
  }
  running.value = true
  completed.value = null
  interrupt.value = null
  feed.value = []
  try {
    const created = await createReview({
      contract_id: contractId.value.trim(),
      version: version.value,
      mode: mode.value,
    })
    unsubscribe = subscribeReview(
      created.review_id,
      (event) => {
        feed.value.push(event)
        if (event.type === 'run.completed') {
          completed.value = event as RunCompletedEvent
          running.value = false
          unsubscribe?.()
          unsubscribe = null
        } else if (event.type === 'interrupt') {
          interrupt.value = event as InterruptEvent
        } else if (event.type === 'run.failed') {
          running.value = false
          unsubscribe?.()
          unsubscribe = null
        }
      },
      () => {
        // EventSource 会自动重连；这里只提示，不手动关闭（断线续传由 Java 重放保证）。
        ElMessage.info('事件流连接中断，正在重连')
      },
    )
  } catch (error) {
    running.value = false
    ElMessage.error(`发起审查失败: ${(error as Error).message}`)
  }
}

onBeforeUnmount(() => {
  unsubscribe?.()
  unsubscribe = null
})
</script>

<template>
  <el-container class="console">
    <el-header class="console-header">
      <h1>ClauseGuard · 合同审查</h1>
    </el-header>

    <el-main>
      <el-card shadow="never" style="margin-bottom: 16px">
        <template #header>上传合同（DOCX / PDF）</template>
        <el-upload
          :auto-upload="false"
          :show-file-list="false"
          :disabled="uploading"
          accept=".docx,.pdf"
          :on-change="onPick"
        >
          <el-button :loading="uploading">选择文件并上传</el-button>
        </el-upload>
        <el-text v-if="uploadedName" type="success" size="small" style="margin-left: 12px">
          已上传：{{ uploadedName }}，合同 ID 与版本号已自动填入下方
        </el-text>
      </el-card>

      <el-card shadow="never">
        <el-form :inline="true" @submit.prevent="start">
          <el-form-item label="合同 ID">
            <el-input v-model="contractId" placeholder="已上传合同的 UUID" style="width: 280px" />
          </el-form-item>
          <el-form-item label="版本号">
            <el-input-number v-model="version" :min="1" :max="99" />
          </el-form-item>
          <el-form-item label="仅重审变更">
            <el-switch
              v-model="incrementalOnly"
              :disabled="incrementalDisabled"
              active-text="incremental"
              inactive-text="full"
            />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="running" @click="start">发起审查</el-button>
          </el-form-item>
        </el-form>
        <el-text v-if="incrementalDisabled" type="info" size="small">
          版本号为 1 时没有基线可比，只能全量重审。
        </el-text>
      </el-card>

      <el-alert
        v-if="interrupt"
        type="warning"
        :closable="false"
        :title="`${interrupt.payload?.items?.length ?? 0} 条高风险条款待人工审批`"
        style="margin-top: 16px"
      />

      <div class="grid">
        <ClauseDiffList :rows="clauseRows" :incremental="mode === 'incremental'" />
        <RunSummary :completed="completed" />
      </div>

      <el-card shadow="never" style="margin-top: 16px">
        <template #header>事件流（{{ feed.length }}）</template>
        <el-timeline v-if="feed.length" style="max-height: 260px; overflow: auto">
          <el-timeline-item
            v-for="event in feed"
            :key="event.seq"
            :timestamp="`#${event.seq} ${event.type}`"
            placement="top"
          >
            <span v-if="'node' in event && event.node">{{ event.node }}</span>
            <span v-else-if="'text' in event && event.text">{{ event.text }}</span>
            <span v-else>{{ event.type }}</span>
          </el-timeline-item>
        </el-timeline>
        <el-empty v-else description="尚未开始" />
      </el-card>
    </el-main>
  </el-container>
</template>

<style scoped>
.console-header h1 {
  margin: 0;
  font-size: 20px;
}

.grid {
  display: grid;
  grid-template-columns: minmax(0, 3fr) minmax(0, 2fr);
  gap: 16px;
  margin-top: 16px;
}

@media (max-width: 960px) {
  .grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
