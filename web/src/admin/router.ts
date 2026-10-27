/**
 * 管理端路由（第 4 条路由带 :id，手切 hash 已经不够：需要参数、需要浏览器前进后退）。
 *
 * 为什么是 hash 而不是 history：管理端是独立 HTML 入口（admin.html）。
 * 用 history 就得让 nginx 为 `/health`、`/approvals/xxx` 这些路径各配一条回退，
 * 否则刷新直接 404；hash 模式下服务端永远只看到 `/admin`，回退配置一条都不用加。
 *
 * 四条路由对应两种视角：
 *   #/health        运维健康    —— 谁探谁、依赖通不通
 *   #/eval          评测看板    —— 成本结构
 *   #/approvals     待审批队列  —— 审核者的入口（阶段 5 建页面）
 *   #/approvals/:id 审批详情    —— 从用户端迁来，链接可以直接发到手机
 */
import { createRouter, createWebHashHistory } from 'vue-router'
import AdminView from '../views/AdminView.vue'
import EvalView from '../views/EvalView.vue'
import ApprovalsView from '../views/ApprovalsView.vue'
import ApprovalView from '../views/ApprovalView.vue'

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', redirect: '/health' },
    { path: '/health', name: 'health', component: AdminView, meta: { title: '运维健康' } },
    { path: '/eval', name: 'eval', component: EvalView, meta: { title: '评测看板' } },
    { path: '/approvals', name: 'approvals', component: ApprovalsView, meta: { title: '待审批' } },
    {
      path: '/approvals/:id',
      name: 'approval',
      component: ApprovalView,
      // props: true 把 :id 变成组件的 props：审批页只认一个 id，
      // 从 URL 拿比在组件里再读一次 route 更容易单测。
      props: true,
      meta: { title: '审批详情' },
    },
    // 拼错的 hash 收敛到运维健康，不要停在白屏。
    { path: '/:pathMatch(.*)*', redirect: '/health' },
  ],
})

/**
 * 标题放在路由里：首次导航也会走 afterEach，所以首屏就是对的，
 * 不需要在组件里再 watch 一遍（阶段 3 那个 watch 缺 immediate，首屏标题是旧的）。
 */
router.afterEach((to) => {
  const title = typeof to.meta.title === 'string' ? to.meta.title : ''
  document.title = title ? `${title} · ClauseGuard 管理端` : 'ClauseGuard 管理端'
})

export default router
