# ADR 0002 · La demo publicada corre la API en el navegador con Pyodide, no en una función sin servidor

- Estado: aceptada · Fecha: 2026-11-18 (commit `fbb1f4b`)

## Contexto
La demo pública (`till-cajero.vercel.app`) muestra la API real del núcleo: una red simulada completa —banco,
cajero, bitácora, libro mayor y reversas pendientes— que vive **en memoria** mientras dura la visita. La
primera versión publicó esa API como función de Python en Vercel. Medido en producción: 24 solicitudes
simultáneas de una misma pestaña cayeron en **9 instancias distintas**; el comprobante de un retiro llegaba a
una instancia que no conocía la operación y el ciclo de fondo que reenvía reversas se congelaba entre
solicitudes. El estado en memoria no sobrevive a una plataforma que reparte y congela instancias.

## Decisión
La demo es un **sitio estático**. La página carga Pyodide (CPython compilado a WebAssembly) en un *web worker*
y entrega cada solicitud `/api/...` a la **misma app FastAPI** de `till/api` como una llamada ASGI
(`web/src/motor/puente.py`). `web/scripts/preparar_motor.mjs` empaqueta el intérprete, sólo los paquetes que
usa la API (verificados contra el sha256 del lockfile de Pyodide) y el código de `till/`. `.vercelignore`
excluye `/api/` y los archivos de Python **anclados a la raíz** para que Vercel no cree ninguna función (sin
el ancla también se llevaba `till/api/`).

## Alternativas descartadas
| Alternativa | Por qué no |
|---|---|
| **Función sin servidor (Vercel Python)** | Lo medido: el estado se reparte entre instancias y el reenvío de reversas se congela. Hacerla sin estado exigiría una base externa compartida y un proceso de fondo aparte: otra infraestructura, otro costo y otra superficie de ataque para una demo. |
| **Servidor permanente (contenedor o VM)** | Resuelve el estado pero agrega operación 24×7, costo fijo y un punto donde todos los visitantes comparten red (había que aislar sesiones de todas formas). |
| **Reescribir la lógica en JavaScript para el navegador** | Dos implementaciones del núcleo: la demo dejaría de ser «la misma API que pasa las pruebas». |
| **Base externa (Redis/Postgres) para el estado de sesión** | Mueve la complejidad al lugar equivocado: la red simulada es por visitante y efímera por diseño. |

## Consecuencias
- Cada pestaña tiene su red completa; nada se pierde entre solicitudes; no hay estado del lado del servidor ni
  datos de visitantes que proteger.
- Costo: la primera visita baja ~7 MB comprimidos (después quedan en caché) y el motor tarda unos segundos en
  arrancar.
- **El runtime publicado no es el que prueba el CI por omisión**: Pyodide trae Python 3.14 y su propia FastAPI.
  Por eso el CI tiene el job `motor`, que corre toda la suite con esas versiones y la prueba de humo
  `web/scripts/humo_motor.mjs` sobre el motor real en WebAssembly. Esa prueba encontró que el navegador no
  tiene base de zonas horarias: se agregó el paquete `tzdata` al motor.
- La CSP tiene que permitir `'wasm-unsafe-eval'` (compilar WebAssembly; no habilita `eval` de JavaScript).
