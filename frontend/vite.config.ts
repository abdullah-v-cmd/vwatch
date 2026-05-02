import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => ({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        ws: true,
      },
    },
  },
  // In production builds (Docker), these are injected as build args:
  //   VITE_API_URL=/api/v1   (relative – nginx proxies to backend)
  //   VITE_WS_URL=ws://...
  // In dev they fall back to localhost defaults in api.ts.
}))
