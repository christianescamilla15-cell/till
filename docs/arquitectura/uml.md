# UML · secuencias y estados

Diagramas en Mermaid (GitHub los dibuja). Están hechos con el flujo real del código; los nombres de los pasos
son los de `till/core/maquina.py`, `till/core/reversas.py` y `till/recon/cuadre.py`.

## 1. Estados de la operación

El diagrama de abajo dice exactamente lo que permite `TRANSICIONES` en `till/core/bitacora.py`:
`tests/test_maquinas_de_estado.py::test_el_diagrama_de_estados_documentado_es_el_del_codigo` falla si alguien
cambia uno sin el otro, y otra prueba recorre las 144 parejas de estados (lo no dibujado se rechaza).

```mermaid
stateDiagram-v2
    [*] --> INICIADA
    INICIADA --> CONTANDO: ingreso (billetes en escrow)
    INICIADA --> AUTORIZANDO: retiro (0200 por enviarse)
    INICIADA --> CANCELADA: tarjeta, chip o monto no disponible
    CONTANDO --> AUTORIZANDO
    CONTANDO --> CANCELADA: atasco o ningún billete válido
    AUTORIZANDO --> AUTORIZADA: 0210 aprobada
    AUTORIZANDO --> RECHAZADA: 0210 negada (51, 61, 05…)
    AUTORIZANDO --> EN_REVERSA: sin respuesta o caída
    AUTORIZADA --> ENTREGANDO: retiro
    AUTORIZADA --> RECIBIENDO: ingreso
    AUTORIZADA --> EN_REVERSA: caída antes de mover efectivo
    ENTREGANDO --> CONFIRMADA: el cliente tomó el efectivo
    ENTREGANDO --> EN_REVERSA: falla o entrega parcial
    RECIBIENDO --> CONFIRMADA: el efectivo quedó guardado
    RECIBIENDO --> EN_REVERSA: no se pudo guardar
    EN_REVERSA --> REVERTIDA: 0430
    EN_REVERSA --> PARCIAL: 0430 de una reversa parcial
    CONFIRMADA --> [*]
    RECHAZADA --> [*]
    CANCELADA --> [*]
    REVERTIDA --> [*]
    PARCIAL --> [*]
```

La reversa tiene su propia máquina, más pequeña: `PENDIENTE → ACUSADA`, una sola vez (`acusar_reversa`
regresa `False` si ya estaba acusada), y una sola reversa por operación (`tx_id` único).

```mermaid
stateDiagram-v2
    direction LR
    [*] --> PENDIENTE: encolar (atómico con EN_REVERSA)
    PENDIENTE --> PENDIENTE: 0420/0421 sin acuse · espera exponencial con tope
    PENDIENTE --> ACUSADA: 0430
    ACUSADA --> [*]
```

## 2. Retiro sin fallas

```mermaid
sequenceDiagram
    autonumber
    actor C as Cliente
    participant M as Máquina de estados<br/>(core/maquina.py)
    participant B as Bitácora<br/>(SQLite WAL)
    participant L as Lector EMV
    participant R as Reciclador
    participant H as Banco<br/>(ISO 8583)
    participant LM as Libro mayor
    participant I as Impresora

    C->>M: tarjeta + retiro de $1,700 (llave de idempotencia)
    M->>B: ¿ya existe la llave? (huella de la solicitud)
    B-->>M: no
    M->>B: crear operación · INICIADA
    M->>L: CardReader.ReadRawData
    L-->>M: token + PAN enmascarado (el PAN sólo vive en la sesión)
    M->>R: CashDispenser.Denominate(170000)
    R-->>M: mezcla {1000:1, 500:1, 200:1}
    M->>L: CardReader.GenerateAC (monto, STAN)
    L-->>M: campo 55 (ARQC, ATC)
    M->>B: AUTORIZANDO con STAN, fecha y RRN (escritura anticipada)
    M->>H: 0200
    H-->>M: 0210 · 00 · autorización · saldo
    M->>B: AUTORIZADA → ENTREGANDO
    M->>R: CashDispenser.Dispense → Present
    R-->>M: retirado {1000:1, 500:1, 200:1}
    rect rgba(120, 160, 220, 0.15)
        note over M,LM: una sola transacción SQLite
        M->>LM: asiento «retiro»: compensación +, casetes −
        M->>B: CONFIRMADA (entregado 170000)
    end
    M->>L: CardReader.Eject (la sesión olvida el PAN)
    M->>I: Printer.PrintForm (PAN enmascarado)
    M-->>C: CONFIRMADA · comprobante
```

## 3. Reversa tras un apagón a media entrega

El corte llega mientras los billetes están en el transporte del reciclador. Nada de lo que estaba en memoria
sobrevive; sólo la bitácora y la memoria no volátil del dispositivo.

```mermaid
sequenceDiagram
    autonumber
    participant M as Máquina de estados
    participant B as Bitácora
    participant R as Reciclador<br/>(memoria no volátil)
    participant LM as Libro mayor
    participant RV as Reenvío de reversas
    participant H as Banco

    note over M,H: la operación ya está ENTREGANDO: el banco aprobó la 0200 y cobró
    M->>R: CashDispenser.Dispense
    R--xM: corte de energía (billetes en el transporte, fase «en_transporte»)
    note over M: el proceso muere · el cajero queda fuera de servicio

    note over M,H: reinicio: proceso nuevo, misma base, mismo hardware
    M->>R: encender
    R-->>M: transporte purgado a rechazo · fase «no_presentado»
    M->>B: pendientes del cajero
    B-->>M: operación en ENTREGANDO
    M->>R: CashDispenser.GetPresentStatus(operación)
    R-->>M: entregado {} · rechazo {1000:1, 500:1, 200:1}
    rect rgba(120, 160, 220, 0.15)
        note over M,LM: una sola transacción SQLite
        M->>LM: asiento interno casete → rechazo
        M->>B: encolar reversa total (0420, motivo 22) y pasar a EN_REVERSA
    end
    M->>RV: drenar
    RV->>H: 0420 (campo 90: STAN y fecha de la 0200 original)
    H--xRV: sin respuesta
    RV->>B: intento 1 · próximo en 0.5 s
    RV->>H: 0421 (repetición)
    H-->>RV: 0430 · 00 (el banco devuelve el cargo una sola vez)
    RV->>B: reversa ACUSADA · operación REVERTIDA
```

Si el corte llega **después** de que el cliente tomó el efectivo, la memoria no volátil dice «retirado» y la
recuperación **confirma** la operación (no hay reversa). Ver `tests/test_fallas.py::test_retiro_con_falla`
(`corte_tras_retiro`) y la tabla de recuperación en `tests/test_maquinas_de_estado.py`.

## 4. Cuadre de fin de día

```mermaid
sequenceDiagram
    autonumber
    actor O as Operador
    participant A as API
    participant Q as Cuadre<br/>(recon/cuadre.py)
    participant RV as Reenvío de reversas
    participant R as Reciclador
    participant LM as Libro mayor
    participant H as Banco
    participant B as Bitácora

    O->>A: GET /cajeros/TILL0001/cuadre
    A->>Q: cuadrar(cajero, banco)
    Q->>RV: drenar (un intento por reversa pendiente)
    Q->>R: CashManagement.GetCashUnitInfo
    R-->>Q: billetes por ubicación y denominación (casete, rechazo, retenido, excedente)
    Q->>LM: saldos cajero:TILL0001:*
    note over Q: banda 1 · físico contra libro, cuenta por cuenta
    Q->>H: totales aprobados netos por tipo de operación
    Q->>LM: saldos de compensación por tipo
    note over Q: banda 2 · libro contra banco
    Q->>B: reversas pendientes · aclaraciones
    note over Q: banda 3 · lo que explica una diferencia legítima
    alt sin diferencias
        Q-->>A: CUADRA
    else sólo reversas sin acuse o efectivo en tránsito
        Q-->>A: PRELIMINAR
    else faltante, sobrante, billetes a medio proceso o diferencia sin explicar
        Q-->>A: DIFERENCIAS (con la cuenta y el monto)
    end
    A-->>O: reporte
```

## 5. Una solicitud de la demo publicada (API en el navegador)

```mermaid
sequenceDiagram
    autonumber
    participant P as Página (React)
    participant W as Web worker
    participant Y as Pyodide<br/>(CPython 3.14 · WebAssembly)
    participant F as App FastAPI<br/>(till/api/app.py)

    P->>W: postMessage {id, POST, /api/cajeros/TILL0001/operaciones, encabezados, cuerpo}
    note over W,Y: la primera vez: carga Pyodide, los paquetes del lockfile recortado y el código de till
    W->>Y: llamar(metodo, ruta, encabezados, cuerpo)
    Y->>F: llamada ASGI (web/src/motor/puente.py)
    F-->>Y: http.response.start · http.response.body
    Y-->>W: [estado, texto]
    W-->>P: postMessage {id, estado, texto}
```
