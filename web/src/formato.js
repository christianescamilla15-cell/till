// Formato de dinero, números, cuentas del libro mayor y campos ISO 8583.
// El servidor maneja centavos enteros; aquí sólo se presentan.

const LOCALE = { es: 'es-MX', en: 'en-US' }

export function dinero(centavos) {
  const n = Math.round(Number(centavos) || 0)
  const signo = n < 0 ? '-' : ''
  const a = Math.abs(n)
  const pesos = Math.floor(a / 100).toLocaleString('en-US')
  return `${signo}$${pesos}.${String(a % 100).padStart(2, '0')}`
}

export function pesos(enteros) {
  return `$${Number(enteros).toLocaleString('en-US')}`
}

export function numero(n, idioma, opciones = {}) {
  return new Intl.NumberFormat(LOCALE[idioma] || 'es-MX', opciones).format(n)
}

export function compacto(n, idioma) {
  if (Math.abs(n) < 10_000) return numero(n, idioma)
  return new Intl.NumberFormat(LOCALE[idioma] || 'es-MX', { notation: 'compact', maximumFractionDigits: 1 }).format(n)
}

export function porcentaje(fraccion, idioma, decimales = 2) {
  return new Intl.NumberFormat(LOCALE[idioma] || 'es-MX', {
    style: 'percent', minimumFractionDigits: decimales, maximumFractionDigits: decimales,
  }).format(fraccion)
}

export function segundos(s, idioma) {
  if (s < 1) return `${numero(Math.round(s * 1000), idioma)} ms`
  return `${numero(s, idioma, { maximumFractionDigits: 2 })} s`
}

export function fechaCorta(iso, idioma) {
  const [a, m, d] = iso.split('-').map(Number)
  return new Intl.DateTimeFormat(LOCALE[idioma] || 'es-MX', { weekday: 'short', day: 'numeric', month: 'short', timeZone: 'UTC' })
    .format(new Date(Date.UTC(a, m - 1, d)))
}

export function mezclaTexto(mezcla) {
  return Object.entries(mezcla || {})
    .filter(([, n]) => n > 0)
    .sort((a, b) => Number(b[0]) - Number(a[0]))
    .map(([d, n]) => `${n} × ${pesos(d)}`)
    .join(' · ')
}

export function tarjetaCorta(enmascarado) {
  return enmascarado ? `•••• ${enmascarado.slice(-4)}` : ''
}

// -- cuentas del libro mayor --------------------------------------------------------
const NOMBRE_UBICACION = {
  es: { casete: 'Casete', rechazo: 'Rechazo', retenido: 'Retenido', excedente: 'Excedente' },
  en: { casete: 'Cassette', rechazo: 'Reject bin', retenido: 'Retained', excedente: 'Overflow' },
}
const NOMBRE_TIPO = {
  es: { retiro: 'retiros', deposito: 'depósitos', pago_credito: 'pagos de crédito', remesa: 'remesas' },
  en: { retiro: 'withdrawals', deposito: 'deposits', pago_credito: 'loan payments', remesa: 'remittances' },
}

export function cuentaLegible(cuenta, idioma) {
  const p = cuenta.split(':')
  const es = idioma !== 'en'
  if (p[0] === 'cajero' && p.length === 4) return `${NOMBRE_UBICACION[es ? 'es' : 'en'][p[2]] || p[2]} ${pesos(p[3])}`
  if (p[0] === 'compensacion') return es ? `Compensación banco · ${NOMBRE_TIPO.es[p[2]] || p[2]}` : `Bank settlement · ${NOMBRE_TIPO.en[p[2]] || p[2]}`
  if (p[0] === 'transito') return es ? 'Efectivo en tránsito' : 'Cash in transit'
  if (p[0] === 'aclaraciones') return es ? 'Aclaraciones de clientes' : 'Customer claims'
  if (cuenta === 'boveda:central') return es ? 'Bóveda central' : 'Central vault'
  return cuenta
}

// -- ISO 8583 -----------------------------------------------------------------------
export const NOMBRE_MTI = {
  '0200': ['Solicitud financiera', 'Financial request'],
  '0210': ['Respuesta financiera', 'Financial response'],
  '0420': ['Aviso de reversa', 'Reversal advice'],
  '0421': ['Repetición del aviso de reversa', 'Reversal advice repeat'],
  '0430': ['Acuse de reversa', 'Reversal acknowledgement'],
  '0800': ['Administración de red', 'Network management'],
  '0810': ['Respuesta de red', 'Network response'],
}

export const NOMBRE_CAMPO = {
  2: ['PAN (enmascarado)', 'PAN (masked)'],
  3: ['Código de proceso', 'Processing code'],
  4: ['Monto', 'Amount'],
  7: ['Fecha y hora de transmisión (UTC)', 'Transmission date and time (UTC)'],
  11: ['STAN', 'STAN'],
  12: ['Hora local', 'Local time'],
  13: ['Fecha local', 'Local date'],
  32: ['Adquirente', 'Acquirer'],
  37: ['RRN', 'RRN'],
  38: ['Autorización', 'Authorization code'],
  39: ['Respuesta / motivo', 'Response / reason'],
  41: ['Terminal', 'Terminal'],
  42: ['Comercio', 'Merchant'],
  43: ['Ubicación', 'Location'],
  49: ['Moneda', 'Currency'],
  54: ['Saldo', 'Balance'],
  70: ['Código de red', 'Network code'],
  90: ['Datos originales', 'Original data'],
  95: ['Montos de reemplazo', 'Replacement amounts'],
  103: ['Cuenta destino', 'Destination account'],
}

const RESPUESTA = {
  '00': ['aprobada', 'approved'],
  '05': ['no honrar', 'do not honor'],
  12: ['transacción inválida', 'invalid transaction'],
  13: ['monto inválido', 'invalid amount'],
  14: ['tarjeta inválida', 'invalid card'],
  51: ['fondos insuficientes', 'insufficient funds'],
  61: ['excede el límite de retiro', 'exceeds withdrawal limit'],
  91: ['emisor no disponible', 'issuer unavailable'],
}
// En la 0420 el campo 39 lleva el motivo de la reversa (convención del simulador).
const MOTIVO_REVERSA = {
  22: ['falla del dispositivo', 'device malfunction'],
  32: ['entrega parcial', 'partial dispense'],
  68: ['sin respuesta del banco', 'no response from bank'],
}
const PROCESO = {
  '01': ['retiro', 'withdrawal'],
  21: ['depósito', 'deposit'],
  50: ['pago de crédito', 'loan payment'],
  40: ['remesa', 'remittance'],
}

const i = (idioma) => (idioma === 'en' ? 1 : 0)

export function nombreMti(mti, idioma) {
  return (NOMBRE_MTI[mti] || [mti, mti])[i(idioma)]
}

export function nombreCampo(n, idioma) {
  return (NOMBRE_CAMPO[n] || [`${n}`, `${n}`])[i(idioma)]
}

export function descRespuesta(codigo, idioma) {
  return (RESPUESTA[codigo] || [codigo, codigo])[i(idioma)]
}

function campo54(v) {
  if (!v || v.length < 20) return v
  const n = Number(v.slice(8, 20))
  return dinero(v[7] === 'C' ? n : -n)
}

// Valor legible de un campo (el crudo sigue disponible en la tabla completa).
export function valorCampo(n, v, mti, idioma) {
  n = Number(n)
  if (v === undefined || v === null) return ''
  if (n === 4) return dinero(Number(v))
  if (n === 3) return `${v} · ${(PROCESO[v.slice(0, 2)] || [v, v])[i(idioma)]}`
  if (n === 39) {
    const tabla = mti === '0420' || mti === '0421' ? MOTIVO_REVERSA : RESPUESTA
    return `${v} · ${(tabla[v] || tabla[Number(v)] || [v, v])[i(idioma)]}`
  }
  if (n === 54) return campo54(v)
  if (n === 95) return dinero(Number(v.slice(0, 12)))
  if (n === 90) return `${v.slice(0, 4)} · STAN ${v.slice(4, 10)}`
  if (n === 49) return v === '484' ? '484 · MXN' : v
  return v
}

// Campos que se muestran sin abrir el detalle, por tipo de mensaje.
export const CAMPOS_PRINCIPALES = {
  '0200': [2, 3, 4, 11, 37],
  '0210': [39, 38, 54],
  '0420': [39, 4, 90, 95, 11],
  '0421': [39, 4, 90, 95, 11],
  '0430': [39],
  '0800': [70, 11],
  '0810': [39],
}

// -- textos que redacta el núcleo (en español) ------------------------------------
const TEXTOS_EN = [
  [/^banco: /, ''],
  [/^monto no disponible en este cajero/, 'amount not available in this teller'],
  [/^lectura de tarjeta: (.*)$/, 'card read error: $1'],
  [/^chip: (.*)$/, 'chip error: $1'],
  [/^conteo: (.*)$/, 'bill validator: $1'],
  [/^ningún billete aceptado/, 'no bill was accepted'],
  [/^interrumpida antes de autorizar/, 'interrupted before authorization'],
  [/^interrumpida durante el conteo/, 'interrupted while counting'],
  [/^falla de entrega \((.*)\)$/, 'dispense failure ($1)'],
  [/^entrega parcial \((.*)\)$/, 'partial dispense ($1)'],
  [/^efectivo no retirado/, 'cash not taken'],
  [/^sin respuesta del banco/, 'no response from the bank'],
  [/^caída esperando la 0210/, 'crash while waiting for the 0210'],
  [/^caída antes de entregar/, 'crash before dispensing'],
  [/^caída antes de guardar el efectivo/, 'crash before storing the cash'],
  [/^no se pudo guardar el efectivo \((.*)\)$/, 'could not store the cash ($1)'],
  [/corte de energía/, 'power cut'],
  [/conexión rechazada por el host/, 'connection refused by the host'],
  [/sin respuesta \(la solicitud no llegó\)/, 'no response (the request never arrived)'],
  [/sin respuesta \(el host sí procesó\)/, 'no response (the host did process it)'],
  [/sin respuesta a tiempo/, 'no response in time'],
  [/acuse sin código 00/, 'acknowledgement without code 00'],
]

export function textoNucleo(m, idioma) {
  if (!m) return ''
  let s = String(m).replace(/^banco: /, '')
  if (idioma !== 'en') return s
  for (const [re, en] of TEXTOS_EN) s = s.replace(re, en)
  return s
}
