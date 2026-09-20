import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 인터넷 없이 동작해야 하므로 외부 CDN 을 쓰지 않는다. 폰트는 public/fonts 에
// 번들링되어 있고, 빌드 결과는 FastAPI 가 같은 포트로 서빙한다(결정-12).
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      '/ws': { target: 'ws://127.0.0.1:8000', ws: true },
    },
  },
  build: {
    outDir: 'dist',
    assetsDir: 'assets',
    chunkSizeWarningLimit: 900,
  },
})
