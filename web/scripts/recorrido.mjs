// Recorrido de la demo en un Chrome real: retiro exitoso, atasco con reversa, enlace caído
// con reintentos hasta el 0430, corte de energía con reinicio, la flota y la vista de celular.
// Deja capturas en web/capturas/ y falla si hay errores de consola o violaciones de CSP.
//
//   TILL_URL=http://127.0.0.1:8766 node scripts/recorrido.mjs
//
// puppeteer-core no es dependencia del proyecto: se toma de PUPPETEER_CORE (ruta al paquete)
// o de node_modules si está instalado. Chrome, de CHROME (por omisión /usr/bin/google-chrome).
import { createRequire } from 'node:module'
import { mkdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const require = createRequire(import.meta.url)
const puppeteer = require(process.env.PUPPETEER_CORE || 'puppeteer-core')
const URL_BASE = process.env.TILL_URL || 'http://127.0.0.1:8766'
const SALIDA = join(dirname(fileURLToPath(import.meta.url)), '..', 'capturas')
mkdirSync(SALIDA, { recursive: true })

const problemas = []
let esperados503 = 0 // el corte de energía responde 503 a propósito (el proceso del cajero murió)
const pasos = []
const paso = (m) => { pasos.push(m); console.log('·', m) }

const navegador = await puppeteer.launch({
  executablePath: process.env.CHROME || '/usr/bin/google-chrome',
  headless: true,
  args: ['--no-first-run', '--no-default-browser-check', '--disable-extensions'],
})

async function nuevaPagina(ancho, alto, movil = false) {
  const page = await navegador.newPage()
  await page.setViewport({ width: ancho, height: alto, deviceScaleFactor: 1, isMobile: movil, hasTouch: movil })
  await page.evaluateOnNewDocument(() => {
    document.addEventListener('securitypolicyviolation', (e) => console.error(`CSP: ${e.violatedDirective} ${e.blockedURI}`))
    try { sessionStorage.clear(); localStorage.clear() } catch {}
  })
  page.on('console', (m) => {
    if (m.type() !== 'error' && m.type() !== 'warning') return
    if (esperados503 > 0 && m.text().includes('status of 503')) { esperados503 -= 1; return }
    problemas.push(`[${m.type()}] ${m.text()}`)
  })
  page.on('pageerror', (e) => problemas.push(`[pageerror] ${e.message}`))
  page.on('requestfailed', (r) => problemas.push(`[requestfailed] ${r.url()} ${r.failure()?.errorText}`))
  page.setDefaultTimeout(15_000)
  return page
}

const texto = (page, t) => page.locator(`::-p-text(${t})`)
const boton = (page, t) => page.locator(`button::-p-text(${t})`)
const esperarTexto = (page, t, timeout = 15_000) => page.waitForFunction(
  (s) => document.body.innerText.includes(s), { timeout, polling: 100 }, t)
const captura = async (page, nombre, selector = null) => {
  const ruta = join(SALIDA, nombre)
  // La barra fija taparía parte del elemento: se suelta sólo para la captura (CSSOM, la CSP lo permite).
  if (selector) {
    await page.evaluate(() => { document.querySelector('.barra').style.position = 'static' })
    await (await page.$(selector)).screenshot({ path: ruta })
    await page.evaluate(() => { document.querySelector('.barra').style.position = '' })
  }
  else await page.screenshot({ path: ruta, fullPage: false })
  paso(`captura ${nombre}`)
}
async function retiro(page, monto) {
  await boton(page, 'Acercar tarjeta de prueba').click()
  await boton(page, 'Retirar efectivo').click()
  await boton(page, monto).click()
  await boton(page, 'Confirmar').click()
}
async function armar(page, falla) {
  await page.locator(`.falla::-p-text(${falla})`).click()
  await page.waitForFunction((f) => [...document.querySelectorAll('.falla[aria-pressed="true"]')].some((b) => b.innerText.includes(f)), {}, falla)
}
async function aReposo(page) {
  const terminar = await page.$('.pantalla button.p-boton:not(.p-activo)')
  if (terminar) await terminar.click()
  await page.waitForSelector('.pantalla[data-pantalla="reposo"]')
}

try {
  // ---------------------------------------------------------------- escritorio
  const page = await nuevaPagina(1440, 1000)
  await page.goto(URL_BASE, { waitUntil: 'networkidle0' })
  await page.waitForSelector('.pantalla[data-pantalla="reposo"]')
  await page.waitForFunction(() => document.querySelectorAll('#tarjeta-prueba option').length === 4)
  paso('cargó la demo: pantalla de reposo y 4 tarjetas de prueba')

  // 1. Retiro exitoso, con teclado hasta el menú
  await page.focus('.lector .boton-primario')
  await page.keyboard.press('Enter')
  await page.waitForSelector('.pantalla[data-pantalla="menu"]')
  const foco = await page.evaluate(() => document.activeElement?.className)
  if (!String(foco).includes('p-saludo')) problemas.push(`[teclado] el foco no fue al título de la pantalla: ${foco}`)
  await page.keyboard.press('Tab')
  await page.keyboard.press('Enter') // «Retirar efectivo» es la primera opción
  await page.waitForSelector('.pantalla[data-pantalla="monto"]')
  paso('teclado: Enter en el lector → menú (foco en el título) → Tab + Enter → monto')
  await boton(page, '$2,500').click()
  await boton(page, 'Confirmar').click()
  await esperarTexto(page, 'Toma tu efectivo')
  await esperarTexto(page, '0210')
  paso('retiro de $2,500: CONFIRMADA, 0200/0210 en la traza')
  await page.evaluate(() => window.scrollTo(0, document.querySelector('.zonas').offsetTop - 70))
  await captura(page, '01-retiro-exitoso.png')
  await boton(page, 'Ya tomé mi efectivo').click()
  await page.waitForSelector('.p-ticket')
  await boton(page, 'Cuadrar el día').click()
  await page.waitForSelector('.cuadre')
  await esperarTexto(page, 'Cuadra')
  paso('comprobante impreso y cuadre del día: CUADRA')
  await captura(page, '02-comprobante-y-cuadre.png', '.zonas')
  await boton(page, 'Terminar').click()
  await page.waitForSelector('.pantalla[data-pantalla="reposo"]')

  // 2. Atasco con reversa
  await armar(page, 'Atasco')
  await retiro(page, '$1,000')
  await esperarTexto(page, 'No pudimos entregarte el efectivo')
  await esperarTexto(page, 'Tu banco confirmó la cancelación')
  await esperarTexto(page, '0430')
  paso('atasco: reversa total 0420 → 0430, no se cobró')
  await page.evaluate(() => window.scrollTo(0, document.querySelector('.zonas').offsetTop - 70))
  await captura(page, '03-atasco-reversa.png')
  await boton(page, 'Ver comprobante').click()
  await page.waitForSelector('.p-ticket')
  await boton(page, 'Terminar').click()

  // 3. Enlace caído: la reversa se reintenta (0421) hasta el 0430
  await armar(page, 'Enlace caído')
  await retiro(page, '$500')
  await esperarTexto(page, 'Avisando a tu banco')
  await page.waitForFunction(() => document.querySelectorAll('.isos .iso-fallo').length >= 3, { timeout: 15_000, polling: 100 })
  paso('enlace caído: 0200 y avisos de reversa sin respuesta, reintentando')
  await page.evaluate(() => window.scrollTo(0, document.querySelector('.zonas').offsetTop - 70))
  await captura(page, '04-enlace-caido-reintentando.png')
  await esperarTexto(page, 'Tu banco confirmó la cancelación', 20_000)
  const mtis = await page.$$eval('.isos > li > .iso-linea:first-child .iso-mti', (xs) => xs.map((x) => x.textContent))
  paso(`enlace caído: mensajes ${mtis.join(' → ')} y luego 0430`)
  if (mtis.join(',') !== '0200,0420,0421,0421,0421') problemas.push(`[enlace] secuencia inesperada: ${mtis.join(',')}`)
  await captura(page, '05-enlace-caido-0430.png', '.zona-taller')
  await boton(page, 'Ver comprobante').click()
  await boton(page, 'Terminar').click()

  // 4. Corte de energía a media operación y reinicio
  await armar(page, 'Corte de energía')
  esperados503 = 1
  await retiro(page, '$2,000')
  await page.waitForSelector('.pantalla[data-pantalla="apagado"]')
  paso('corte de energía: la pantalla se apaga y el cajero queda fuera de servicio')
  await page.evaluate(() => window.scrollTo(0, document.querySelector('.zonas').offsetTop - 70))
  await captura(page, '06-corte-energia.png')
  await boton(page, 'Reiniciar el cajero').click()
  await page.waitForSelector('.pantalla[data-pantalla="recuperado"]')
  await esperarTexto(page, 'reversa total')
  paso('reinicio: recuperación con reversa total; el libro sigue balanceado')
  await boton(page, 'Cuadrar el día').click()
  await esperarTexto(page, 'Cuadra')
  await captura(page, '07-reinicio-y-recuperacion.png', '.zonas')

  // 5. Ingresos y un rechazo del banco (sin captura)
  await page.locator('.pantalla button.p-activo').click() // «Continuar» en la pantalla de recuperación
  await page.waitForSelector('.pantalla[data-pantalla="reposo"]')
  const meter = async (d, n) => { for (let k = 0; k < n; k++) await page.locator(`button[aria-label="Agregar un billete de ${d}"]`).click() }
  await boton(page, 'Acercar tarjeta de prueba').click()
  await boton(page, 'Depositar').click()
  await meter('$500', 2)
  await boton(page, 'Continuar').click()
  await boton(page, 'Confirmar').click()
  await esperarTexto(page, 'Depositamos $1,000.00 en tu cuenta')
  await boton(page, 'Ver comprobante').click()
  await boton(page, 'Otra operación').click()
  await boton(page, 'Pagar mi crédito').click()
  await meter('$1,000', 1)
  await boton(page, 'Continuar').click()
  await boton(page, 'Confirmar').click()
  await esperarTexto(page, 'Abonamos $1,000.00 a tu crédito')
  await esperarTexto(page, '$11,000.00') // el adeudo del crédito bajó en los libros
  await boton(page, 'Ver comprobante').click()
  await boton(page, 'Otra operación').click()
  await boton(page, 'Enviar dinero').click()
  await page.type('#beneficiario', 'Ana López')
  const beneficiario = await page.$eval('#beneficiario', (x) => x.value)
  if (beneficiario !== 'Ana Lopez') problemas.push(`[remesa] el beneficiario debía quedar sin acento (campo 103): ${beneficiario}`)
  await meter('$200', 3)
  await boton(page, 'Continuar').click()
  await boton(page, 'Confirmar').click()
  await esperarTexto(page, 'Clave de cobro')
  await boton(page, 'Ver comprobante').click()
  await boton(page, 'Terminar').click()
  paso('depósito de $1,000, pago de crédito de $1,000 y remesa de $600 con clave de cobro: CONFIRMADAS')
  await page.select('#tarjeta-prueba', '1') // CTA-0002, saldo $1,500
  await retiro(page, '$2,000')
  await esperarTexto(page, 'fondos insuficientes')
  await boton(page, 'Ver comprobante').click()
  await boton(page, 'Terminar').click()
  await page.select('#tarjeta-prueba', '0')
  paso('tarjeta con $1,500 pide $2,000: el banco responde 51 (fondos insuficientes)')
  await boton(page, 'Cuadrar el día').click()
  await page.waitForFunction(() => document.querySelector('.cuadre-estado')?.innerText.includes('Cuadra'), { polling: 100 })
  paso('cuadre tras todas las operaciones: CUADRA')

  // 6. La flota
  await page.locator('.zona-flota button[type="submit"]').click()
  await page.waitForSelector('.flota-resultado', { timeout: 60_000 })
  const tiempo = await page.$eval('.tiles .tile:last-child .tile-valor', (x) => x.textContent)
  paso(`flota de 15 000 cajeros simulada en ${tiempo} (servidor)`)
  await boton(page, 'Correr la muestra').click()
  await page.waitForSelector('.muestra-resultado', { timeout: 60_000 })
  paso(`muestra: ${await page.$eval('.muestra-resultado', (x) => x.textContent)}`)
  const flota = await page.$('.zona-flota')
  await flota.evaluate((x) => x.scrollIntoView())
  await page.hover('.columna:nth-child(3)')
  await captura(page, '08-flota-15000.png', '.zona-flota')

  // 7. Inglés
  await page.evaluate(() => window.scrollTo(0, 0))
  await boton(page, 'EN').click()
  await esperarTexto(page, 'A teller that never loses a peso')
  await boton(page, 'Tap test card').click()
  await page.waitForSelector('.pantalla[data-pantalla="menu"]')
  await page.evaluate(() => window.scrollTo(0, document.querySelector('.zonas').offsetTop - 70))
  await captura(page, '09-ingles.png')
  await page.close()

  // ---------------------------------------------------------------- celular
  const movil = await nuevaPagina(390, 844, true)
  await movil.goto(URL_BASE, { waitUntil: 'networkidle0' })
  await movil.waitForSelector('.pantalla[data-pantalla="reposo"]')
  await boton(movil, 'Acercar tarjeta de prueba').click()
  await movil.waitForSelector('.pantalla[data-pantalla="menu"]')
  const desborde = await movil.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
  if (desborde > 0) problemas.push(`[celular] desborde horizontal de ${desborde}px`)
  const orden = await movil.evaluate(() => [...document.querySelectorAll('.zonas > section')].map((s) => s.className))
  paso(`celular: sin desborde horizontal (${desborde}px); orden ${orden.map((c) => c.split(' ')[1]).join(' → ')}`)
  await movil.evaluate(() => window.scrollTo(0, document.querySelector('.zonas').offsetTop - 60))
  await captura(movil, '10-celular.png')
  await movil.close()
} catch (e) {
  problemas.push(`[recorrido] ${e.stack || e}`)
} finally {
  await navegador.close()
}

console.log(`\n${pasos.length} pasos; ${problemas.length} problema(s)`)
for (const p of problemas) console.log('  ✗', p)
process.exit(problemas.length ? 1 : 0)
