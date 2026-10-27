<script setup lang="ts">
/**
 * 管理端外壳（独立入口 admin.html，与用户端 index.html 不共享任何导航）。
 *
 * 为什么物理拆成两个入口，而不是在同一个侧栏里加两项：
 * 运维健康暴露的是双栈拓扑（Java/Python/MySQL/Redis 的连通与耗时），评测看板暴露的是成本结构，
 * 这两屏都不是给签约方看的。放在用户端同一个菜单里，等于让每个用户知道内部有几层服务。
 *
 * 为什么还要在这层加登录：入口分开只是「看不见」，不是「进不来」。
 * /api/v1/admin/** 的权限在后端（ADMIN 角色），这里的登录卡只做两件事——
 * 把凭证带给后端，以及在后端说「不行」时把人挡在门外。
 */
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { getAdminHealth } from '../api/admin'
import { ApiError, AuthError, setUnauthorizedHandler } from '../api/http'
import { clearCredential, getUsername, hasCredential, setCredential } from '../api/auth'

/**
 * 侧栏三项：运维健康、评测看板、待审批。审批详情（/approvals/:id）不占菜单项——
 * 它是从队列点进去的深链接，给它一个并列的入口会让人以为还有「第四种活儿」。
 */
const route = useRoute()

const NAV = [
  { to: '/health', label: '运维健康', hint: '双栈探活' },
  { to: '/eval', label: '评测看板', hint: '用量与质量' },
  { to: '/approvals', label: '待审批', hint: '审核者待办' },
]

/**
 * 高亮要按前缀判：`/approvals/xxx` 属于「待审批」这一支，
 * 用 route.path === item.to 精确比较，点进详情后侧栏会一项都不亮。
 */
function isActive(path: string): boolean {
  return route.path === path || route.path.startsWith(`${path}/`)
}

/**
 * 三态：有凭证但要先向后端确认（checking）、没有可用凭证（login）、已进入（ready）。
 * 之所以不是「有凭证就直接 ready」：凭证可能在这次打开之前就已失效（改过密码、
 * 后端重启换了账号），不先确认就会先渲染出一个满屏 401 的骨架再闪回登录卡。
 */
const phase = ref<'checking' | 'login' | 'ready'>(hasCredential() ? 'checking' : 'login')
const form = ref({ username: getUsername(), password: '' })
const account = ref(getUsername())
const loginError = ref('')
const submitting = ref(false)

/**
 * 用一次真实 admin 请求验证凭证：状态码本身就是答案，不需要额外的「验证登录」接口，
 * 也就不会出现「验证接口说行、真实接口说不行」的两套口径。
 */
async function probe(): Promise<string> {
  try {
    await getAdminHealth()
    return ''
  } catch (err) {
    if (err instanceof AuthError) return '账号或密码错误'
    // 认证通过但角色不够：这跟密码错不是一回事，说错了会让人一直换密码。
    if (err instanceof ApiError && err.status === 403) return '该账号没有管理员权限（需要 ADMIN 角色）'
    return err instanceof Error ? `无法连接后端：${err.message}` : '无法连接后端'
  }
}

/**
 * 任何 admin 请求 401 都意味着凭证已失效，统一在这里回到登录卡。
 * 交给各页面自己处理的话，10 秒一轮的探活会把 401 渲染成「健康检查失败」，
 * 让人以为服务挂了，而实际上只是该重新登录。
 */
setUnauthorizedHandler(() => {
  clearCredential()
  form.value.password = ''
  loginError.value = '登录状态已失效，请重新登录'
  phase.value = 'login'
})

onMounted(async () => {
  // 没有凭证就停在登录卡，一个请求都不发——不给未认证会话任何打接口的机会。
  if (!hasCredential()) return

  const problem = await probe()
  if (!problem) {
    phase.value = 'ready'
    return
  }
  // 401 已被上面的处理器翻到登录卡；其余失败（后端不可达）在这里补位。
  if (phase.value === 'checking') {
    phase.value = 'login'
    loginError.value = problem
  }
})

async function login() {
  const username = form.value.username.trim()
  if (!username || !form.value.password) {
    loginError.value = '请输入账号和密码'
    return
  }

  submitting.value = true
  loginError.value = ''
  // 先落凭证再试请求：fetch 层只认「当前凭证」，不为登录这一发加特例参数，
  // 否则登录路径与后续请求走的不是同一段代码，验过的等于没验。
  setCredential(username, form.value.password)

  const problem = await probe()
  submitting.value = false
  if (problem) {
    clearCredential()
    loginError.value = problem
    return
  }

  account.value = username
  form.value.password = ''
  phase.value = 'ready'
}

function logout() {
  clearCredential()
  account.value = ''
  form.value.password = ''
  loginError.value = ''
  phase.value = 'login'
  // 不动 hash：重新登录后回到原来那一屏
}
</script>

<template>
  <!-- 未登录时整屏只有登录卡：此时渲染侧栏等于告诉未认证的人「这里有几屏」，
       也会让人误以为已经进来了。 -->
  <div v-if="phase === 'login'" class="gate">
    <form class="gate-card" @submit.prevent="login">
      <div class="gate-brand">
        <span class="mark">CG</span>
        <span class="brand-text">
          <strong>ClauseGuard</strong>
          <em>管理端</em>
        </span>
      </div>

      <p class="gate-sub">需要管理员账号。凭证只存在本标签页，关掉即失效。</p>

      <label class="field">
        <span>账号</span>
        <el-input
          v-model="form.username"
          name="username"
          autocomplete="username"
          placeholder="管理员账号"
          :disabled="submitting"
        />
      </label>

      <label class="field">
        <span>密码</span>
        <el-input
          v-model="form.password"
          name="password"
          type="password"
          show-password
          autocomplete="current-password"
          placeholder="密码"
          :disabled="submitting"
        />
      </label>

      <el-alert v-if="loginError" type="error" :closable="false" show-icon :title="loginError" />

      <el-button type="primary" native-type="submit" :loading="submitting" class="gate-submit">
        登录
      </el-button>

      <a class="gate-back" href="/">← 返回用户端</a>
    </form>
  </div>

  <div v-else-if="phase === 'checking'" class="gate">
    <p class="gate-hint">正在验证登录状态…</p>
  </div>

  <div v-else class="admin-shell">
    <aside class="side">
      <div class="brand">
        <span class="mark">CG</span>
        <span class="brand-text">
          <strong>ClauseGuard</strong>
          <em>管理端</em>
        </span>
      </div>

      <nav class="side-nav">
        <!-- 用 router-link 而不是自己 click 改状态：浏览器前进/后退、中键新开标签、
             复制地址，这三件事手写的 active 一个都接不住。 -->
        <router-link
          v-for="item in NAV"
          :key="item.to"
          :to="item.to"
          class="side-item"
          :class="{ active: isActive(item.to) }"
        >
          <span>{{ item.label }}</span>
          <small>{{ item.hint }}</small>
        </router-link>
      </nav>

      <div class="account">
        <span class="account-name" :title="account">当前账号：{{ account }}</span>
        <button type="button" class="logout" @click="logout">退出</button>
      </div>

      <!-- 用真实 <a> 而不是 router-link：这是另一个 HTML 入口，不是本应用的路由。 -->
      <a class="back" href="/">← 返回用户端</a>
    </aside>

    <main class="main">
      <!-- 内容区交给路由。放在 ready 分支里是有意的：未登录时 router-view 不挂载，
           各页面 onMounted 里的拉数据/订阅流一条都不会发出去。 -->
      <router-view />
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
  /* border-box 让「216px 侧栏」就是总宽：审批页的底部操作栏按这个数字对齐，
     否则 padding 会把它撑到 244，底栏就会压住侧栏最后 28px。
     min-width:0 是同一件事的另一半：flex 项的自动最小宽度会按内容把 216 顶大。 */
  box-sizing: border-box;
  min-width: 0;
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
  text-decoration: none;
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

.account {
  display: flex;
  gap: 8px;
  align-items: center;
  justify-content: space-between;
  margin-top: auto;
  padding: 8px 12px;
  border-top: 1px solid #2b3341;
}

.account-name {
  overflow: hidden;
  color: #8b93a5;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.logout {
  flex: 0 0 auto;
  padding: 0;
  border: 0;
  background: transparent;
  color: #6f7789;
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}

.logout:hover {
  color: #e6a23c;
}

.back {
  padding: 0 12px 4px;
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

/* 登录卡：与侧栏同色系，让人一眼看出这里是管理端而不是用户端。 */
.gate {
  display: grid;
  place-items: center;
  min-height: 100vh;
  padding: 24px;
  background: #1f2430;
}

.gate-card {
  display: flex;
  flex-direction: column;
  gap: 14px;
  width: 100%;
  max-width: 340px;
  padding: 26px 24px 20px;
  border-radius: 14px;
  background: #fff;
}

.gate-brand {
  display: flex;
  gap: 10px;
  align-items: center;
}

.gate-brand .brand-text strong {
  color: #1f2430;
}

.gate-brand .brand-text em {
  color: #8b93a5;
}

.gate-sub {
  margin: 0;
  color: #6f7789;
  font-size: 12px;
  line-height: 1.6;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.field > span {
  color: #4b5566;
  font-size: 12px;
}

.gate-submit {
  width: 100%;
}

.gate-back {
  color: #9aa3b2;
  font-size: 12px;
  text-align: center;
  text-decoration: none;
}

.gate-back:hover {
  color: #409eff;
}

.gate-hint {
  color: #8b93a5;
  font-size: 13px;
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

  .account {
    margin-top: 0;
    justify-content: flex-end;
  }

  .back {
    text-align: right;
  }

  .main {
    padding: 12px;
  }
}
</style>

<style>
/* 管理端是独立入口，不经过用户端的全局样式：body 默认的 8px 边距不清掉，
   深色侧栏四周会留白边，而底部操作栏是 fixed（按视口定位），两边就错开 8px。 */
body {
  margin: 0;
}
</style>
