// Cliente de la API de Till. Todo va a rutas relativas /api/... del mismo origen.
// Cada pestaña tiene su propia red simulada en el servidor (encabezado X-Till-Sesion),
// así la falla que arma una persona no la dispara otra.

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

async function pedir(metodo, ruta, cuerpo, encabezados = {}) {
  let r
  try {
    r = await fetch(BASE + ruta, {
      method: metodo,
      headers: {
        'X-Till-Sesion': sesionActual(),
        ...(cuerpo !== undefined ? { 'Content-Type': 'application/json' } : {}),
        ...encabezados,
      },
      body: cuerpo !== undefined ? JSON.stringify(cuerpo) : undefined,
    })
  } catch (e) {
    throw new ErrorApi(0, 'sin conexión con la API')
  }
  const texto = await r.text()
  let datos = null
  try {
    datos = texto ? JSON.parse(texto) : null
  } catch {
    datos = texto
  }
  if (!r.ok) throw new ErrorApi(r.status, datos && typeof datos === 'object' && 'detail' in datos ? datos.detail : datos)
  return datos
}

export const api = {
  get: (ruta) => pedir('GET', ruta),
  post: (ruta, cuerpo, encabezados) => pedir('POST', ruta, cuerpo, encabezados),
  del: (ruta) => pedir('DELETE', ruta),
}
