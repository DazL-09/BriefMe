import { fileURLToPath } from 'node:url'
import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv, type ProxyOptions } from 'vite'

// API key Langflow dibaca dari ../.env (root proyek) dan disuntikkan oleh proxy
// dev — frontend tidak pernah menyimpan key.
const repoRoot = fileURLToPath(new URL('..', import.meta.url))

function makeProxy(langflowUrl: string, apiKey: string, historyUrl: string): Record<string, ProxyOptions> {
  return {
    // URUTAN PENTING: '/api/history' (Express/MongoDB) harus didaftarkan
    // SEBELUM '/api' (Langflow) — vite mencocokkan key secara berurutan.
    '/api/history': {
      target: historyUrl,
      changeOrigin: true,
    },
    '/api': {
      target: langflowUrl,
      changeOrigin: true,
      configure(proxy) {
        if (!apiKey) return
        proxy.on('proxyReq', (proxyReq) => {
          proxyReq.setHeader('x-api-key', apiKey)
        })
      },
    },
  }
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, repoRoot, '')
  const apiKey = env.LANGFLOW_API_KEY ?? ''
  const langflowUrl = env.LANGFLOW_URL ?? 'http://127.0.0.1:7860'
  const historyUrl = env.HISTORY_URL ?? 'http://127.0.0.1:8790'
  const proxy = makeProxy(langflowUrl, apiKey, historyUrl)
  return {
    plugins: [react()],
    server: { proxy },
    preview: { proxy },
  }
})
