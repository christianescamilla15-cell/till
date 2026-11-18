// El motor de la demo: la API de Till —la misma app FastAPI que pasa las pruebas— corriendo en este navegador
// sobre Pyodide (CPython compilado a WebAssembly), en un worker para no trabar la página.
//
// Por qué aquí y no en una función del servidor: la red simulada vive en memoria, y una plataforma sin servidor
// reparte las solicitudes de una misma pestaña entre varias instancias (medido en Vercel: 24 solicitudes
// simultáneas cayeron en 9 instancias). Corriendo en el navegador, cada pestaña tiene su red completa y nada
// se pierde entre solicitudes.
import PUENTE from './puente.py?raw'

/* global __PYODIDE__ */
const BASE = `/motor/pyodide-${__PYODIDE__}/`
let arranque = null

const avisar = (etapa, detalle) => self.postMessage({ tipo: 'estado', etapa, detalle })

async function arrancar() {
  const t0 = performance.now()
  avisar('descargando')
  const [{ loadPyodide }, manifiesto] = await Promise.all([
    import(/* @vite-ignore */ BASE + 'pyodide.mjs'),
    fetch('/motor/till.json', { cache: 'no-cache' }).then((r) => {
      if (!r.ok) throw new Error(`till.json: HTTP ${r.status}`)
      return r.json()
    }),
  ])
  const py = await loadPyodide({
    indexURL: BASE,
    stdout: () => {},
    stderr: (linea) => console.info('[till]', linea),
  })
  await py.loadPackage(manifiesto.paquetes, { messageCallback: () => {}, errorCallback: (m) => console.warn(m) })
  avisar('arrancando')
  for (const [ruta, texto] of Object.entries(manifiesto.archivos)) {
    const destino = '/app/' + ruta
    py.FS.mkdirTree(destino.slice(0, destino.lastIndexOf('/')))
    py.FS.writeFile(destino, texto)
  }
  await py.runPythonAsync(PUENTE)
  await py.runPythonAsync('await arrancar()')
  const llamar = py.globals.get('llamar')
  avisar('listo', { pyodide: manifiesto.pyodide, python: py.runPython('import sys; sys.version.split()[0]'), ms: Math.round(performance.now() - t0) })
  return llamar
}

function motor() {
  if (!arranque) {
    arranque = arrancar().catch((e) => {
      arranque = null // el siguiente «Reintentar» vuelve a intentarlo
      avisar('error', String(e?.message || e))
      throw e
    })
  }
  return arranque
}

self.onmessage = async ({ data }) => {
  const { id, metodo, ruta, encabezados, cuerpo } = data
  try {
    const llamar = await motor()
    const [estado, texto] = JSON.parse(await llamar(metodo, ruta, JSON.stringify(encabezados || {}), cuerpo ?? ''))
    self.postMessage({ tipo: 'respuesta', id, estado, texto })
  } catch (e) {
    self.postMessage({ tipo: 'respuesta', id, falla: String(e?.message || e) })
  }
}

motor().catch(() => {}) // arranca en cuanto la página crea el worker
