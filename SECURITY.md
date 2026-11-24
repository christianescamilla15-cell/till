# Seguridad

## Reportar una vulnerabilidad

Escribe a **christianescamilla15@gmail.com** con el asunto «Seguridad: Till». Respondo en 72 horas hábiles y
publico el arreglo con su aviso. Por favor no abras un issue público para vulnerabilidades.

Alcance: el código de este repositorio y la demo publicada en [till-cajero.vercel.app](https://till-cajero.vercel.app).
Till es una implementación de referencia **simulada**: no procesa tarjetas reales, sólo acepta PANs de prueba
públicos y no tiene servidor de API en la nube (la API corre en el navegador de cada visitante).

## Qué revisa cada cambio

| Revisión | Herramienta | Bloquea |
|---|---|---|
| Análisis estático | CodeQL (Python y JavaScript/TypeScript) + Semgrep `p/default` | sí (Semgrep) |
| Dependencias, configuración e IaC | Trivy | sí, en CRITICAL/HIGH con arreglo publicado |
| Secretos en toda la historia | gitleaks | sí |
| Datos de terceros | revisión de confidencialidad | sí |
| Inventario de componentes | SBOM (syft, SPDX) | no: se publica con cada versión |
| Dependencias desactualizadas | Dependabot (pip, npm de `web/` y `e2e/`, acciones), agrupado y con espera de 7 días | — |

Cada revisión debe probar que analizó algo (archivos o paquetes > 0); un reporte vacío cuenta como fallo.
Las excepciones viven en `.trivyignore`, cada una con fecha de vencimiento y motivo; las supresiones de Semgrep
van en el código con su motivo (hoy hay una: el UPDATE de la bitácora, cuyas columnas salen de una lista fija;
la cubre `tests/test_maquinas_de_estado.py::test_la_bitacora_solo_actualiza_columnas_editables`).

## Controles del dominio

| Control | Dónde | Prueba |
|---|---|---|
| El PAN nunca se persiste: token con llave (HMAC-SHA256) y enmascarado 6+4 | `till/seguridad.py`, `till/devices/tarjeta.py` | reglas P1–P5 de [`docs/reglas.md`](docs/reglas.md) |
| La traza ISO 8583 descarta el campo 55 y enmascara el campo 2 | `till/host/traza.py` | `tests/test_traza.py` |
| La impresora se niega a imprimir un PAN completo | `till/devices/impresora.py` | `tests/test_dispositivos.py` |
| Sólo se aceptan PANs de prueba públicos que pasan Luhn | `till/seguridad.py` | `tests/test_maquinas_de_estado.py` |
| El SQL nunca lleva datos de entrada: valores como parámetros y columnas de una lista fija | `till/core/bitacora.py`, `till/db.py` | `tests/test_maquinas_de_estado.py` |
| CSP estricta (`default-src 'self'`; sólo `'wasm-unsafe-eval'` para compilar WebAssembly), sin estilos ni scripts en línea, `frame-ancestors 'none'` | `vercel.json`, `api/index.py` | `tests/test_vercel.py`, E2E (falla con cualquier violación de CSP) |
| El motor de la demo sólo descarga paquetes verificados contra el sha256 del lockfile de Pyodide | `web/scripts/preparar_motor.mjs` | build del CI |

## Lo que NO cubre la Etapa 1 (declarado)

La llave de tokenización y la del emisor son de desarrollo (`TILL_LLAVE_TOKEN` permite cambiar la primera);
no hay PIN, EPP, DUKPT, carga remota de llaves (TR-31/TR-34), HSM, MAC en ISO 8583 ni cifrado del enlace con
el banco. El criptograma EMV es un HMAC, no el de EMV Book 2. Ver «Alcance honesto» en el README y
[`docs/hardware.md`](docs/hardware.md).
