// Un retiro completo en la demo, como lo haría una persona: la API corre en el navegador (Pyodide en un worker),
// así que la prueba también confirma que la página no le pide nada a un servidor de API.
import { expect, test } from '@playwright/test'

const PAN = '4111111111111111'

test('retiro completo de $2,500: tarjeta, monto, efectivo, comprobante y cuadre', async ({ page }) => {
  const problemas = []
  const llamadasAlServidor = []
  page.on('console', (m) => { if (m.type() === 'error') problemas.push(m.text()) })
  page.on('pageerror', (e) => problemas.push(e.message))
  page.on('request', (r) => { if (new URL(r.url()).pathname.startsWith('/api/')) llamadasAlServidor.push(r.url()) })
  await page.addInitScript(() => {
    document.addEventListener('securitypolicyviolation', (e) => console.error(`CSP: ${e.violatedDirective} ${e.blockedURI}`))
  })

  await page.goto('/')
  await expect(page.locator('.motor')).toHaveAttribute('data-etapa', 'listo', { timeout: 120_000 })
  await expect(page.locator('.pantalla')).toHaveAttribute('data-pantalla', 'reposo')

  await page.getByRole('button', { name: 'Acercar tarjeta de prueba' }).click()
  await expect(page.locator('.pantalla')).toHaveAttribute('data-pantalla', 'menu')
  await page.getByRole('button', { name: /Retirar efectivo/ }).click()
  await page.getByRole('button', { name: '$2,500', exact: true }).click()
  await page.getByRole('button', { name: 'Confirmar', exact: true }).click()

  await expect(page.getByText('Toma tu efectivo')).toBeVisible()
  await expect(page.locator('.isos')).toContainText('0210')  // la traza ISO 8583: 0200 aprobada por el banco
  await page.getByRole('button', { name: 'Ya tomé mi efectivo' }).click()

  const ticket = page.locator('.p-ticket')
  await expect(ticket).toContainText('MONTO $2,500.00')
  await expect(ticket).toContainText('411111******1111')
  await expect(ticket).toContainText('ESTADO CONFIRMADA')

  await page.getByRole('button', { name: 'Cuadrar el día' }).click()
  await expect(page.locator('.cuadre-estado')).toContainText('Cuadra')

  expect(await page.locator('body').innerText()).not.toContain(PAN)
  expect(llamadasAlServidor, 'la demo publicada no tiene servidor de API').toEqual([])
  expect(problemas, 'errores de consola o violaciones de CSP').toEqual([])
})
