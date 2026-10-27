<script setup lang="ts">
/**
 * 路由 `/reviews/:id/approve`：人工审批（375px 优先）。
 *
 * 为什么单开一页而不是在工作台弹抽屉：审批人是「被拉进来拍板的人」，不是发起审查的人。
 * 他要的是一条能直接发到手机的链接，打开就是待办清单，不需要看懂三栏。
 *
 * 审批基线（contract_version / content_hash）来自 interrupt 事件——事件流从 seq 0 可回放，
 * 所以手机端刷新、直开链接都能重建，不依赖上一个页面的内存状态。
 */
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { approveReview } from '../api/reviews'
import { ApiError } from '../api/http'
import { useRunStore } from '../stores/run'
import { useSessionStore } from '../stores/session'
import type { ApprovalDecision, ApproveRequest } from '../types/api'
import { RISK_TAG_TYPE } from '../types/api'

const props = defineProps<{ id: string }>()

const router = useRouter()
const run = useRunStore()
const session = useSessionStore()

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
const submitting = ref(false)

const rawItems = computed(() => run.pendingItems)

/**
 * 按 clause_id 归并：契约里 ApproveRequest 的主键是 clause_id（乐观锁也按条款比对），
 * 不归并会出现「同一条款卡片渲染两次、却只能落一条草稿」的错位。
 */
const items = computed<PendingGroup[]>(() => {
  const map = new Map<string, PendingGroup>()
  for (const item of rawItems.value) {
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
})

const decidedCount = computed(
  () => items.value.filter((item) => drafts[item.clause_id]?.decision).length,
)

const canSubmit = computed(
  () =>
    items.value.length > 0 &&
    decidedCount.value === items.value.length &&
    session.operator.trim().length > 0 &&
    items.value.every((item) => {
      const draft = drafts[item.clause_id]
      return draft?.decision !== 'edit' || draft.edited_text.trim().length > 0
    }),
)

const state = computed(() => {
  if (items.value.length) return 'pending'
  if (run.status === 'completed') return 'done'
  if (run.status === 'failed') return 'failed'
  return 'loading'
})

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
  const payload = run.interrupt?.payload
  const version = payload?.contract_version
  const hash = payload?.content_hash
  if (version === undefined || !hash) {
    ElMessage.error('缺少中断快照（合同版本/内容哈希），无法审批')
    return
  }
  if (!session.operator.trim()) {
    ElMessage.warning('请先填写审批人（会写入审计记录）')
    return
  }

  const approvals: ApproveRequest[] = items.value.map((item) => {
    const draft = drafts[item.clause_id]
    return {
      clause_id: item.clause_id,
      decision: draft.decision,
      comment: draft.comment.trim() || undefined,
      edited_text: draft.decision === 'edit' ? draft.edited_text.trim() : null,
      contract_version: String(version),
      content_hash: hash,
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
    await approveReview(run.reviewId || props.id, approvals, session.operator.trim())
    // 状态切回 running，同一 store 上清掉中断标记即可继续收续跑事件。
    run.markResumed()
    ElMessage.success('审批已记录，正在续跑')
    await router.push({ name: 'review', params: { id: props.id } })
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

onMounted(() => {
  void run.attach(props.id)
})
</script>

<template>
  <div class="page">
    <header class="head">
      <h1>人工审批</h1>
      <p class="sub">
        <span v-if="state === 'pending'">{{ items.length }} 条高风险条款待拍板</span>
        <span v-else-if="state === 'done'">本次审查已完成</span>
        <span v-else-if="state === 'failed'">运行失败</span>
        <span v-else>正在读取中断快照…</span>
      </p>
    </header>

    <el-alert
      v-if="state === 'done'"
      type="success"
      :closable="false"
      show-icon
      title="这笔审查已经完成，没有待审批项"
    />

    <el-alert
      v-if="state === 'failed'"
      type="error"
      :closable="false"
      show-icon
      :title="`运行失败：${run.failure?.message ?? '未知原因'}`"
    />

    <div v-if="state === 'loading'" class="loading">
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
      <el-input v-model="session.operator" size="small" placeholder="审批人（必填）" class="operator" />
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
  left: 0;
  right: 0;
  bottom: 0;
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 10px 12px calc(10px + env(safe-area-inset-bottom));
  background: var(--el-bg-color);
  border-top: 1px solid var(--el-border-color-light);
  /* 与 shell 的移动端 tabbar 叠在一起时让位，避免两个底栏打架。 */
  z-index: 10;
}

.operator {
  flex: 1;
  min-width: 0;
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

@media (max-width: 767px) {
  .actions {
    bottom: 56px;
  }
}
</style>
