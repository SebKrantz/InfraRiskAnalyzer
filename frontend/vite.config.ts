import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

// https://vitejs.dev/config/
export default defineConfig(({ command, mode }) => {
  // `vite dev` only: MapView reads the CARTO key from VITE_CARTO_API_KEY, so
  // default that to the CARTO_API_KEY the backend reads from backend/.env. A
  // build never embeds the key — the backend injects it at serve time.
  const devCartoKey =
    command === 'serve'
      ? process.env.VITE_CARTO_API_KEY ??
        loadEnv(mode, path.resolve(__dirname, '../backend'), 'CARTO_').CARTO_API_KEY
      : undefined

  return {
    plugins: [react()],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
      },
    },
    define: devCartoKey
      ? { 'import.meta.env.VITE_CARTO_API_KEY': JSON.stringify(devCartoKey) }
      : {},
    server: {
      port: 5173,
      proxy: {
        // Override with BACKEND_URL when port 8000 is taken by another app.
        '/api': {
          target: process.env.BACKEND_URL || 'http://localhost:8000',
          changeOrigin: true,
        }
      }
    }
  }
})
