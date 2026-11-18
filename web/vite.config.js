import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// En desarrollo, /api va a uvicorn (api.index:app, que ya monta las rutas bajo /api).
// En producción el front es estático y /api lo atiende la función de Python del mismo origen.
const API = process.env.TILL_API || 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { '/api': { target: API, changeOrigin: false } } },
  preview: { port: 4173, proxy: { '/api': { target: API, changeOrigin: false } } },
  build: {
    // Nada en línea: la CSP de producción es default-src 'self' (sin data: ni estilos en línea).
    assetsInlineLimit: 0,
    sourcemap: false,
    target: 'es2020',
  },
})
