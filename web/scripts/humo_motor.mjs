// Prueba de humo del motor publicado: la API de Till corriendo en Pyodide (CPython compilado a WebAssembly) bajo
// el MISMO puente ASGI que usa el worker del navegador (src/motor/puente.py), pero en Node para poder correrla en el
// CI sin navegador. Carga el intérprete, el lockfile recortado, los paquetes y el código de till desde la carpeta
// del motor ya preparada (la que se publica), igual que src/motor/motor.worker.js, y hace llamadas de humo:
// salud, catálogo, un retiro completo con su traza ISO 8583 y su comprobante, y el cuadre del día.
//
//   npm run motor --prefix web && node web/scripts/humo_motor.mjs          # contra web/public/motor
//   npm run build --prefix web && node web/scripts/humo_motor.mjs dist     # contra web/dist/motor (lo que se sube)
//
// Sale con 1 si cualquier llamada no responde lo esperado.
import assert from 'node:assert/strict'
import { readFile, readdir } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const WEB = join(dirname(fileURLToPath(import.meta.url)), '..')
const ORIGEN = process.argv[2] === 'dist' ? join(WEB, 'dist') : join(WEB, 'public')
const MOTOR = join(ORIGEN, 'motor')
const PAN = '4111111111111111'
const pasos = []
const paso = (m) => { pasos.push(m); console.log('·', m) }

const manifiesto = JSON.parse(await readFile(join(MOTOR, 'till.json'), 'utf8'))
const carpeta = (await readdir(MOTOR)).find((n) => n === `pyodide-${manifiesto.pyodide}`)
assert.ok(carpeta, `falta ${MOTOR}/pyodide-${manifiesto.pyodide}: corre antes «npm run motor» o «npm run build»`)
const BASE = join(MOTOR, carpeta) + '/'
const PUENTE = await readFile(join(WEB, 'src', 'motor', 'puente.py'), 'utf8')

const t0 = performance.now()
const { loadPyodide } = await import(pathToFileURL(join(BASE, 'pyodide.mjs')).href)
const py = await loadPyodide({ indexURL: BASE, stdout: () => {}, stderr: (l) => console.error('[till]', l) })
await py.loadPackage(manifiesto.paquetes, { messageCallback: () => {}, errorCallback: (m) => console.error(m) })
for (const [ruta, texto] of Object.entries(manifiesto.archivos)) {
  const destino = '/app/' + ruta
  py.FS.mkdirTree(destino.slice(0, destino.lastIndexOf('/')))
  py.FS.writeFile(destino, texto)
}
await py.runPythonAsync(PUENTE)
await py.runPythonAsync('await arrancar()')
const llamar = py.globals.get('llamar')
const version = py.runPython('import sys; sys.version.split()[0]')
const zona = py.runPython('from till.host import iso; str(iso.ZONA_LOCAL)')
paso(`Pyodide ${manifiesto.pyodide} con Python ${version}: motor listo en ${Math.round(performance.now() - t0)} ms` +
  ` (${manifiesto.paquetes.length} paquetes, ${Object.keys(manifiesto.archivos).length} módulos de till)`)
paso(`zona horaria de los campos 12/13 de ISO 8583 dentro del motor: ${zona}`)
// Sin tzdata en el motor, ZoneInfo falla y iso.py cae a UTC en silencio: la hora local de la 0200 saldría corrida.
assert.equal(zona, 'America/Mexico_City', 'el motor no tiene la base de zonas horarias (paquete tzdata)')

const SESION = { 'X-Till-Sesion': 'humo-del-motor' }
async function api(metodo, ruta, cuerpo, encabezados = {}) {
  const enc = { ...SESION, ...(cuerpo ? { 'Content-Type': 'application/json' } : {}), ...encabezados }
  const [estado, texto] = JSON.parse(await llamar(metodo, '/api' + ruta, JSON.stringify(enc), cuerpo ? JSON.stringify(cuerpo) : ''))
  return { estado, datos: texto ? JSON.parse(texto) : null, texto }
}

let codigo = 0
try {
  const salud = await api('GET', '/salud')
  assert.equal(salud.estado, 200, salud.texto)
  assert.equal(salud.datos.estado, 'ok')
  assert.equal(salud.datos.libro_balanceado, true)
  paso(`GET /api/salud → 200 · ${salud.datos.cajeros} cajero(s), libro balanceado`)

  const catalogo = await api('GET', '/catalogo')
  assert.equal(catalogo.estado, 200, catalogo.texto)
  assert.equal(catalogo.datos.tarjetas_de_prueba.length, 4)
  paso('GET /api/catalogo → 200 · 4 tarjetas de prueba')

  const r = await api('POST', '/cajeros/TILL0001/operaciones', { tipo: 'retiro', pan: PAN, monto_pesos: 2500 },
    { 'Idempotency-Key': 'humo-1' })
  assert.equal(r.estado, 200, r.texto)
  assert.equal(r.datos.estado, 'CONFIRMADA')
  assert.equal(r.datos.entregado, 250_000)
  assert.equal(r.datos.enmascarado, '411111******1111')
  paso(`POST retiro de $2,500 → ${r.datos.estado}, entregado ${r.datos.entregado_texto}`)

  const repetida = await api('POST', '/cajeros/TILL0001/operaciones', { tipo: 'retiro', pan: PAN, monto_pesos: 2500 },
    { 'Idempotency-Key': 'humo-1' })
  assert.equal(repetida.datos.tx_id, r.datos.tx_id)
  assert.equal(repetida.datos.repetida, true)
  paso('la misma llave de idempotencia devuelve la misma operación (repetida)')

  const d = await api('GET', `/operaciones/${r.datos.tx_id}`)
  assert.equal(d.estado, 200, d.texto)
  assert.deepEqual(d.datos.mensajes.map((m) => m.mti), ['0200'])
  assert.equal(d.datos.mensajes[0].respuesta['39'], '00')
  assert.match(d.datos.comprobante, /MONTO \$2,500\.00/)
  assert.ok(!d.texto.includes(PAN), 'el PAN en claro salió del motor')
  paso('detalle: 0200 → 0210 aprobada, comprobante impreso y sin PAN en claro')

  const cuadre = await api('GET', '/cajeros/TILL0001/cuadre')
  assert.equal(cuadre.estado, 200, cuadre.texto)
  assert.equal(cuadre.datos.estado, 'CUADRA', JSON.stringify(cuadre.datos.observaciones))
  paso('cuadre a tres bandas → CUADRA')

  const invalida = await api('POST', '/cajeros/TILL0001/operaciones', { tipo: 'retiro', pan: '4242424242424241', monto_pesos: 100 },
    { 'Idempotency-Key': 'humo-2' })
  assert.equal(invalida.estado, 422, invalida.texto)
  const inexistente = await api('GET', '/cajeros/NOEXISTE')
  assert.equal(inexistente.estado, 404, inexistente.texto)
  paso('PAN fuera de la lista de prueba → 422; cajero inexistente → 404')

  await py.runPythonAsync('await _ciclo.__aexit__(None, None, None)')
} catch (e) {
  console.error('✗', e.stack || e)
  codigo = 1
}
console.log(`\nmotor (${ORIGEN.endsWith('dist') ? 'web/dist' : 'web/public'}): ${pasos.length} pasos; ${codigo ? 'FALLÓ' : 'sin problemas'}`)
process.exit(codigo)
