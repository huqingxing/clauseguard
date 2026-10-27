<script setup lang="ts">
/**
 * 用户端外壳：品牌 + 导航 + 内容区。
 *
 * 导航只有「合同」一项——运维与评测已移到独立入口 admin.html（见 src/admin/AdminApp.vue），
 * 用户端不该出现内部视角的入口。单项导航在桌面侧栏仍然保留，因为它是品牌下方的定位锚，删掉反而显得页面没装好。
 *
 * 移动端（<768px）底部只有「返回列表」：一层导航做成 tab bar 就是一条死栏
 * （点它只是回到当前页），而这一层真正需要的动作只有「退回上一层」。
 */
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

const route = useRoute()
const router = useRouter()

const items = [{ to: '/', label: '合同', name: 'contracts' }]

/** 工作台/详情都算「合同」这一支，高亮不能只按 path 精确匹配。 */
const activeName = computed(() => {
  const name = String(route.name ?? '')
  if (name === 'workbench' || name === 'review') return 'contracts'
  return name
})

/** 首页自身不需要「返回列表」；其余页面都有上一层可回。 */
const canGoBack = computed(() => route.name !== 'contracts')

function goBack() {
  // 有历史就退一步（保留用户原来的筛选/页码），直接打开链接时退回列表。
  if (window.history.state?.back) router.back()
  else void router.push('/')
}
</script>

<template>
  <div class="shell" :class="{ 'with-tabbar': canGoBack }">
    <aside class="sidebar">
      <div class="brand">
        <span class="brand-mark">CG</span>
        <span class="brand-text">
          <strong>ClauseGuard</strong>
          <small>合同风险审查</small>
        </span>
      </div>
      <nav class="nav">
        <router-link
          v-for="item in items"
          :key="item.to"
          :to="item.to"
          class="nav-item"
          :class="{ active: activeName === item.name }"
        >
          {{ item.label }}
        </router-link>
      </nav>
      <p class="footnote">前端只经 Java 网关<br />不直连编排层</p>
    </aside>

    <main class="content">
      <router-view />
    </main>

    <!--
      移动端底部栏只留「返回列表」：用户端从 4 条路由减到 3 条后，navigation 只剩「合同」一项，
      把它做成 tab 等于一个指向自己的死按钮（首页点它什么也不会发生）。
      首页没有上一层可回，整条栏都不渲染——留一条空栏比没有栏更让人困惑。
    -->
    <nav v-if="canGoBack" class="tabbar">
      <button type="button" class="tab" @click="goBack">‹ 返回列表</button>
    </nav>
  </div>
</template>

<style scoped>
.shell {
  display: flex;
  min-height: 100vh;
  background: var(--el-bg-color-page);
}

.sidebar {
  display: flex;
  flex-direction: column;
  gap: 24px;
  width: 200px;
  flex: 0 0 200px;
  padding: 20px 16px;
  background: var(--el-bg-color);
  border-right: 1px solid var(--el-border-color-light);
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
}

.brand-mark {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  border-radius: 9px;
  background: var(--el-color-primary);
  color: #fff;
  font-weight: 700;
  font-size: 13px;
  letter-spacing: 0.5px;
}

.brand-text {
  display: flex;
  flex-direction: column;
  line-height: 1.25;
}

.brand-text small {
  color: var(--el-text-color-secondary);
  font-size: 11px;
}

.nav {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.nav-item {
  padding: 9px 12px;
  border-radius: 8px;
  color: var(--el-text-color-regular);
  text-decoration: none;
  font-size: 14px;
}

.nav-item:hover {
  background: var(--el-fill-color-light);
}

.nav-item.active {
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
  font-weight: 600;
}

.footnote {
  margin: auto 0 0;
  color: var(--el-text-color-placeholder);
  font-size: 11px;
  line-height: 1.6;
}

.content {
  flex: 1;
  min-width: 0;
  padding: 20px 24px 32px;
}

.tabbar {
  display: none;
}

@media (max-width: 767px) {
  .shell {
    flex-direction: column;
  }

  /* 只有真的渲染了底栏才留出它的高度：首页没有底栏，固定留 56px 会是一条白边。 */
  .shell.with-tabbar {
    padding-bottom: 56px;
  }

  .sidebar {
    width: auto;
    flex: 0 0 auto;
    padding: 12px 14px;
    border-right: none;
    border-bottom: 1px solid var(--el-border-color-light);
  }

  .nav,
  .footnote {
    display: none;
  }

  .content {
    padding: 12px 12px 16px;
  }

  .tabbar {
    position: fixed;
    inset: auto 0 0 0;
    display: flex;
    background: var(--el-bg-color);
    border-top: 1px solid var(--el-border-color-light);
    /* 底部安全区：iPhone 有 home indicator，不留白会压到手势条上。 */
    padding-bottom: env(safe-area-inset-bottom);
  }

  .tab {
    flex: 1;
    padding: 12px 0;
    /* 这是个 <button>：清掉默认外观，才和底栏的边框/背景连成一片。 */
    border: 0;
    background: transparent;
    font-family: inherit;
    text-align: center;
    font-size: 13px;
    color: var(--el-text-color-regular);
    cursor: pointer;
  }
}
</style>
