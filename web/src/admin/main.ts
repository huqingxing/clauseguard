import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import AdminApp from './AdminApp.vue'
import router from './router'
import { toHashRoute } from './legacy-hash'

/**
 * 老链接迁移必须在挂载前做完：`#eval`（阶段 3 的手切 hash）不是任何一条路由的 path，
 * 挂载后再改，路由已经按旧 hash 解析过一次，会先闪一下运维屏再跳走。
 * 用 replace 而不是赋值 location.hash：别在历史里留一条指向旧格式的记录，
 * 否则用户按一次后退又回到要重定向的地址。
 */
const initialHash = toHashRoute(window.location.hash)
if (initialHash !== window.location.hash) {
  window.location.replace(`${window.location.pathname}${window.location.search}${initialHash}`)
}

createApp(AdminApp).use(router).use(ElementPlus).mount('#admin')
