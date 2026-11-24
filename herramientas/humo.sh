#!/usr/bin/env bash
# Prueba de humo de la demo publicada (sitio estático + motor de Pyodide). Sale con 1 al primer problema.
#
#   herramientas/humo.sh https://till-cajero.vercel.app        # después de cada despliegue (RUNBOOK.md)
#   HUMO_LOCAL=1 herramientas/humo.sh http://127.0.0.1:8000    # contra api.index:app sirviendo web/dist
#
# Comprueba lo que la publicación puede romper sin que lo note el build: que la página salga con la MISMA CSP de
# vercel.json, que el motor (till.json, el intérprete y el lockfile recortado con tzdata) esté completo y que no se
# haya publicado ninguna función de servidor en /api (la API corre en el navegador; ver ADR 0002). Lo que pasa
# DENTRO del motor lo prueban web/scripts/humo_motor.mjs y el E2E de Playwright en el CI.
set -euo pipefail
URL="${1:?uso: herramientas/humo.sh <url>}"
URL="${URL%/}"
RAIZ="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
falla() { echo "✗ $*" >&2; exit 1; }
ok() { echo "✓ $*"; }

codigo=$(curl -sS -o "$TMP/index.html" -D "$TMP/encabezados" -w '%{http_code}' "$URL/")
[ "$codigo" = 200 ] || falla "GET / respondió $codigo"
grep -q '<div id="raiz">' "$TMP/index.html" || falla "la página no trae el contenedor de la demo"
CSP_ESPERADA=$(python3 -c "
import json, sys
cfg = json.load(open(sys.argv[1], encoding='utf-8'))
print(next(h['value'] for b in cfg['headers'] if b['source'] == '/(.*)' for h in b['headers'] if h['key'] == 'Content-Security-Policy'))
" "$RAIZ/vercel.json")
CSP_REAL=$(grep -i '^content-security-policy:' "$TMP/encabezados" | head -1 | cut -d' ' -f2- | tr -d '\r')
[ "$CSP_REAL" = "$CSP_ESPERADA" ] || falla "la CSP publicada no es la de vercel.json: «$CSP_REAL»"
grep -qi '^x-content-type-options: nosniff' "$TMP/encabezados" || falla "falta X-Content-Type-Options: nosniff"
ok "página con la CSP de vercel.json"

codigo=$(curl -sS -o "$TMP/till.json" -w '%{http_code}' "$URL/motor/till.json")
[ "$codigo" = 200 ] || falla "GET /motor/till.json respondió $codigo"
VERSION=$(python3 -c "
import json, sys
m = json.load(open(sys.argv[1], encoding='utf-8'))
esenciales = ['till/__init__.py', 'till/api/app.py', 'till/api/sesiones.py', 'till/red.py', 'till/core/maquina.py']
faltan = [x for x in esenciales if x not in m['archivos']]
assert not faltan, f'faltan módulos en el motor: {faltan}'
assert 'tzdata' in m['paquetes'], 'el motor no trae tzdata (la hora local saldría en UTC)'
print(m['pyodide'])
" "$TMP/till.json") || falla "till.json incompleto"
ok "motor/till.json: Pyodide $VERSION, módulos esenciales y tzdata"

for f in pyodide.mjs pyodide.asm.wasm python_stdlib.zip pyodide-lock.json; do
  codigo=$(curl -sS -o "$TMP/$f" -w '%{http_code}' "$URL/motor/pyodide-$VERSION/$f")
  [ "$codigo" = 200 ] || falla "GET /motor/pyodide-$VERSION/$f respondió $codigo"
done
python3 -c "
import json, sys
lock = json.load(open(sys.argv[1], encoding='utf-8'))
for p in ('fastapi', 'pydantic', 'tzdata', 'pyiso8583'):
    assert p in lock['packages'], p
" "$TMP/pyodide-lock.json" || falla "el lockfile recortado del motor no trae los paquetes de la API"
ok "intérprete y lockfile recortado del motor"

if [ -z "${HUMO_LOCAL:-}" ]; then
  codigo=$(curl -sS -o /dev/null -w '%{http_code}' "$URL/api/salud")
  [ "$codigo" = 404 ] || falla "GET /api/salud respondió $codigo: ¿se publicó una función de servidor? (.vercelignore)"
  ok "sin funciones de servidor en /api"
fi
echo "humo: sin problemas en $URL"
