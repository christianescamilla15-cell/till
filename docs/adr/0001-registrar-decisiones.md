# ADR 0001 · Registrar las decisiones de arquitectura

- Estado: aceptada · Fecha: 2026-11-24

## Contexto
Las decisiones importantes de Till (dónde corre la API, qué protocolo habla con el banco, cómo se guarda el
dinero) estaban en mensajes de commit y en el README. Quien llega al repo ve el «qué» pero no el «por qué» ni
lo que se descartó.

## Decisión
Cada decisión que cambie la estructura, una dependencia mayor o un contrato se registra como ADR numerado en
`docs/adr/`, con contexto, decisión, **alternativas descartadas y por qué**, y consecuencias. Las decisiones
anteriores a este registro (0002–0005) se documentan con su fecha original, tomada de la historia de Git.

## Alternativas descartadas
- **Sólo el README.** Mezcla el manual de uso con el razonamiento y no deja ver qué se descartó.
- **Wiki externa.** Se desincroniza del código; un ADR viaja con el commit que lo implementa.

## Consecuencias
Quien llegue al repo entiende por qué el sistema es como es. Un ADR no se reescribe: si una decisión cambia,
se escribe otro que lo reemplaza y se marca el anterior como «reemplazada por».
