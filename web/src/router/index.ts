/**
 * 用户端路由（3 条）。管理端是另一个入口 admin.html，不在这里。
 *
 * 划分依据是「用户此刻在做什么」而不是技术分层：
 *   /                       合同列表      —— 入口，找合同/传合同
 *   /contracts/:id          工作台        —— 三栏：原文 + 发现 + 运行
 *   /reviews/:id            审查详情      —— 结果视角，可分享的落地页
 *
 * 审批页已移到管理端（`admin.html#/approvals/:id`）：拍板的是审核者，发起审查的是提交者，
 * 两种角色不该共用一套页面。留在这里会让审核链接落在「提交者」的界面里。
 *
 * 运维健康与评测看板同样在管理端：它们暴露双栈拓扑与成本结构，属内部视角。
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
    // 兜底落到 404 而不是静默回列表：老审批链接 `/reviews/:id/approve` 被移除后，
    // 悄悄改写成列表会让人以为点错了地方——地址栏和页面都得说出「这里没有页面」。
    // 这条不算业务路由，它不承载任何角色职责，只在地址失效时停住。
    {
      path: '/:pathMatch(.*)*',
      name: 'not-found',
      component: () => import('../views/NotFoundView.vue'),
      meta: { title: '页面不存在' },
    },
  ],
  scrollBehavior: () => ({ top: 0 }),
})

router.afterEach((to) => {
  const title = typeof to.meta.title === 'string' ? to.meta.title : ''
  document.title = title ? `${title} · ClauseGuard` : 'ClauseGuard'
})

export default router
