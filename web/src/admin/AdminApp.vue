<script setup lang="ts">
/**
 * 管理端外壳（独立入口 admin.html，与用户端 index.html 不共享任何导航）。
 *
 * 为什么物理拆成两个入口，而不是在同一个侧栏里加两项：
 * 运维健康暴露的是双栈拓扑（Java/Python/MySQL/Redis 的连通与耗时），评测看板暴露的是成本结构，
 * 这两屏都不是给签约方看的。放在用户端同一个菜单里，等于让每个用户知道内部有几层服务。
 *
 * 拆开之后用户端产物里不再有任何指向 /admin、/eval 的引用（router 少 2 条、导航少 2 项），
 * 管理端代码也不会被打进用户首包——这是「分开」能立刻拿到的好处。
 *
 * 但要说清楚：这只分开入口，不构成权限隔离。真正的门在后端 /api/v1/admin/**（目前未鉴权，
 * 见 README 已知限制「没有认证」），换掉这个页面的 URL 并不能阻止任何人直接调接口。
 */
import { ref, watch } from 'vue'
import EvalView from '../views/EvalView.vue'
import AdminView from '../views/AdminView.vue'

type Tab = 'admin' | 'eval'

const TABS: Array<{ key: Tab; label: string; hint: string }> = [
  { key: 'admin', label: '运维健康', hint: '双栈探活' },
  { key: 'eval', label: '评测看板', hint: '用量与质量' },
]

/** 用 hash 记当前视图：刷新和复制链接都能回到同一屏，且不需要 router + 服务端回退配置。 */
function initialTab(): Tab {
  return location.hash === '#eval' ? 'eval' : 'admin'
}

const active = ref<Tab>(initialTab())

watch(active, (tab) => {
  // 用 replaceState 而不是 location.hash：后者会把焦点/滚动跳到锚点。
  history.replaceState(null, '', `#${tab}`)
  document.title = `${TABS.find((t) => t.key === tab)?.label ?? '管理端'} · ClauseGuard 管理端`
})
</script>

<template>
  <div class="admin-shell">
    <aside class="side">
      <div class="brand">
        <span class="mark">CG</span>
        <span class="brand-text">
          <strong>ClauseGuard</strong>
          <em>管理端</em>
        </span>
      </div>

      <nav class="side-nav">
        <button
          v-for="tab in TABS"
          :key="tab.key"
          type="button"
          class="side-item"
          :class="{ active: active === tab.key }"
          @click="active = tab.key"
        >
          <span>{{ tab.label }}</span>
          <small>{{ tab.hint }}</small>
        </button>
      </nav>

      <!-- 用真实 <a> 而不是 router-link：这是另一个 HTML 入口，不是本应用的路由。 -->
      <a class="back" href="/">← 返回用户端</a>
    </aside>

    <main class="main">
      <AdminView v-if="active === 'admin'" />
      <EvalView v-else />
    </main>
  </div>
</template>

<style scoped>
/* 深色侧栏是有意的：管理端与用户端长得像，就容易在里面点错东西（用户端是浅色侧的深色字）。 */
.admin-shell {
  display: flex;
  min-height: 100vh;
  background: var(--el-bg-color-page);
}

.side {
  display: flex;
  flex: 0 0 216px;
  flex-direction: column;
  gap: 20px;
  width: 216px;
  padding: 20px 14px;
  background: #1f2430;
}

.brand {
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 0 6px;
}

.mark {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  border-radius: 9px;
  background: #409eff;
  color: #fff;
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0.5px;
}

.brand-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  line-height: 1.2;
}

.brand-text strong {
  color: #eef1f6;
  font-size: 15px;
}

.brand-text em {
  color: #8b93a5;
  font-size: 11px;
  font-style: normal;
  letter-spacing: 2px;
}

.side-nav {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.side-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
  align-items: flex-start;
  padding: 9px 12px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: #b6bdcc;
  font: inherit;
  font-size: 14px;
  text-align: left;
  cursor: pointer;
}

.side-item small {
  color: #6f7789;
  font-size: 11px;
}

.side-item:hover {
  background: #272d3b;
}

.side-item.active {
  background: #2b3547;
  color: #fff;
  font-weight: 600;
}

.side-item.active small {
  color: #8fa6c8;
}

.back {
  margin-top: auto;
  padding: 8px 12px;
  color: #6f7789;
  font-size: 12px;
  text-decoration: none;
}

.back:hover {
  color: #b6bdcc;
}

.main {
  flex: 1;
  min-width: 0;
  padding: 20px 24px 32px;
}

@media (max-width: 900px) {
  .admin-shell {
    flex-direction: column;
  }

  .side {
    flex: 0 0 auto;
    width: auto;
    gap: 12px;
  }

  .brand-text em {
    letter-spacing: 1px;
  }

  .side-nav {
    flex-direction: row;
  }

  .side-item {
    flex: 1;
    align-items: center;
  }

  .back {
    margin-top: 0;
    text-align: right;
  }

  .main {
    padding: 12px;
  }
}
</style>
