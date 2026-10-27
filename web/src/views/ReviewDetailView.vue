<script setup lang="ts">
/**
 * 路由 `/reviews/:id`：审查结果。
 *
 * 与工作台的区别是「只读」：这是可以发给同事的落地页，不提供发起运行的能力。
 * 数据来源仍走事件流（它本身就是事实源），REST 快照只用于首屏标题与状态。
 */
import { computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { artifactUrl } from '../api/reviews'
import FindingsPanel from '../components/FindingsPanel.vue'
import RunPanel from '../components/RunPanel.vue'
import { useRunStore } from '../stores/run'
import { REVIEW_STATUS_LABEL, REVIEW_STATUS_TYPE } from '../types/api'

const props = defineProps<{ id: string }>()

const router = useRouter()
const run = useRunStore()

const contractId = computed(() => run.snapshot?.contract_id ?? '')
const hasArtifact = computed(() => Boolean(run.completed?.artifact))

function openContract() {
  if (!contractId.value) return
  router.push({ name: 'workbench', params: { id: contractId.value }, query: { review: props.id } })
}

/**
 * 产物下载走 Java 的 302：新窗口打开后由后端跳转到 MinIO 预签名地址，
 * 前端不持有也不拼装对象存储地址。
 */
function downloadArtifact() {
  window.open(artifactUrl(props.id), '_blank', 'noopener')
}

onMounted(() => {
  void run.attach(props.id)
})
</script>

<template>
  <div class="page">
    <header class="page-head">
      <div>
        <h1>审查结果</h1>
        <p class="sub">
          <el-tag :type="REVIEW_STATUS_TYPE[run.status]" size="small" effect="dark">
            {{ REVIEW_STATUS_LABEL[run.status] }}
          </el-tag>
          <span class="meta mono">{{ id }}</span>
        </p>
      </div>
      <div class="actions">
        <el-button v-if="hasArtifact" size="small" @click="downloadArtifact">下载批注稿</el-button>
        <el-button v-if="contractId" size="small" @click="openContract">打开工作台</el-button>
      </div>
    </header>

    <div class="grid">
      <section class="col">
        <div class="col-head">风险发现</div>
        <FindingsPanel :findings="run.findings" :loading="run.snapshotLoading && !run.events.length" />
      </section>
      <section class="col">
        <div class="col-head">运行</div>
        <RunPanel />
      </section>
    </div>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 12px;
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
  word-break: break-all;
}

.actions {
  display: flex;
  gap: 8px;
}

.grid {
  display: grid;
  grid-template-columns: minmax(0, 1.4fr) minmax(0, 1fr);
  gap: 12px;
  min-height: 480px;
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
  margin-bottom: 8px;
  font-size: 13px;
  font-weight: 600;
}

@media (max-width: 960px) {
  .grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
