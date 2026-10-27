<script setup lang="ts">
/**
 * 合同上传卡（新合同 / 新版本共用）。
 *
 * auto-upload=false + on-change：让 el-upload 只负责选文件，发请求走我们自己的
 * api 层——否则拿不到统一的错误体，失败时只能提示「上传失败」。
 */
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import type { UploadFile } from 'element-plus'
import { uploadContract, type UploadContractResult } from '../api/contracts'

const props = defineProps<{
  /** 传入即为「覆盖上传产生新版本」（同一 contract_id，version+1）。 */
  contractId?: string
  buttonText?: string
  hint?: string
}>()

const emit = defineEmits<{ uploaded: [result: UploadContractResult] }>()

const uploading = ref(false)

async function onPick(file: UploadFile) {
  const raw = file.raw
  if (!raw) return
  uploading.value = true
  try {
    const result = await uploadContract(raw, props.contractId)
    ElMessage.success(
      props.contractId
        ? `新版本已上传：${result.filename} v${result.version}`
        : `上传成功：${result.filename}（v${result.version}）`,
    )
    emit('uploaded', result)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '上传失败')
  } finally {
    uploading.value = false
  }
}
</script>

<template>
  <div class="upload">
    <el-upload
      :auto-upload="false"
      :show-file-list="false"
      :disabled="uploading"
      accept=".docx,.pdf"
      :on-change="onPick"
    >
      <el-button type="primary" :loading="uploading" plain>
        {{ buttonText ?? '上传合同' }}
      </el-button>
    </el-upload>
    <span class="hint">{{ hint ?? '支持 .docx / .pdf，上传即版本 1' }}</span>
  </div>
</template>

<style scoped>
.upload {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.hint {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
</style>
