// Cliente de la API de Till: rutas /api/... que atiende el motor en el navegador (o uvicorn, ver MOTOR).
// Cada pestaña tiene su propia red simulada (encabezado X-Till-Sesion): con el motor en el navegador ya
// está aislada por construcción, y con uvicorn la falla que arma una persona no la dispara otra.

const BASE = '/api'
const LLAVE_SESION = 'till-sesion'

export class ErrorApi extends Error {
  constructor(status, detalle) {
    super(typeof detalle === 'string' ? detalle : `HTTP ${status}`)
    this.status = status
    this.detalle = detalle
  }
}

export function idAleatorio(bytes = 12) {
  const b = new Uint8Array(bytes)
  crypto.getRandomValues(b)
  return Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('')
}

let sesion = null

export function sesionActual() {
  if (sesion) return sesion
  try {
    sesion = sessionStorage.getItem(LLAVE_SESION)
  } catch {
    sesion = null
  }
  if (!sesion || !/^[A-Za-z0-9_-]{8,64}$/.test(sesion)) return nuevaSesion()
  return sesion
}

export function nuevaSesion() {
  sesion = 'web-' + idAleatorio()
  try {
    sessionStorage.setItem(LLAVE_SESION, sesion)
  } catch {
    // sin almacenamiento (ventana privada estricta): la sesión vive mientras viva la página
  }
  return sesion
}

// -- transporte --------------------------------------------------------------------------------------------
// Por omisión la API corre en este navegador (Pyodide en un worker: src/motor/). Con VITE_TILL_MOTOR=servidor
// va por HTTP a uvicorn, útil para depurar la API con sus propios registros.
export const MOTOR = import.meta.env.VITE_TILL_MOTOR === 'servidor' ? 'servidor' : 'navegador'

let trabajador = null
let folio = 0
const enEspera = new Map()
const oyentes = new Set()
let estadoActual = { etapa: MOTOR === 'servidor' ? 'listo' : 'inactivo' }

function publicar(estado) {
  estadoActual = estado
  oyentes.forEach((fn) => fn(estado))
}

/** Avisa cada cambio del motor: inactivo → descargando → arrancando → listo (o error). */
export function suscribirMotor(fn) {
  oyentes.add(fn)
  fn(estadoActual)
  return () => oyentes.delete(fn)
}

function motor() {
  if (trabajador) return trabajador
  trabajador = new Worker(new URL('./motor/motor.worker.js', import.meta.url), { type: 'module', name: 'till-motor' })
  trabajador.onmessage = ({ data }) => {
    if (data.tipo === 'estado') {
      publicar({ etapa: data.etapa, detalle: data.detalle })
      return
    }
    const p = enEspera.get(data.id)
    if (!p) return
    enEspera.delete(data.id)
    if (data.falla !== undefined) p.reject(new ErrorApi(0, data.falla))
    else p.resolve(data)
  }
  trabajador.onerror = (e) => {
    publicar({ etapa: 'error', detalle: e.message || 'el worker no cargó' })
    enEspera.forEach((p) => p.reject(new ErrorApi(0, 'el motor de la demo se detuvo')))
    enEspera.clear()
    trabajador = null
  }
  return trabajador
}

/** Arranca el motor sin esperar a la primera solicitud. */
export function iniciarMotor() {
  if (MOTOR === 'navegador') motor()
}

function enNavegador(metodo, ruta, encabezados, cuerpo) {
  return new Promise((resolve, reject) => {
    const id = ++folio
    enEspera.set(id, { resolve, reject })
    motor().postMessage({ id, metodo, ruta, encabezados, cuerpo })
  })
}

async function porHttp(metodo, ruta, encabezados, cuerpo) {
  let r
  try {
    r = await fetch(ruta, { method: metodo, headers: encabezados, body: cuerpo })
  } catch (e) {
    throw new ErrorApi(0, 'sin conexión con la API')
  }
  return { estado: r.status, texto: await r.text() }
}

async function pedir(metodo, ruta, cuerpo, encabezados = {}) {
  const enc = {
    'X-Till-Sesion': sesionActual(),
    ...(cuerpo !== undefined ? { 'Content-Type': 'application/json' } : {}),
    ...encabezados,
  }
  const datosCuerpo = cuerpo !== undefined ? JSON.stringify(cuerpo) : undefined
  const { estado, texto } = await (MOTOR === 'navegador' ? enNavegador : porHttp)(metodo, BASE + ruta, enc, datosCuerpo)
  let datos = null
  try {
    datos = texto ? JSON.parse(texto) : null
  } catch {
    datos = texto
  }
  if (estado < 200 || estado >= 300) {
    throw new ErrorApi(estado, datos && typeof datos === 'object' && 'detail' in datos ? datos.detail : datos)
  }
  return datos
}

export const api = {
  get: (ruta) => pedir('GET', ruta),
  post: (ruta, cuerpo, encabezados) => pedir('POST', ruta, cuerpo, encabezados),
  del: (ruta) => pedir('DELETE', ruta),
}
