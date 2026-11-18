// Prepara el «motor» de la demo publicada: la API de Till corre en el navegador sobre Pyodide (CPython
// compilado a WebAssembly), así que aquí se deja en web/public/motor/ todo lo que necesita, servido desde el
// mismo origen (la CSP no permite CDNs):
//
//   motor/pyodide-<versión>/  el intérprete (del paquete npm «pyodide») y sólo los paquetes que usa la API,
//                             descargados del CDN de Pyodide y verificados contra el sha256 de su lockfile;
//                             un lockfile recortado a esos paquetes (FastAPI sin httpx ni jinja2, que sólo
//                             usan el cliente de pruebas y las plantillas).
//   motor/till.json           el código Python de Till (paquete till/) y la versión de Pyodide.
//
// Corre solo antes de `npm run build` y de `npm run dev`. Lo descargado se reutiliza si el hash coincide.
import { createHash } from 'node:crypto'
import { copyFile, mkdir, readFile, readdir, rm, writeFile } from 'node:fs/promises'
import { dirname, join, relative, sep } from 'node:path'
import { fileURLToPath } from 'node:url'

const WEB = join(dirname(fileURLToPath(import.meta.url)), '..')
const RAIZ = join(WEB, '..')
const ORIGEN = join(WEB, 'node_modules', 'pyodide')
const VERSION = JSON.parse(await readFile(join(ORIGEN, 'package.json'), 'utf8')).version
const MOTOR = join(WEB, 'public', 'motor')
const SALIDA = join(MOTOR, `pyodide-${VERSION}`)
const CDN = `https://cdn.jsdelivr.net/pyodide/v${VERSION}/full/`

// Lo que importa la API de Till (fastapi y su cadena) y pyiso8583, que no viene en la distribución de Pyodide.
const PAQUETES = [
  'fastapi', 'starlette', 'anyio', 'sniffio', 'idna', 'pydantic', 'pydantic-core', 'annotated-types',
  'annotated-doc', 'typing-extensions', 'typing-inspection',
]
const FUERA = { // paquetes puros de PyPI, fijados por versión y hash
  pyiso8583: {
    version: '4.0.1',
    file_name: 'pyiso8583-4.0.1-py3-none-any.whl',
    url: 'https://files.pythonhosted.org/packages/54/dc/e1b0dbf4a57da8a375ea5974878735ed13e0cb38978fa699b61a0f5185d2/pyiso8583-4.0.1-py3-none-any.whl',
    sha256: 'a30ba63d2ccb61f0d9464add6ed2442b72687b8193743cfdfeace8ebaf6f6ce7',
    imports: ['iso8583'],
  },
}
const NUCLEO = ['pyodide.mjs', 'pyodide.asm.mjs', 'pyodide.asm.wasm', 'python_stdlib.zip']

const normal = (n) => n.toLowerCase().replace(/[-_.]+/g, '-')
const sha256 = (b) => createHash('sha256').update(b).digest('hex')

async function existeCon(ruta, hash) {
  try {
    return sha256(await readFile(ruta)) === hash
  } catch {
    return false
  }
}

async function bajar(url, ruta, hash) {
  if (await existeCon(ruta, hash)) return false
  const r = await fetch(url)
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`)
  const b = Buffer.from(await r.arrayBuffer())
  if (sha256(b) !== hash) throw new Error(`${url}: el sha256 no coincide con el lockfile`)
  await writeFile(ruta, b)
  return true
}

async function archivosPython(dir) {
  const fuera = {}
  for (const e of await readdir(dir, { withFileTypes: true, recursive: true })) {
    if (!e.isFile() || !e.name.endsWith('.py')) continue
    const ruta = join(e.parentPath ?? e.path, e.name)
    if (ruta.includes(`${sep}__pycache__${sep}`)) continue
    fuera[relative(RAIZ, ruta).split(sep).join('/')] = await readFile(ruta, 'utf8')
  }
  return fuera
}

// Quita versiones viejas del intérprete (cambiar de Pyodide deja la carpeta anterior).
await mkdir(SALIDA, { recursive: true })
for (const e of await readdir(MOTOR, { withFileTypes: true })) {
  if (e.isDirectory() && e.name.startsWith('pyodide-') && e.name !== `pyodide-${VERSION}`) {
    await rm(join(MOTOR, e.name), { recursive: true, force: true })
  }
}

for (const f of NUCLEO) await copyFile(join(ORIGEN, f), join(SALIDA, f))

const lock = JSON.parse(await readFile(join(ORIGEN, 'pyodide-lock.json'), 'utf8'))
const porNombre = new Map(Object.keys(lock.packages).map((k) => [normal(k), k]))
const elegidos = new Set(PAQUETES.map(normal))
const recortado = { info: lock.info, packages: {} }
let bajados = 0
for (const n of PAQUETES) {
  const clave = porNombre.get(normal(n))
  if (!clave) throw new Error(`Pyodide ${VERSION} no trae el paquete ${n}`)
  const p = { ...lock.packages[clave] }
  p.depends = p.depends.filter((d) => elegidos.has(normal(d)))
  recortado.packages[clave] = p
  if (await bajar(CDN + p.file_name, join(SALIDA, p.file_name), p.sha256)) bajados++
}
for (const [nombre, p] of Object.entries(FUERA)) {
  recortado.packages[nombre] = {
    name: nombre, version: p.version, file_name: p.file_name, install_dir: 'site', sha256: p.sha256,
    package_type: 'package', imports: p.imports, depends: [], unvendored_tests: false,
  }
  if (await bajar(p.url, join(SALIDA, p.file_name), p.sha256)) bajados++
}
await writeFile(join(SALIDA, 'pyodide-lock.json'), JSON.stringify(recortado))

const till = {
  pyodide: VERSION,
  paquetes: [...PAQUETES, ...Object.keys(FUERA)],
  archivos: await archivosPython(join(RAIZ, 'till')),
}
// Si falta algo esencial (p. ej. una regla de .vercelignore que se lleve till/api/), mejor fallar el build
// que publicar un motor que no arranca.
for (const m of ['till/__init__.py', 'till/api/app.py', 'till/api/sesiones.py', 'till/red.py']) {
  if (!(m in till.archivos)) throw new Error(`motor: falta ${m} en el código de Till`)
}
await writeFile(join(MOTOR, 'till.json'), JSON.stringify(till))

console.log(
  `motor: Pyodide ${VERSION} (Python ${lock.info.python}) · ${till.paquetes.length} paquetes` +
    ` (${bajados} descargados) · ${Object.keys(till.archivos).length} módulos de till`,
)
