<script setup lang="ts">
/**
 * 兜底页：用户端任何没有对应页面的地址都落在这里。
 *
 * 为什么不是「静默回列表」：老审批链接 `/reviews/:id/approve` 已被移除，
 * 悄悄重定向到列表会让打开链接的人以为系统跳错了页；地址栏也看不出原本要访问什么。
 * 停住并说明「这个地址不存在、审批在管理端」，才对得上他手里的链接。
 */
import { useRouter } from 'vue-router'

const router = useRouter()
</script>

<template>
  <div class="page">
    <header class="page-head">
      <div>
        <h1>页面不存在</h1>
        <p class="sub">这个地址在用户端没有对应页面</p>
      </div>
    </header>

    <el-card shadow="never" class="card">
      <el-result icon="warning" title="404" sub-title="链接可能来自旧版本，或地址拼写有误">
        <template #extra>
          <p class="hint">
            人工审批已迁到管理端（审核者入口），用户端只保留「提交 / 看进度 / 看结果」。
          </p>
          <el-button type="primary" @click="router.push('/')">返回合同列表</el-button>
        </template>
      </el-result>
    </el-card>
  </div>
</template>

<style scoped>
.page {
  padding: 24px;
  max-width: 960px;
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
  margin-top: 16px;
}

.hint {
  margin: 0 0 12px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
  line-height: 1.6;
}
</style>
