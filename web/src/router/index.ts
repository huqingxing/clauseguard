/**
 * 用户端路由（4 条）。管理端是另一个入口 admin.html，不在这里。
 *
 * 划分依据是「用户此刻在做什么」而不是技术分层：
 *   /                       合同列表      —— 入口，找合同/传合同
 *   /contracts/:id          工作台        —— 三栏：原文 + 发现 + 运行
 *   /reviews/:id            审查详情      —— 结果视角，可分享的落地页
 *   /reviews/:id/approve    审批页        —— 375px 优先，手机上逐条批
 *
 * 工作台与详情分开的理由：工作台要发起运行（写），详情只读；
 * 把「分享一条链接给同事看结果」与「发起一次新运行」放在同一页面会误操作。
 *
 * 运维健康与评测看板移出本文件：它们暴露双栈拓扑与成本结构，属内部视角，
 * 现在由独立的 admin.html 承载（见 src/admin/AdminApp.vue）。
 */

import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'contracts',
      component: () => import('../views/ContractListView.vue'),
      meta: { title: '合同' },
    },
    {
      path: '/contracts/:id',
      name: 'workbench',
      component: () => import('../views/WorkbenchView.vue'),
      props: true,
      meta: { title: '审查工作台' },
    },
    {
      path: '/reviews/:id',
      name: 'review',
      component: () => import('../views/ReviewDetailView.vue'),
      props: true,
      meta: { title: '审查详情' },
    },
    {
      path: '/reviews/:id/approve',
      name: 'approve',
      component: () => import('../views/ApprovalView.vue'),
      props: true,
      meta: { title: '人工审批' },
    },
    // 旧链接兜底：/admin、/eval 已移到独立入口，这里交给 main.ts 做 302 到 admin.html。
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
  scrollBehavior: () => ({ top: 0 }),
})

router.afterEach((to) => {
  const title = typeof to.meta.title === 'string' ? to.meta.title : ''
  document.title = title ? `${title} · ClauseGuard` : 'ClauseGuard'
})

export default router
