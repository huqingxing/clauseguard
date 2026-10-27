<script setup lang="ts">
/**
 * 管理端 `/admin`（独立入口 admin.html）：运维健康。
 *
 * 前端不直接探 Python：/api/v1/admin/health 由 Java 代理探测后聚合，
 * 所以这里看到的是「Java 视角下 Python 是否可达」——正是出问题时需要知道的那一层。
 * 轮询 10s：这页是开着看的，不是刷的。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { getAdminHealth } from '../api/admin'
import type { AdminHealth } from '../types/api'
import { describeApiError } from '../utils/error'

const POLL_INTERVAL_MS = 10_000

const health = ref<AdminHealth | null>(null)
const loading = ref(false)
const error = ref('')
const updatedAt = ref('')
let timer: ReturnType<typeof setInterval> | null = null

async function load() {
  loading.value = true
  try {
    health.value = await getAdminHealth()
    error.value = ''
    updatedAt.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
  } catch (err) {
    // 请求失败本身也是一种健康信息：Java 挂了才会连 /health 都拿不到。
    error.value = describeApiError(err, '健康检查请求失败')
    health.value = null
  } finally {
    loading.value = false
  }
}

const overall = computed(() => {
  if (!health.value) return 'down'
  return health.value.java.status === 'up' && health.value.python.status === 'up' ? 'up' : 'degraded'
})

const overallLabel = computed(() =>
  overall.value === 'up' ? '全部正常' : overall.value === 'degraded' ? '存在异常' : '不可达',
)

onMounted(() => {
  void load()
  timer = setInterval(load, POLL_INTERVAL_MS)
})

onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
  timer = null
})
</script>

<template>
  <div class="page">
    <header class="page-head">
      <div>
        <h1>运维健康</h1>
        <p class="sub">
          双栈聚合（Java 代理探测 Python）· 每 10 秒自动刷新
          <span v-if="updatedAt">· 更新于 {{ updatedAt }}</span>
        </p>
      </div>
      <el-button size="small" :loading="loading" @click="load">立即检查</el-button>
    </header>

    <el-alert v-if="error" type="error" :closable="false" show-icon :title="`健康检查失败：${error}`" />

    <div class="cards">
      <div class="tile" :class="overall">
        <span class="tile-label">整体</span>
        <span class="tile-value">{{ overallLabel }}</span>
      </div>
      <div class="tile" :class="health?.java.status ?? 'unknown'">
        <span class="tile-label">Java 网关 :8080</span>
        <span class="tile-value">{{ health ? (health.java.status === 'up' ? 'UP' : 'DOWN') : '—' }}</span>
      </div>
      <div class="tile" :class="health?.python.status ?? 'unknown'">
        <span class="tile-label">Python 编排 :8000</span>
        <span class="tile-value">{{ health ? (health.python.status === 'up' ? 'UP' : 'DOWN') : '—' }}</span>
      </div>
    </div>

    <el-card v-if="health?.python.detail" shadow="never" class="detail">
      <template #header>Python /healthz 原始响应</template>
      <pre class="json">{{ JSON.stringify(health.python.detail, null, 2) }}</pre>
    </el-card>

    <el-card shadow="never" class="detail">
      <template #header>说明</template>
      <ul class="notes">
        <li>本页不直连 Python：`/api/v1/admin/health` 由 Java 代理探测，反映的是网关视角。</li>
        <li>MySQL / Redis / Postgres / MinIO 的逐项探测尚未在 Java 侧实现，当前只聚合双栈进程存活。</li>
        <li>任一依赖 down 时接口仍返回 200，状态由 body 表达——健康检查不该靠 HTTP 码猜。</li>
      </ul>
    </el-card>
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

.cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
}

.tile {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 16px;
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-lighter);
  border-left: 4px solid var(--el-text-color-placeholder);
  border-radius: 10px;
}

.tile.up {
  border-left-color: var(--el-color-success);
}

.tile.degraded,
.tile.down {
  border-left-color: var(--el-color-danger);
}

.tile-label {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.tile-value {
  font-size: 20px;
  font-weight: 600;
}

.detail {
  border-radius: 10px;
}

.json {
  margin: 0;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
}

.notes {
  margin: 0;
  padding-left: 18px;
  color: var(--el-text-color-regular);
  font-size: 12px;
  line-height: 1.9;
}
</style>
