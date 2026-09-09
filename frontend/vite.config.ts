import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(() => {
  const apiBaseUrl = process.env.VITE_API_BASE_URL?.trim()
  if (process.env.VERCEL === '1' && apiBaseUrl) {
    const parsed = new URL(apiBaseUrl)
    if (parsed.protocol !== 'https:' || parsed.pathname !== '/' || parsed.search || parsed.hash) {
      throw new Error('VITE_API_BASE_URL must be an HTTPS origin such as https://api.example.com, without /api, a path, query, or fragment.')
    }
  }

  return {
    plugins: [react()],
    server: { proxy: { '/api': 'http://127.0.0.1:8000' } },
  }
})
