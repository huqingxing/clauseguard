import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 红线：前端不直连 Python(8000)，/api 一律代理到 Java(8080)
export default defineConfig({
  plugins: [
    vue(),
    {
      // dev 下让 /admin 与生产同形：MPA 入口文件是 admin.html，但没人愿意记 .html。
      // 只改请求路径、不动响应；生产仍由 nginx 把 /admin 映射到 admin.html。
      name: 'admin-entry-alias',
      configureServer(server) {
        server.middlewares.use((req, _res, next) => {
          // 项目没装 @types/node，Connect 的 IncomingMessage 在这份类型里没有 url，收窄一次即可。
          const incoming = req as unknown as { url?: string }
          if (incoming.url && /^\/admin\/?(?:[?#].*)?$/.test(incoming.url)) {
            incoming.url = '/admin.html'
          }
          next()
        })
      },
    },
  ],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8080',
        changeOrigin: true,
      },
    },
  },
  build: {
    rollupOptions: {
      // 两个 HTML 入口 = 两套产物：用户端包里不再有 /admin、/eval 的任何引用，
      // 管理端的 EvalView/AdminView 也只进 admin 包。
      input: {
        main: 'index.html',
        admin: 'admin.html',
      },
    },
  },
})
