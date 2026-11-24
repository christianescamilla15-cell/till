// E2E de la demo: Playwright contra el build LOCAL servido con la misma CSP de producción (api/index.py sirve
// web/dist con los encabezados de vercel.json). Nunca contra la demo publicada.
//
//   npm ci --prefix web && npm run build --prefix web      # el build que se prueba
//   npm ci --prefix e2e && npx --prefix e2e playwright install --with-deps chromium
//   TILL_PYTHON=.venv/bin/python npm test --prefix e2e
//
// TILL_PYTHON: el Python con requirements.txt instalado (sirve el build con uvicorn). PW_CANAL=chrome usa el
// Chrome del sistema en vez del Chromium de Playwright.
import { existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig, devices } from '@playwright/test'

const RAIZ = join(dirname(fileURLToPath(import.meta.url)), '..')
if (!existsSync(join(RAIZ, 'web', 'dist', 'index.html'))) {
  throw new Error('no hay build de la demo: corre antes `npm run build --prefix web`')
}
const PUERTO = Number(process.env.TILL_PUERTO || 8766)

export default defineConfig({
  testDir: '.',
  testMatch: /.*\.e2e\.js$/,
  timeout: 180_000,
  retries: 0,
  workers: 1,
  reporter: [['list'], ['html', { open: 'never', outputFolder: 'playwright-report' }]],
  use: {
    baseURL: `http://127.0.0.1:${PUERTO}`,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'escritorio',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 1000 }, channel: process.env.PW_CANAL || undefined },
    },
  ],
  webServer: {
    command: `${process.env.TILL_PYTHON || 'python3'} -m uvicorn api.index:app --host 127.0.0.1 --port ${PUERTO}`,
    cwd: RAIZ,
    url: `http://127.0.0.1:${PUERTO}/`,
    timeout: 60_000,
    reuseExistingServer: false,
    env: { TILL_DATOS: join(tmpdir(), `till-e2e-${PUERTO}`) },
  },
})
