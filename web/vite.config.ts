import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 红线：前端不直连 Python(8000)，/api 一律代理到 Java(8080)
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8080',
        changeOrigin: true,
      },
    },
  },
})
