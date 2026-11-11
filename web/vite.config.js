import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    // 开发时把 /api 代理到后端，解决跨域，生产环境由后端托管静态文件或 Nginx 反代
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
    // 忽略编辑器/工具写入时产生的临时文件，避免 watcher EBUSY 崩溃
    watch: {
      ignored: (p) =>
        p.includes('.tmp') ||
        p.includes('.tmpdir') ||
        p.includes('~$') ||
        p.includes('#') && /\.[jt]sx?$/.test(p),
    },
  },
})
