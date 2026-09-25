import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The backend runs on 8002; proxy keeps the frontend same-origin (SSE included).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8002', changeOrigin: true },
    },
  },
})
