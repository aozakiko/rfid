import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  base: '/ui/',
  server: {
    port: 5173,
    strictPort: true,
    proxy: Object.fromEntries(
      ['/api', '/legacy', '/static'].map((path) => [path, 'http://127.0.0.1:8765']),
    ),
  },
  build: {
    sourcemap: false,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('echarts') || id.includes('zrender')) return 'charts'
          if (id.includes('element-plus')) return 'components'
          if (id.includes('node_modules')) return 'vendor'
        },
      },
    },
  },
})
