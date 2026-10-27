<script setup lang="ts">
/**
 * 左栏原文渲染。
 *
 * PDF 用浏览器原生 iframe（不引 pdfjs：一份 3MB 的解析器换一个已经存在的查看器不划算）；
 * DOCX 浏览器不能原生渲染，用 docx-preview 转 HTML。
 *
 * locate() 供中栏「定位」调用：把证据片段在 HTML 里包成 <mark> 并滚动过去。
 * PDF 没有文本层，locate 会返回 false，由调用方提示——不假装定位成功了。
 */
import { onBeforeUnmount, ref, watch } from 'vue'
import { renderAsync } from 'docx-preview'
import { contractContentUrl } from '../api/contracts'
import type { ContractDetail } from '../types/api'

const props = defineProps<{ contract: ContractDetail | null }>()

const bodyEl = ref<HTMLElement | null>(null)
const styleHost = ref<HTMLElement | null>(null)
const loading = ref(false)
const error = ref('')
const kind = ref<'pdf' | 'docx' | 'other'>('other')

let markEl: HTMLElement | null = null
let renderToken = 0

function detectKind(contract: ContractDetail): 'pdf' | 'docx' | 'other' {
  const type = contract.content_type.toLowerCase()
  if (type.includes('pdf') || contract.filename.toLowerCase().endsWith('.pdf')) return 'pdf'
  if (type.includes('wordprocessingml') || contract.filename.toLowerCase().endsWith('.docx')) return 'docx'
  return 'other'
}

const contentUrl = ref('')

function clearMark() {
  if (!markEl) return
  const parent = markEl.parentNode
  if (parent) {
    parent.replaceChild(document.createTextNode(markEl.textContent ?? ''), markEl)
    parent.normalize()
  }
  markEl = null
}

async function renderDocx(contract: ContractDetail) {
  const token = ++renderToken
  loading.value = true
  error.value = ''
  try {
    const response = await fetch(contractContentUrl(contract.contract_id, contract.version))
    if (!response.ok) throw new Error(`原文下载失败：HTTP ${response.status}`)
    const buffer = await response.arrayBuffer()
    // 快速切换合同时，早先发起的渲染结果必须丢弃，否则会把旧文档画进新容器。
    if (token !== renderToken || !bodyEl.value) return
    bodyEl.value.innerHTML = ''
    await renderAsync(buffer, bodyEl.value, styleHost.value ?? undefined, {
      inWrapper: true,
      breakPages: true,
      ignoreLastRenderedPageBreak: false,
      renderComments: true,
      className: 'docx',
    })
  } catch (err) {
    if (token !== renderToken) return
    error.value = err instanceof Error ? err.message : '原文渲染失败'
  } finally {
    if (token === renderToken) loading.value = false
  }
}

watch(
  () => props.contract,
  (contract) => {
    renderToken += 1
    clearMark()
    error.value = ''
    if (!contract) {
      kind.value = 'other'
      contentUrl.value = ''
      return
    }
    kind.value = detectKind(contract)
    contentUrl.value = contractContentUrl(contract.contract_id, contract.version)
    if (kind.value === 'docx') void renderDocx(contract)
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  renderToken += 1
  clearMark()
})

/**
 * 在渲染结果里定位证据片段并高亮。
 * 逐级缩短 needle：证据常被换行/脚注截断，24 字找不到就用 12 字、6 字，
 * 找到第一个命中的即可——定位只需要「滚到附近并标出来」。
 */
function locate(snippet: string): boolean {
  if (kind.value !== 'docx' || !bodyEl.value) return false
  const raw = snippet.trim()
  if (!raw) return false
  clearMark()
  const needles = [raw.slice(0, 24), raw.slice(0, 12), raw.slice(0, 6)].filter((item) => item.length > 0)

  for (const needle of needles) {
    const walker = document.createTreeWalker(bodyEl.value, NodeFilter.SHOW_TEXT)
    let node = walker.nextNode()
    while (node) {
      const text = node.textContent ?? ''
      const index = text.indexOf(needle)
      if (index >= 0) {
        const range = document.createRange()
        range.setStart(node, index)
        range.setEnd(node, index + needle.length)
        const mark = document.createElement('mark')
        mark.className = 'doc-hit'
        range.surroundContents(mark)
        markEl = mark
        mark.scrollIntoView({ block: 'center', behavior: 'smooth' })
        return true
      }
      node = walker.nextNode()
    }
  }
  return false
}

defineExpose({ locate })
</script>

<template>
  <div class="viewer">
    <div v-if="!contract" class="placeholder">未选择合同</div>

    <template v-else>
      <el-alert v-if="error" type="error" :closable="false" :title="error" show-icon />

      <iframe
        v-else-if="kind === 'pdf'"
        :src="contentUrl"
        class="pdf"
        :title="`${contract.filename} 原文`"
      />

      <div v-else-if="kind === 'docx'" v-loading="loading" class="docx-scroll">
        <div ref="bodyEl" class="docx-body" />
      </div>

      <div v-else class="placeholder">
        <p>该格式无法在浏览器内预览。</p>
        <el-link :href="contentUrl" type="primary" target="_blank">下载原文</el-link>
      </div>

      <!-- docx-preview 的样式挂在这里，避免污染全局（否则会改掉工作台自身的排版）。 -->
      <div ref="styleHost" class="docx-styles" />
    </template>
  </div>
</template>

<style scoped>
.viewer {
  position: relative;
  display: flex;
  flex-direction: column;
  min-height: 320px;
  height: 100%;
}

.placeholder {
  display: grid;
  place-items: center;
  gap: 8px;
  min-height: 240px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.pdf {
  width: 100%;
  height: 100%;
  min-height: 460px;
  border: 1px solid var(--el-border-color-light);
  border-radius: 6px;
  background: var(--el-bg-color);
}

.docx-scroll {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 8px;
  background: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color-light);
  border-radius: 6px;
}

.docx-body {
  min-height: 100px;
}

/*
 * docx-preview 默认按 A4（约 794px）排版，而左栏只有 400px 上下：
 * 不覆盖宽度会横向溢出，用户看到的是被裁掉左边的半页。
 * 这里让「页」跟随容器宽度，去掉仿真纸张的阴影与边距。
 */
.docx-body :deep(.docx-wrapper) {
  padding: 0;
  background: transparent;
}

.docx-body :deep(.docx-wrapper > section.docx) {
  width: 100% !important;
  min-height: auto !important;
  padding: 18px !important;
  margin-bottom: 10px !important;
  box-shadow: none !important;
}

.docx-body :deep(.docx-wrapper > section.docx > article) {
  max-width: 100%;
}

.docx-styles {
  display: none;
}

/* 定位高亮：与中栏「证据」的黄色底一致，让人一眼对上。 */
.docx-body :deep(mark.doc-hit) {
  background: var(--el-color-warning-light-5);
  outline: 2px solid var(--el-color-warning);
  border-radius: 2px;
}
</style>
