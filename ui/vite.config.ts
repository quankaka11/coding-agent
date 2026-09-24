import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Build đổ thẳng vào gói python để `pip install` mang theo giao diện.
export default defineConfig({
  plugins: [react()],
  base: './',
  build: { outDir: '../e2e_agent/web/static', emptyOutDir: true },
  server: { proxy: { '/api': 'http://127.0.0.1:8080' } },
})
