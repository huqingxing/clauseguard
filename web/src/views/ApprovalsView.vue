<script setup lang="ts">
/**
 * 管理端 `#/approvals`：待审批队列——审核者的待办清单。
 *
 * 为什么这一屏必须存在：审批归位到管理端以后，审核者需要一个「我该批哪些」的入口。
 * 之前只能从提交者发来的链接点进去，等于把找人这件事外包给了对方。
 *
 * 为什么队列是查出来的而不是订阅出来的：待审是 review 的状态，不是事件；
 * 事件流适合「盯着一笔跑」，不适合「按等待时长排一页」。这里 30 秒轮询一次就够了。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { getApprovalQueue } from '../api/admin'
import { ApiError } from '../api/http'
import { formatWait, isOverdue } from '../utils/wait'
import type { ApprovalQueueItem } from '../types/api'
import { describeApiError } from '../utils/error'

const items = ref<ApprovalQueueItem[]>([])
const total = ref(0)
const loading = ref(true)
const error = ref('')

/**
 * 同一屏共用一个「现在」：每行各调一次 Date.now() 会让等待时长互差几秒，
 * 刷新时整列会一起跳，看起来像数据在乱动。
 */
const now = ref(Date.now())

const hasItems = computed(() => items.value.length > 0)
const overdueCount = computed(() => items.value.filter((item) => isOverdue(item.waiting_since, now.value)).length)

async function load() {
  loading.value = items.value.length === 0
  error.value = ''
  try {
    const page = await getApprovalQueue(0, 20)
    items.value = page.items
    total.value = page.total
    now.value = Date.now()
  } catch (err) {
    // 401 已由 AdminApp 的全局处理器翻回登录卡；这里只留业务失败（500/网络）。
    if (err instanceof ApiError && err.status === 401) return
    error.value = describeApiError(err, '加载待审批队列失败')
  } finally {
    loading.value = false
  }
}

let timer: ReturnType<typeof setInterval> | null = null

onMounted(() => {
  void load()
  // 别的审核者批完、提交者重新发起，这一屏要自己能变干净，不能只靠人按刷新。
  timer = setInterval(() => void load(), 30_000)
})

onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
})
</script>

<template>
  <div class="page">
    <header class="head">
      <div>
        <h1>待审批</h1>
        <p class="sub">
          <span v-if="loading">正在读取队列…</span>
          <span v-else-if="hasItems">
            {{ total }} 笔合同等审核者拍板
            <template v-if="overdueCount">，其中 {{ overdueCount }} 笔已超过 2 小时</template>
          </span>
          <span v-else>队列已清空</span>
        </p>
      </div>
      <el-button size="small" :loading="loading" @click="load">刷新</el-button>
    </header>

    <el-alert v-if="error" type="error" :closable="false" show-icon :title="`加载失败：${error}`" />

    <div v-if="loading" class="loading">
      <el-skeleton :rows="5" animated />
    </div>

    <!-- 空态要说清「没有」而不是留白：队列空是正常状态，不是加载失败。 -->
    <el-empty v-else-if="!hasItems && !error" description="当前没有待审批的合同" />

    <div v-else-if="hasItems" class="table">
      <div class="row head-row">
        <span>合同名</span>
        <span class="num">高风险条款</span>
        <span>等待时长</span>
        <span class="op">操作</span>
      </div>

      <div
        v-for="item in items"
        :key="item.review_id"
        class="row"
        :class="{ overdue: isOverdue(item.waiting_since, now) }"
      >
        <div class="title-cell">
          <span class="title" :title="item.contract_title">{{ item.contract_title }}</span>
          <span class="review-id mono">{{ item.review_id.slice(0, 8) }}</span>
        </div>
        <div class="num">
          <el-tag type="danger" size="small" effect="plain">{{ item.risk_high_count }} 条</el-tag>
        </div>
        <div class="wait">
          {{ formatWait(item.waiting_since, now) }}
          <span v-if="isOverdue(item.waiting_since, now)" class="flag">超时</span>
        </div>
        <div class="op">
          <router-link :to="{ name: 'approval', params: { id: item.review_id } }" class="enter">
            进入审批
          </router-link>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

h1 {
  margin: 0 0 2px;
  font-size: 18px;
}

.sub {
  margin: 0;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.loading {
  padding: 8px 0;
}

.table {
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 10px;
}

/*
 * 用 grid 而不是 <table>：375px 下要变成两行卡片，table 的列宽与换行几乎不可控。
 * 桌面四列（合同名 / 高风险条款 / 等待时长 / 操作），窄屏切成标题行 + 数据行。
 */
.row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 120px 150px 96px;
  gap: 8px;
  align-items: center;
  padding: 10px 14px;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.row:last-child {
  border-bottom: none;
}

.head-row {
  background: var(--el-fill-color-light);
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

/* 超过 2 小时标黄：不是错误，是「这笔在等人」的提示，用警告色而不是红色。 */
.row.overdue {
  background: var(--el-color-warning-light-9);
  box-shadow: inset 3px 0 0 var(--el-color-warning);
}

.title-cell {
  display: flex;
  align-items: baseline;
  gap: 8px;
  min-width: 0;
}

.title {
  overflow: hidden;
  font-size: 14px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}

.review-id {
  flex: 0 0 auto;
  color: var(--el-text-color-placeholder);
  font-size: 11px;
}

.num {
  font-size: 13px;
}

.wait {
  font-size: 13px;
  color: var(--el-text-color-regular);
}

.flag {
  margin-left: 6px;
  padding: 0 5px;
  border-radius: 3px;
  background: var(--el-color-warning-light-8);
  color: var(--el-color-warning-dark-2);
  font-size: 11px;
}

.enter {
  color: var(--el-color-primary);
  font-size: 13px;
  text-decoration: none;
}

.enter:hover {
  text-decoration: underline;
}

@media (max-width: 767px) {
  /* 选择器带 .row 前缀是有意的：`.row` 与 `.head-row` 同权重，
     写在后面的 `.row { display: flex }` 会把这里的 none 顶掉，表头就留在窄屏上了。 */
  .row.head-row {
    display: none;
  }

  /*
   * 窄屏放弃列对齐，改成两行：第一行合同名，第二行「高风险 · 等待时长 … 进入审批」。
   * 用 flex-wrap 而不是再排一套 grid-template-areas：四列变两列时，任何一个单元格
   * 漏配 grid-area 就会被自动塞进隐式行，位置随内容长度漂移，肉眼很难发现。
   */
  .row {
    display: flex;
    flex-wrap: wrap;
    gap: 6px 10px;
    padding: 12px;
  }

  .title-cell {
    flex: 1 1 100%;
    flex-direction: column;
    align-items: flex-start;
    gap: 2px;
  }

  .title,
  .review-id {
    white-space: normal;
    word-break: break-word;
  }

  .num,
  .wait {
    flex: 0 0 auto;
  }

  .op {
    flex: 0 0 auto;
    margin-left: auto;
  }
}
</style>
