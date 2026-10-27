<script setup lang="ts">
/**
 * 应用外壳：品牌 + 导航 + 内容区。
 *
 * 移动端（<768px）导航沉到底部做成 tab bar——审批人在手机上单手操作，
 * 顶部汉堡菜单要多一次点击，底部栏才是移动端的默认习惯。
 */
import { computed } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()

const items = [
  { to: '/', label: '合同', name: 'contracts' },
  { to: '/eval', label: '评测', name: 'eval' },
  { to: '/admin', label: '运维', name: 'admin' },
]

/** 工作台/详情/审批都算「合同」这一支，高亮不能只按 path 精确匹配。 */
const activeName = computed(() => {
  const name = String(route.name ?? '')
  if (name === 'workbench' || name === 'review' || name === 'approve') return 'contracts'
  return name
})
</script>

<template>
  <div class="shell">
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

    <nav class="tabbar">
      <router-link
        v-for="item in items"
        :key="item.to"
        :to="item.to"
        class="tab"
        :class="{ active: activeName === item.name }"
      >
        {{ item.label }}
      </router-link>
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
    text-align: center;
    font-size: 13px;
    color: var(--el-text-color-regular);
    text-decoration: none;
  }

  .tab.active {
    color: var(--el-color-primary);
    font-weight: 600;
  }
}
</style>
