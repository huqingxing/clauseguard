<script setup lang="ts">
/**
 * 路由 `/`：合同列表。
 *
 * 列表只回答一个问题「有哪些合同、各审到哪一步了」。所以每行给一个明确的下一步动作，
 * 而不是把发起审查的表单也塞进来——发起运行属于工作台。
 */
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { listContracts } from '../api/contracts'
import UploadCard from '../components/UploadCard.vue'
import { useRunStore } from '../stores/run'
import type { ContractSummary, ReviewStatus } from '../types/api'
import { REVIEW_STATUS_LABEL, REVIEW_STATUS_TYPE } from '../types/api'

const router = useRouter()
const run = useRunStore()

const contracts = ref<ContractSummary[]>([])
const keyword = ref('')
const loading = ref(false)
const error = ref('')

async function load() {
  loading.value = true
  error.value = ''
  try {
    contracts.value = await listContracts(keyword.value)
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载合同列表失败'
  } finally {
    loading.value = false
  }
}

async function onUploaded() {
  await load()
  ElMessage.success('已回到列表，点「进入工作台」发起审查')
}

function openWorkbench(row: ContractSummary) {
  router.push({ name: 'workbench', params: { id: row.contract_id } })
}

function openReview(row: ContractSummary) {
  if (!row.last_review) return
  router.push({ name: 'review', params: { id: row.last_review.review_id } })
}

function formatTime(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', { hour12: false })
}

/**
 * el-table 的插槽行数据是 any（表格组件不做行类型泛型），直接在模板里索引
 * Record<ReviewStatus, …> 会丢类型检查。收进这两个函数，把断言限制在一处。
 */
function statusOf(status: string): ReviewStatus {
  return status as ReviewStatus
}

onMounted(() => {
  // 回到列表就意味着「不再盯那一笔运行」，在这里收掉事件流：
  // 否则 SSE 连接会挂到服务端 10 分钟超时才断，白占一个 emitter。
  run.stop()
  void load()
})
</script>

<template>
  <div class="page">
    <header class="page-head">
      <div>
        <h1>合同</h1>
        <p class="sub">上传合同 → 发起审查 → 高风险条款人工审批 → 导出批注稿</p>
      </div>
    </header>

    <el-card shadow="never" class="card">
      <div class="toolbar">
        <UploadCard hint="支持 .docx / .pdf，上传即版本 1；改稿请在工作台里传新版本" @uploaded="onUploaded" />
        <div class="search">
          <el-input
            v-model="keyword"
            placeholder="按文件名搜索"
            clearable
            size="default"
            @keyup.enter="load"
            @clear="load"
          />
          <el-button @click="load">搜索</el-button>
        </div>
      </div>

      <el-alert v-if="error" type="error" :closable="false" :title="error" show-icon class="alert" />

      <el-table v-loading="loading" :data="contracts" size="default" row-key="contract_id">
        <el-table-column prop="filename" label="文件名" min-width="220" show-overflow-tooltip />
        <el-table-column label="版本" width="80">
          <template #default="{ row }">v{{ row.version }}</template>
        </el-table-column>
        <el-table-column label="最近审查" width="140">
          <template #default="{ row }">
            <el-tag
              v-if="row.last_review"
              :type="REVIEW_STATUS_TYPE[statusOf(row.last_review.status)]"
              size="small"
              effect="light"
            >
              {{ REVIEW_STATUS_LABEL[statusOf(row.last_review.status)] }}
            </el-tag>
            <el-text v-else type="info" size="small" class="muted">未审查</el-text>
          </template>
        </el-table-column>
        <el-table-column label="创建时间" width="180">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="180" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openWorkbench(row)">进入工作台</el-button>
            <el-button
              v-if="row.last_review"
              link
              type="primary"
              @click="openReview(row)"
            >
              查看结果
            </el-button>
          </template>
        </el-table-column>

        <template #empty>
          <el-empty description="还没有合同。上传一份 .docx 开始（仓库里 deploy/demo/demo-contract.docx 可直接用）" />
        </template>
      </el-table>
    </el-card>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 16px;
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

.card {
  border-radius: 10px;
}

.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 14px;
}

.search {
  display: flex;
  gap: 8px;
  width: 280px;
}

.alert {
  margin-bottom: 12px;
}

.muted {
  color: var(--el-text-color-placeholder);
}

@media (max-width: 640px) {
  .search {
    width: 100%;
  }
}
</style>
