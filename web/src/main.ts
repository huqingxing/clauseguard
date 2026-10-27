import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import App from './App.vue'
import router from './router'

/**
 * 管理端已拆成独立入口（admin.html）。老书签 / 群里传过的链接仍在 /admin、/eval 上，
 * 这里在挂载前就把它们送到新入口——比让它被 catch-all 静默重定向到首页要好：
 * 用户点运维链接却看到合同列表，会以为系统坏了。
 *
 * 只认 `/admin` 两个确切写法，不用 startsWith：`/administrator` 之类同样以 /admin 开头，
 * 却和我们的入口毫无关系，误送过去就是拿别人的路径做了我们的路由判断。
 */
const path = window.location.pathname
if (path === '/admin' || path === '/admin/') {
  window.location.replace('/admin.html')
} else if (path === '/eval') {
  window.location.replace('/admin.html#/eval')
} else {
  createApp(App).use(createPinia()).use(router).use(ElementPlus).mount('#app')
}
