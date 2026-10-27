<script setup lang="ts">
/**
 * 管理端 `#/approvals`：待审批队列。
 *
 * 本阶段只固定路由位，页面在阶段 5 建——队列需要一个「按审核者列出待办」的后端查询，
 * 现在没有这个接口，硬凑只能把全部审查拉下来在浏览器里筛，翻页与权限都会错。
 * 先占位而不是让链接 404：审批详情挂在这个路径下，父级必须存在，否则浏览器后退没有落脚点。
 */
</script>

<template>
  <div class="page">
    <h1>待审批</h1>
    <p class="sub">队列页面在阶段 5 提供。本阶段先固定这条路由。</p>

    <el-alert type="info" :closable="false" show-icon title="现在怎么审批">
      <template #default>
        从用户端工作台的「去审批」打开某条审批详情（<code>#/approvals/:id</code>），
        链接可以直接发到手机上逐条拍板。
      </template>
    </el-alert>

    <a class="back" href="/">← 返回用户端合同列表</a>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 12px;
  max-width: 720px;
}

h1 {
  margin: 0;
  font-size: 18px;
}

.sub {
  margin: 0;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.back {
  color: var(--el-color-primary);
  font-size: 12px;
  text-decoration: none;
}
</style>
