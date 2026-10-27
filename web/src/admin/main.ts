import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import AdminApp from './AdminApp.vue'

// 管理端是独立入口（admin.html），不挂路由：只有 2 个视图，用 hash 记状态即可，
// 引入 vue-router 只会多一层「刷新要配置回退」的部署负担。
createApp(AdminApp).use(ElementPlus).mount('#admin')
