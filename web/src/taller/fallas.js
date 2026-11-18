// Las cinco fallas del taller y cómo se programan en la API.
// Las de dispositivo se disparan en el siguiente retiro (CashDispenser.Dispense);
// las de enlace, en el siguiente mensaje al banco.

export const FALLAS = [
  {
    clave: 'atasco',
    dispositivo: { dispositivo: 'efectivo', falla: 'atasco', comando: 'CashDispenser.Dispense' },
  },
  {
    clave: 'entrega_parcial',
    dispositivo: { dispositivo: 'efectivo', falla: 'entrega_parcial', comando: 'CashDispenser.Dispense', parametros: { fraccion: 0.5 } },
  },
  {
    // La 0200 llega y el banco aprueba (y cobra), pero la 0210 se pierde en el camino.
    clave: 'tiempo_agotado',
    enlace: { falla: 'respuesta_perdida', mti: '0200' },
  },
  {
    clave: 'corte_energia',
    dispositivo: { dispositivo: 'efectivo', falla: 'corte_energia', comando: 'CashDispenser.Dispense' },
  },
  {
    // El enlace rechaza la 0200 y los tres primeros avisos de reversa (0420, 0421, 0421):
    // la reversa se queda guardada y el cuarto intento recibe el 0430.
    clave: 'enlace_caido',
    enlace: { falla: 'host_caido', veces: 4 },
  },
]

export function fallaArmada(tablero) {
  if (!tablero) return null
  for (const f of tablero.fallas.dispositivos.efectivo || []) {
    if (f.comando === 'CashDispenser.Dispense' && ['atasco', 'entrega_parcial', 'corte_energia'].includes(f.falla)) return f.falla
  }
  for (const f of tablero.fallas.enlace || []) {
    if (f.falla === 'respuesta_perdida') return 'tiempo_agotado'
    if (f.falla === 'host_caido') return 'enlace_caido'
  }
  return null
}

export function restantesEnlace(tablero) {
  const f = (tablero?.fallas.enlace || []).find((x) => x.falla === 'host_caido')
  return f ? f.restantes : 0
}
