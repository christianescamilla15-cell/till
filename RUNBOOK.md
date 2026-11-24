# Runbook · Till

La demo pública ([till-cajero.vercel.app](https://till-cajero.vercel.app)) es un **sitio estático en Vercel**: no
hay servidor de API ni base de datos en la nube. La API corre en el navegador de cada visitante sobre Pyodide
(ver [ADR 0002](docs/adr/0002-api-en-el-navegador-con-pyodide.md)). `vercel.json` define el build y los
encabezados; `.vercelignore` deja fuera `/api/` y los archivos de Python **anclados a la raíz** (sin el ancla
también se llevaba `till/api/` y el motor no arrancaba). No hacen falta variables de entorno.

## Antes de desplegar

1. El cambio entró a `main` por PR con todos los checks de `ci` y `seguridad` en verde (incluidos `motor de la
   demo` y `front (build y E2E con Playwright)`, que prueban el runtime que se publica).
2. En local, desde la raíz del repo:

   ```bash
   npm ci --prefix web && npm run build --prefix web     # prepara el motor y construye web/dist
   npm run humo:motor --prefix web                       # la API real en Pyodide, desde web/dist
   ```

## Desplegar

El despliegue se hace hoy **a mano desde el clon de despliegue** (`~/till`, enlazado al proyecto de Vercel con
`.vercel/`, que no se versiona). No hay despliegue automático desde GitHub.

```bash
cd ~/till
git fetch origin && git checkout main && git pull --ff-only
npx vercel deploy --prod      # Vercel corre `npm ci --prefix web` y `npm run build --prefix web` y publica web/dist
```

Para una vista previa sin tocar producción: `npx vercel deploy` (sin `--prod`); la CLI imprime la URL de la
vista previa y sobre ella se corre la prueba de humo antes de promover.

## Después de desplegar (obligatorio)

```bash
herramientas/humo.sh https://till-cajero.vercel.app
```

Comprueba que la página salga con la CSP exacta de `vercel.json`, que el motor esté completo (`till.json` con
los módulos esenciales, el intérprete, el lockfile recortado y `tzdata`) y que **no** se haya publicado una
función de servidor en `/api`. Si falla, revertir (abajo).

Luego, en un navegador: abrir la demo, esperar «motor listo» y hacer un retiro de $2,500 (es el mismo recorrido
que el E2E del CI).

## Revertir

Vercel conserva los despliegues anteriores: revertir es volver a apuntar producción al último bueno, sin
reconstruir.

```bash
cd ~/till
npx vercel ls                                   # localizar el último despliegue bueno de producción
URL_BUENA=https://till-cajero-xxxxxxxx.vercel.app  # la URL de ese despliegue, tal como la lista `vercel ls`
npx vercel rollback "$URL_BUENA"                # producción vuelve a ese despliegue
herramientas/humo.sh https://till-cajero.vercel.app
```

También desde el panel de Vercel: *Deployments* → el despliegue bueno → *Instant Rollback*.

**Ensayo:** la reversa **no** se ha ensayado desde esta rama (el programa de calidad no despliega). Queda como
pendiente del dueño ensayarla una vez con una vista previa y anotar aquí la fecha.

> Nota de esta versión: la producción actual (desplegada el 18-nov) todavía no tiene `tzdata` en el motor, así
> que `herramientas/humo.sh` contra producción falla en ese punto hasta desplegar esta rama. Es el hallazgo que
> corrige el commit «fix(motor): cargar tzdata en Pyodide».

## Diagnóstico

| Síntoma | Causa probable | Qué hacer |
|---|---|---|
| La demo se queda en «descargando» o «arrancando» | El motor no se publicó completo (falta `motor/till.json` o un archivo del intérprete) | `herramientas/humo.sh` contra la URL; revisar que el build haya corrido `prebuild` (prepara el motor) |
| «el motor no arrancó: falta till/api/app.py» en el build | `.vercelignore` con reglas sin ancla (`api/` en vez de `/api/`) | Anclar las reglas; `tests/test_vercel.py` lo exige |
| Errores de CSP en la consola del navegador | Encabezados de `vercel.json` y `api/index.py` distintos | `tests/test_vercel.py::test_vercel_json_coincide_con_la_entrada` |
| La hora de los mensajes ISO 8583 sale seis horas adelantada | El motor no trae `tzdata` y `iso.py` cayó a UTC | `npm run humo:motor --prefix web` lo detecta; `tzdata` debe estar en `PAQUETES` de `web/scripts/preparar_motor.mjs` |
| El job `motor` del CI falla en «el Python de este job es el de Pyodide» | Se actualizó el paquete npm `pyodide` y cambió su Python | Subir `python-version` del job al Python que reporta `herramientas/requisitos_motor.py --python` |
| El job `motor` falla en pruebas que pasan en «pruebas (Python 3.12)» | Diferencia real entre la FastAPI/Pydantic de Pyodide y la de `requirements.txt` | Es exactamente lo que el job existe para atrapar: corregir el código para ambos runtimes |
| Un cajero queda «fuera de servicio» en la demo | Corte de energía simulado (falla programada) | Botón «Reiniciar el cajero» (`POST /cajeros/{t}/reiniciar`): la recuperación cierra lo pendiente |

## Entorno local

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest                                    # pruebas
npm ci --prefix web && npm run build --prefix web
.venv/bin/uvicorn api.index:app --port 8000                   # la demo con la CSP de producción
HUMO_LOCAL=1 herramientas/humo.sh http://127.0.0.1:8000        # humo contra el build local
TILL_PYTHON=$PWD/.venv/bin/python npm test --prefix e2e        # E2E (Node 20+, `npx playwright install chromium`)
```
