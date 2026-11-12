<script setup lang="ts">
/**
 * 管理端 `#/approvals/:id`：人工审批（375px 优先）。
 *
 * 为什么这一页在管理端：拍板的是审核者，发起审查的是提交者。链接发给审核者，
 * 打开就是待办清单，不需要看懂用户端的三栏工作台。
 *
 * 为什么不复用用户端 pinia 的 run store：管理端只有这一屏要运行态，
 * 为一个页面把整套 store 带进 admin 包不划算。这里直接订阅同一条事件流——
 * 事件从 seq 0 可回放，所以手机刷新、直开链接都能重建中断快照，不依赖上个页面的内存。
 */
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { approveReview, getReview, subscribeReview, type ReviewStream } from '../api/reviews'
import { ApiError } from '../api/http'
import { getUsername } from '../api/auth'
import type { ApprovalDecision, ApproveRequest } from '../types/api'
import { RISK_TAG_TYPE } from '../types/api'
import type { InterruptItem } from '../types/events'
import { isInterrupt, isRunCompleted, isRunFailed } from '../types/events'

const props = defineProps<{ id: string }>()

interface Draft {
  decision: ApprovalDecision
  comment: string
  edited_text: string
}

/** 一条条款可能被多个专家各报一次（如付款风险 + 违约风险），审批却是「按条款拍板」。 */
interface PendingGroup {
  clause_id: string
  clause_text: string
  risks: Array<{ risk_type: string; risk: string; suggestion: string }>
}

const drafts = reactive<Record<string, Draft>>({})
const items = ref<PendingGroup[]>([])
const submitting = ref(false)

/**
 * 审批人 = 当前登录的管理员账号（仅用于界面展示，不回传后端）。
 * 真正的操作人由后端从认证主体写入审批记录，前端传姓名等于谁都能以别人名义签字，
 * 审计表不可信——这是阶段 14 刻意去掉的环节。
 */
const operator = getUsername()

/** 乐观锁基线来自 interrupt 事件；没有它不能提交（服务端会 409）。 */
let baseline: { contract_version: string; content_hash: string } | null = null

let stream: ReviewStream | null = null
let staleTimer: ReturnType<typeof setTimeout> | null = null

/**
 * submitted 不是 pending 的终态，而是「这一页的活儿干完了」：
 * 续跑事件随后还会到，不能让界面自己翻回「已完成」。
 * stale 是「重放里根本没有中断」：服务重启过、事件超出保留窗口，
 * 这类链接永远等不到快照，转圈比报错更误导人。
 */
const phase = ref<'loading' | 'pending' | 'done' | 'failed' | 'submitted' | 'stale'>('loading')
const failure = ref('')

/**
 * 按 clause_id 归并：契约里 ApproveRequest 的主键是 clause_id（乐观锁也按条款比对），
 * 不归并会出现「同一条款卡片渲染两次、却只能落一条草稿」的错位。
 */
function groupItems(raw: InterruptItem[]): PendingGroup[] {
  const map = new Map<string, PendingGroup>()
  for (const item of raw) {
    const existing = map.get(item.clause_id)
    if (existing) {
      existing.risks.push({ risk_type: item.risk_type, risk: item.risk, suggestion: item.suggestion })
      if (!existing.clause_text && item.clause_text) existing.clause_text = item.clause_text
    } else {
      map.set(item.clause_id, {
        clause_id: item.clause_id,
        clause_text: item.clause_text,
        risks: [{ risk_type: item.risk_type, risk: item.risk, suggestion: item.suggestion }],
      })
    }
  }
  return Array.from(map.values())
}

const decidedCount = computed(
  () => items.value.filter((item) => drafts[item.clause_id]?.decision).length,
)

const canSubmit = computed(
  () =>
    items.value.length > 0 &&
    decidedCount.value === items.value.length &&
    operator.trim().length > 0 &&
    items.value.every((item) => {
      const draft = drafts[item.clause_id]
      return draft?.decision !== 'edit' || draft.edited_text.trim().length > 0
    }),
)

/** 中断快照到达后初始化草稿：默认「通过」，但必须逐条确认过才算已决策。 */
function seedDrafts() {
  for (const item of items.value) {
    if (!drafts[item.clause_id]) {
      drafts[item.clause_id] = { decision: 'approve', comment: '', edited_text: '' }
    }
  }
}

watch(items, seedDrafts, { immediate: true })

async function submit() {
  const snapshot = baseline
  if (!snapshot) {
    ElMessage.error('缺少中断快照（合同版本/内容哈希），无法审批')
    return
  }
  if (!operator.trim()) {
    ElMessage.error('当前标签页没有登录账号，无法写入审计记录')
    return
  }

  const approvals: ApproveRequest[] = items.value.map((item) => {
    const draft = drafts[item.clause_id]
    return {
      clause_id: item.clause_id,
      decision: draft.decision,
      comment: draft.comment.trim() || undefined,
      edited_text: draft.decision === 'edit' ? draft.edited_text.trim() : null,
      contract_version: snapshot.contract_version,
      content_hash: snapshot.content_hash,
    }
  })

  const rejectCount = approvals.filter((item) => item.decision === 'reject').length
  try {
    await ElMessageBox.confirm(
      `${approvals.length} 条高风险条款：通过 ${approvals.length - rejectCount} 条、驳回 ${rejectCount} 条。提交后立即续跑，不能撤回。`,
      '确认审批结果',
      { confirmButtonText: '提交并续跑', cancelButtonText: '再检查一下', type: 'warning' },
    )
  } catch {
    return
  }

  submitting.value = true
  try {
    await approveReview(props.id, approvals)
    // 提交成功后主动关流：续跑事件会把这一页从「已提交」翻成「已完成」，
    // 审核者会以为自己的审批没落库。这一页的职责到「审批已记录」为止。
    phase.value = 'submitted'
    stream?.close()
    ElMessage.success('审批已记录，正在续跑')
  } catch (error) {
    if (error instanceof ApiError && error.status === 409) {
      ElMessage.error('合同版本或内容哈希已变化（合同被改过），本次审批被拒。请回工作台重新发起审查。')
    } else {
      ElMessage.error(error instanceof Error ? error.message : '提交审批失败')
    }
  } finally {
    submitting.value = false
  }
}

/**
 * 快照只做兜底：链接可能是转发的，打开时这笔审查早就跑完了，
 * 那种情况下重放里没有 interrupt，页面不该一直转圈。
 */
async function loadSnapshot() {
  try {
    const review = await getReview(props.id)
    // 事件流是更权威的答案：它已经给了结论，就别用可能更旧的快照覆盖。
    if (phase.value !== 'loading') return
    if (review.status === 'completed') phase.value = 'done'
    else if (review.status === 'failed') phase.value = 'failed'
  } catch {
    // 拿不到就继续等事件流：它自己会重连并全量重放。
  }
}

onMounted(() => {
  void loadSnapshot()
  // 重放是秒级完成的；6 秒还停在 loading，说明这条链接的 interrupt 已经不在存储里了。
  staleTimer = setTimeout(() => {
    if (phase.value === 'loading') phase.value = 'stale'
  }, 6000)

  stream = subscribeReview(props.id, {
    onEvent: (event) => {
      if (phase.value === 'submitted') return
      if (isInterrupt(event)) {
        const payload = event.payload
        // 没有基线就等于没有乐观锁，卡片点得动也提交不了，宁可停在 loading。
        if (payload?.contract_version === undefined || !payload.content_hash) return
        baseline = {
          contract_version: String(payload.contract_version),
          content_hash: payload.content_hash,
        }
        items.value = groupItems(payload.items ?? [])
        phase.value = 'pending'
        if (staleTimer) clearTimeout(staleTimer)
      } else if (isRunCompleted(event)) {
        items.value = []
        phase.value = 'done'
      } else if (isRunFailed(event)) {
        failure.value = event.message ?? event.code ?? '运行失败'
        phase.value = 'failed'
      }
    },
  })
})

onBeforeUnmount(() => {
  if (staleTimer) clearTimeout(staleTimer)
  stream?.close()
})
</script>

<template>
  <div class="page">
    <header class="head">
      <!-- 从队列点进来的人要能回去，且回去后队列会重新拉一次（组件重挂载），
           刚批完的那条自然就不在了——不需要手动刷新。 -->
      <router-link :to="{ name: 'approvals' }" class="back-queue">‹ 返回待审批队列</router-link>
      <h1>人工审批</h1>
      <p class="sub">
        <span v-if="phase === 'pending'">{{ items.length }} 条高风险条款待拍板</span>
        <span v-else-if="phase === 'submitted'">审批已提交，正在续跑</span>
        <span v-else-if="phase === 'done'">本次审查已完成</span>
        <span v-else-if="phase === 'failed'">运行失败</span>
        <span v-else-if="phase === 'stale'">中断快照已不可用</span>
        <span v-else>正在读取中断快照…</span>
      </p>
    </header>

    <el-alert
      v-if="phase === 'submitted'"
      type="success"
      :closable="false"
      show-icon
      :title="`审批已记录（${decidedCount} 条），审查已续跑`"
    >
      <template #default>
        <a class="result-link" :href="`/reviews/${props.id}`">查看审查结果 →</a>
      </template>
    </el-alert>

    <el-alert
      v-if="phase === 'done'"
      type="success"
      :closable="false"
      show-icon
      title="这笔审查已经完成，没有待审批项"
    />

    <el-alert
      v-if="phase === 'stale'"
      type="warning"
      :closable="false"
      show-icon
      title="这条审批链接已失效（中断快照不在事件存储里）"
    >
      <template #default>
        服务重启或事件超出保留窗口后，审批基线（合同版本 / 内容哈希）就取不回来了。
        请让提交者在工作台重新发起一次审查，再发新的审批链接。
      </template>
    </el-alert>

    <el-alert
      v-if="phase === 'failed'"
      type="error"
      :closable="false"
      show-icon
      :title="`运行失败：${failure || '未知原因'}`"
    />

    <div v-if="phase === 'loading'" class="loading">
      <el-skeleton :rows="4" animated />
    </div>

    <div class="list">
      <article v-for="(item, index) in items" :key="item.clause_id" class="card">
        <header class="card-head">
          <span class="index">{{ index + 1 }}</span>
          <el-tag :type="RISK_TAG_TYPE.high" size="small" effect="dark">高风险</el-tag>
          <el-text v-if="item.risks.length > 1" type="info" size="small">
            {{ item.risks.length }} 个风险点
          </el-text>
          <span class="clause-id">{{ item.clause_id }}</span>
        </header>

        <p class="clause-text">{{ item.clause_text }}</p>

        <div v-for="risk in item.risks" :key="risk.risk_type" class="risk-block">
          <p class="risk"><span class="label">{{ risk.risk_type }}</span>{{ risk.risk }}</p>
          <p v-if="risk.suggestion" class="suggestion">
            <span class="label suggest">对策</span>{{ risk.suggestion }}
          </p>
        </div>

        <el-radio-group v-model="drafts[item.clause_id].decision" size="small" class="decisions">
          <el-radio-button value="approve">通过</el-radio-button>
          <el-radio-button value="reject">驳回</el-radio-button>
          <el-radio-button value="edit">改措辞</el-radio-button>
        </el-radio-group>

        <el-input
          v-if="drafts[item.clause_id].decision === 'edit'"
          v-model="drafts[item.clause_id].edited_text"
          type="textarea"
          :rows="3"
          placeholder="填写审批后的条款正文（必填）"
          class="field"
        />

        <el-input
          v-model="drafts[item.clause_id].comment"
          size="small"
          placeholder="审批意见（可选，写入审计）"
          class="field"
        />
      </article>
    </div>

    <footer class="actions">
      <span class="operator">审批人：{{ operator || '（未登录）' }}</span>
      <el-button
        type="primary"
        :loading="submitting"
        :disabled="!canSubmit"
        class="submit"
        @click="submit"
      >
        提交（{{ decidedCount }}/{{ items.length }}）
      </el-button>
    </footer>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 12px;
  /* 底部固定操作栏占位，避免最后一张卡片被遮住。 */
  padding-bottom: 84px;
}

.head h1 {
  margin: 0 0 2px;
  font-size: 18px;
}

/* 类名带 -queue：管理端外壳里已有一个 .back（返回用户端），
   同名会让「这一页的返回」和「换个入口」在选择器上无法区分。 */
.back-queue {
  display: inline-block;
  margin-bottom: 4px;
  color: var(--el-color-primary);
  font-size: 12px;
  text-decoration: none;
}

.sub {
  margin: 0;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.card {
  padding: 12px;
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-lighter);
  border-left: 3px solid var(--el-color-danger);
  border-radius: 10px;
}

.card-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.index {
  display: grid;
  place-items: center;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: var(--el-fill-color);
  font-size: 11px;
  color: var(--el-text-color-regular);
}

.clause-id {
  margin-left: auto;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11px;
  color: var(--el-text-color-placeholder);
}

.risk-block {
  padding-left: 2px;
  border-left: 2px solid var(--el-color-danger-light-7);
  margin-bottom: 6px;
}

.clause-text {
  margin: 0 0 8px;
  padding: 8px 10px;
  border-radius: 6px;
  background: var(--el-fill-color-light);
  font-size: 13px;
  line-height: 1.7;
  color: var(--el-text-color-primary);
  word-break: break-word;
}

.risk,
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
  background: var(--el-color-danger-light-9);
  color: var(--el-color-danger);
  font-size: 11px;
}

.suggestion .label,
.label.suggest {
  background: var(--el-color-success-light-9);
  color: var(--el-color-success);
}

.decisions {
  margin: 8px 0;
}

.field {
  margin-top: 8px;
}

.actions {
  position: fixed;
  left: 216px;
  right: 0;
  bottom: 0;
  /* 管理端有 216px 深色侧栏：底栏贴在视口左边会盖住侧栏底部的「当前账号 / 退出」
     和「返回用户端」。 */
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 10px 12px calc(10px + env(safe-area-inset-bottom));
  background: var(--el-bg-color);
  border-top: 1px solid var(--el-border-color-light);
  z-index: 10;
}

.operator {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  color: var(--el-text-color-regular);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.result-link {
  color: var(--el-color-primary);
  font-size: 12px;
  text-decoration: none;
}

.submit {
  flex: 0 0 132px;
}

@media (min-width: 768px) {
  .page {
    max-width: 720px;
    margin: 0 auto;
    padding-bottom: 96px;
  }
}

@media (max-width: 900px) {
  /* 管理端在窄屏把侧栏折到顶部，没有底部 tabbar，底栏回到整宽。 */
  .actions {
    left: 0;
  }
}
</style>
