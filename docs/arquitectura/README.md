# Arquitectura · Till

Modelo C4 (contexto → contenedores → componentes → despliegue) en Mermaid, hecho con los módulos reales del
repositorio. Los diagramas son texto y se versionan con el código.

## Paquete de arquitectura empresarial

| Documento | Qué contiene |
|---|---|
| [`vision.md`](vision.md) | Visión de arquitectura al estilo TOGAF ADM ligero: drivers, metas, principios, partes interesadas, línea base contra objetivo, brechas y hoja de ruta. |
| [`requisitos.md`](requisitos.md) | Requisitos funcionales y no funcionales con trazabilidad requisito → componente → regla → prueba. |
| [`archimate.md`](archimate.md) y [`till.archimate.xml`](till.archimate.xml) | Modelo ArchiMate 3.1 (motivación, negocio, aplicación, tecnología, migración) en el formato de intercambio de The Open Group, validado contra su XSD, y su vista legible. |
| [`bian.md`](bian.md) | Componentes de Till contra dominios de servicio BIAN (release 14.0), con lo que no tiene equivalente. |
| [`uml.md`](uml.md) | Estados de la operación y de la reversa; secuencias de retiro, reversa tras apagón, cuadre y solicitud de la demo. |
| [`../adr/`](../adr/) | Decisiones con sus alternativas descartadas. |
| [`../reglas.md`](../reglas.md) | Reglas de negocio → pruebas (la compuerta de calidad). |

## 1. Contexto

```mermaid
C4Context
  title Contexto · Till (Etapa 1: dispositivos y banco simulados)
  Person(cliente, "Cliente de sucursal", "Retira, deposita, paga su crédito o envía una remesa con una tarjeta de prueba")
  Person(operador, "Operador de la red", "Cuadra cada cajero, programa fallas, reinicia y simula la flota")
  System(till, "Till", "Red de cajeros de sucursal: dispositivo, transacción, libro mayor, cuadre y flota")
  System_Ext(banco, "Banco emisor y autorizador", "En la Etapa 1 es el autorizador simulado de till/host")
  Rel(cliente, till, "Opera en el cajero")
  Rel(operador, till, "Opera y audita", "HTTP/JSON")
  Rel(till, banco, "Autoriza, revierte y acusa", "ISO 8583 · 0200/0210 · 0420/0421/0430 · 0800/0810")
```

## 2. Contenedores

```mermaid
C4Container
  title Contenedores · Till
  Person(visitante, "Visitante de la demo", "Cliente u operador en un navegador")
  System_Boundary(b, "Till") {
    Container(web, "Demo web", "React 18 · Vite", "Cajero, taller de fallas, libros y flota (web/)")
    Container(motor, "Motor en el navegador", "Web worker · Pyodide (CPython 3.14)", "Entrega cada /api/... a la app por ASGI (web/src/motor/)")
    Container(api, "API de Till", "Python · FastAPI", "La misma app en local, en las pruebas y en el navegador (till/api/)")
    Container(nucleo, "Núcleo del cajero", "Python", "Máquina de estados, dispositivos, reversas, libro mayor y cuadre (till/)")
    Container(host, "Banco simulado", "Python · ISO 8583", "Autorizador idempotente con cuentas, límites, créditos y remesas (till/host/)")
    ContainerDb(bd, "Bitácora y libro mayor", "SQLite · WAL · synchronous=FULL", "Una base por red; en el navegador, en el FS en memoria del worker")
  }
  Rel(visitante, web, "Usa", "HTTPS")
  Rel(web, motor, "Solicitudes /api", "postMessage")
  Rel(motor, api, "Llama", "ASGI")
  Rel(api, nucleo, "Opera cajeros, cuadra y simula")
  Rel(nucleo, bd, "Escribe estado y asiento en una sola transacción", "SQL")
  Rel(nucleo, host, "Autoriza y revierte", "ISO 8583 en bytes")
```

## 3. Componentes del núcleo y de la API

```mermaid
C4Component
  title Componentes · núcleo del cajero y API
  Container_Boundary(api_b, "API (till/api)") {
    Component(app, "app.py", "FastAPI", "Rutas, validación de entrada, errores 404/422/503, ciclo de fondo de reversas")
    Component(sesiones, "sesiones.py", "Python", "Una red por visitante (X-Till-Sesion), tope y purga")
  }
  Container_Boundary(nucleo_b, "Núcleo (till/)") {
    Component(red, "red.py", "Python", "Arma banco, enlace, bitácora, libro y cajeros")
    Component(maquina, "core/maquina.py", "Python", "AUTORIZAR → ENTREGAR/RECIBIR → CONFIRMAR, recuperación tras corte")
    Component(bitacora, "core/bitacora.py", "SQLite", "Estados y transiciones, historial sólo-anexar, STAN, cola de reversas")
    Component(reversas, "core/reversas.py", "asyncio", "0420/0421 con espera exponencial hasta el 0430")
    Component(dispositivos, "devices/", "Python", "Reciclador, lector EMV, impresora y cámara con inyección de fallas")
    Component(seguridad, "seguridad.py", "HMAC", "Token con llave, enmascarado 6+4, ARQC simulado")
    Component(cliente_iso, "host/cliente.py · enlace.py · traza.py", "pyiso8583", "Mensajes ISO 8583, transporte con fallas o TCP, traza sin PAN")
    Component(libro, "ledger/", "SQLite", "Partida doble sólo-anexar con llave por asiento")
    Component(cuadre, "recon/cuadre.py", "Python", "Físico contra libro contra banco")
    Component(flota, "fleet/", "asyncio", "Modelo agregado de 15 000 cajeros y muestra con la máquina real")
  }
  Component_Ext(autorizador, "host/autorizador.py", "Banco simulado", "Idempotente con 0200 y reversas repetidas")
  Rel(app, sesiones, "Obtiene la red de la sesión")
  Rel(sesiones, red, "Construye")
  Rel(app, maquina, "operar · recuperar")
  Rel(app, cuadre, "cuadrar")
  Rel(app, flota, "simular · muestra")
  Rel(maquina, bitacora, "Transiciones y escritura anticipada")
  Rel(maquina, libro, "Asientos")
  Rel(maquina, dispositivos, "Comandos y terminaciones")
  Rel(maquina, cliente_iso, "0200")
  Rel(maquina, reversas, "Encola e intenta")
  Rel(reversas, cliente_iso, "0420/0421")
  Rel(dispositivos, seguridad, "Tokeniza y genera ARQC")
  Rel(cliente_iso, autorizador, "Bytes ISO 8583")
  Rel(cuadre, libro, "Saldos")
  Rel(cuadre, autorizador, "Totales por tipo")
  Rel(flota, maquina, "Muestra de alta fidelidad")
```

Las capas son una regla, no una sugerencia: `tests/test_arquitectura.py` falla si un módulo importa una capa
superior (dinero/seguridad/db → dispositivos y libro → host → core → recon → red → flota → API) o si el
dominio importa FastAPI, Starlette, Pydantic, uvicorn o httpx.

## 4. Despliegue

```mermaid
C4Deployment
  title Despliegue · demo publicada (sitio estático) y entorno local
  Deployment_Node(vercel, "Vercel", "CDN, sitio estático") {
    Container(sitio, "web/dist", "HTML, JS, CSS, motor/", "CSP estricta de vercel.json; sin funciones")
  }
  Deployment_Node(navegador, "Navegador del visitante", "Chrome, Firefox, Safari") {
    Deployment_Node(worker, "Web worker", "Pyodide 314 · CPython 3.14 · WebAssembly") {
      Container(api_nav, "API de Till", "FastAPI", "La red simulada de la pestaña, en memoria")
      ContainerDb(bd_nav, "SQLite", "FS en memoria", "Bitácora y libro")
    }
  }
  Deployment_Node(local, "Equipo de desarrollo o CI", "Linux · CPython 3.12") {
    Container(uvicorn, "api.index:app", "uvicorn", "La API bajo /api y web/dist con la misma CSP (pruebas, E2E)")
  }
  Rel(navegador, vercel, "Descarga el sitio y el motor", "HTTPS")
```

El despliegue real, la reversa y la prueba de humo posterior están en [`RUNBOOK.md`](../../RUNBOOK.md); el
porqué de correr la API en el navegador, en el [ADR 0002](../adr/0002-api-en-el-navegador-con-pyodide.md).
