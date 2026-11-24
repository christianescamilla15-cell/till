#!/usr/bin/env bash
# Valida docs/arquitectura/till.archimate.xml contra el XSD OFICIAL de The Open Group (ArchiMate 3.1 Model
# Exchange File Format) con xmllint. Los XSD no se versionan aquí: se bajan de opengroup.org a un directorio
# temporal (y xml.xsd del W3C, que importan) cada vez.
#
#   herramientas/validar_archimate.sh            # requiere xmllint (paquete libxml2-utils) y curl
set -euo pipefail
RAIZ="$(cd "$(dirname "$0")/.." && pwd)"
MODELO="$RAIZ/docs/arquitectura/till.archimate.xml"
XSD="$(mktemp -d)"
trap 'rm -rf "$XSD"' EXIT
for f in archimate3_Model.xsd archimate3_View.xsd archimate3_Diagram.xsd; do
  curl -fsSL --retry 3 -o "$XSD/$f" "https://www.opengroup.org/xsd/archimate/3.1/$f"
done
curl -fsSL --retry 3 -o "$XSD/xml.xsd" "https://www.w3.org/2001/xml.xsd"
# El XSD del modelo importa xml.xsd por URL; se apunta a la copia local para no depender de la red de nuevo.
sed -i 's#schemaLocation="http://www.w3.org/2001/xml.xsd"#schemaLocation="xml.xsd"#' "$XSD/archimate3_Model.xsd"
xmllint --version 2>&1 | head -1
xmllint --noout --schema "$XSD/archimate3_Diagram.xsd" "$MODELO"
