import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// In development the API runs on :8000 and Vite proxies /api to it.
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    proxy: { '/api': 'http://localhost:8000' },
  },
})
