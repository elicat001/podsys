import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发时把 /api、/files 代理到后端。
// 默认连**本机后端 10000**(本地联调用)。先起后端:
//   backend/.venv/Scripts/python.exe -m uvicorn app.main:app --port 10000
// 想连线上后端调试:set VITE_API_TARGET=https://pod.kejing.online && npm run dev
const API_TARGET = process.env.VITE_API_TARGET || 'http://127.0.0.1:10000'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: API_TARGET, changeOrigin: true },
      '/files': { target: API_TARGET, changeOrigin: true },
    },
  },
  build: { outDir: 'dist', chunkSizeWarningLimit: 1500 },
})
