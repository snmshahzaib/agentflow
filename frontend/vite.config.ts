import { fileURLToPath, URL } from 'node:url'

import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { visualizer } from 'rollup-plugin-visualizer'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    // Bundle composition report, written only when ANALYSE=1 so a normal
    // build stays quiet and fast.
    ...(process.env.ANALYSE
      ? [visualizer({ filename: 'bundle-report.json', template: 'raw-data', gzipSize: true })]
      : []),
  ],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    port: 5173,
    // The dev server proxies /api so the browser sees one origin and the
    // session cookie behaves exactly as it does in production.
    proxy: {
      '/api': { target: 'http://localhost:8000', changeOrigin: true },
      '/health': { target: 'http://localhost:8000', changeOrigin: true },
      '/ready': { target: 'http://localhost:8000', changeOrigin: true },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    css: false,
    // Component tests only. The e2e directory is Playwright's: it drives a
    // real browser against a running stack, and Vitest picking those files up
    // fails with "Playwright Test did not expect test.describe()".
    include: ['src/**/*.test.{ts,tsx}'],
    exclude: ['e2e/**', 'node_modules/**', 'dist/**'],
  },
})
