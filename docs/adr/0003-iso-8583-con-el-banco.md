# ADR 0003 · El cajero habla con el banco en ISO 8583, no en ISO 20022

- Estado: aceptada · Fecha: 2026-11-17 (commit `5e9c470`)

## Contexto
Un cajero automático autoriza en línea cada operación con el emisor de la tarjeta: solicitud financiera,
respuesta, reversa y su acuse, en segundos y con mensajes de pocos cientos de bytes. Till necesita mostrar ese
diálogo con sus fallas reales (respuesta perdida, reversa repetida, reversa sin original) y con el detalle de
los campos que usan las redes de cajeros (STAN, RRN, campo 55 del chip, campo 90 de datos originales, campo 95
de montos de reemplazo).

## Decisión
ISO 8583 (versión 1987, codificación ASCII) sobre `pyiso8583` (licencia MIT): 0200/0210, 0420/0421/0430 y
0800/0810, transporte en proceso con fallas inyectables y TCP con marco de 2 bytes de longitud.

## Alternativas descartadas
| Alternativa | Por qué no |
|---|---|
| **ISO 20022** (pacs/camt, XML o JSON) | Es el estándar de pagos interbancarios, transferencias y reportes de cuenta, y es lo que conviene para pagos internacionales y liquidación. ISO 20022 también tiene mensajes de cajero a host (familia `catp`, de la iniciativa ATICA), pero su adopción en las redes de cajeros es todavía limitada: la autorización de tarjeta en cajero sigue mayoritariamente en ISO 8583. Simular el cajero en 20022 mostraría un diálogo que la mayoría de los switches de cajeros no espera. |
| **API propia JSON** | Esconde justo lo que Till quiere enseñar: STAN, fecha de transmisión, campo 90 y la idempotencia que da la llave (terminal, STAN, fecha). |
| **Implementar el empaquetado a mano** | `pyiso8583` está probado, es pequeño, puro Python (corre en Pyodide) y su licencia es MIT. |

## Consecuencias
- El diálogo con el banco es reconocible para quien opera una red de cajeros, y la traza de la demo lo muestra
  campo por campo (PAN enmascarado, sin campo 55).
- Lo que sí iría en ISO 20022 en un banco real queda fuera del cajero y se señala como frontera: la liquidación
  con el banco y los reportes de cuenta (hoy el cuadre lee los totales del banco simulado; en producción
  llegarían por archivo de liquidación o mensajes 0500/0510, o por camt en una red 20022).
- Sin MAC (campos 64/128) ni cifrado del enlace: simplificación declarada en el README.
