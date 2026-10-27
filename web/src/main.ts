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
 */
const path = window.location.pathname
if (path.startsWith('/admin')) {
  window.location.replace('/admin.html')
} else if (path === '/eval') {
  window.location.replace('/admin.html#eval')
} else {
  createApp(App).use(createPinia()).use(router).use(ElementPlus).mount('#app')
}
