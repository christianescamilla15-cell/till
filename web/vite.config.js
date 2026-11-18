import { readFileSync } from 'node:fs'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// La API de Till corre en el navegador (Pyodide en un worker, ver src/motor/). La versión de Pyodide sale del
// paquete npm y nombra la carpeta que prepara scripts/preparar_motor.mjs.
const PYODIDE = JSON.parse(readFileSync(new URL('./node_modules/pyodide/package.json', import.meta.url), 'utf8')).version

// Con VITE_TILL_MOTOR=servidor la página usa en cambio la API de uvicorn (api.index:app, rutas bajo /api).
const API = process.env.TILL_API || 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  define: { __PYODIDE__: JSON.stringify(PYODIDE) },
  worker: { format: 'es' },
  server: { port: 5173, proxy: { '/api': { target: API, changeOrigin: false } } },
  preview: { port: 4173, proxy: { '/api': { target: API, changeOrigin: false } } },
  build: {
    // Nada en línea: la CSP de producción es default-src 'self' (sin data: ni estilos en línea).
    assetsInlineLimit: 0,
    sourcemap: false,
    target: 'es2020',
  },
})
