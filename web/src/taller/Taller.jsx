// Zona 2 · el taller de fallas: armar una falla, reiniciar el cajero y ver qué pasó por dentro
// (línea de tiempo de la transacción, mensajes ISO 8583 y la reversa hasta su 0430).
import { useIdioma } from '../i18n.js'
import { CAMPOS_PRINCIPALES, descRespuesta, dinero, mezclaTexto, nombreCampo, nombreMti, numero, textoNucleo, valorCampo } from '../formato.js'
import { Alerta, Flecha, FlechaIzq, Palomita, Reiniciar, Reloj, Sin, Tache } from '../iconos.jsx'
import { FALLAS, fallaArmada, restantesEnlace } from './fallas.js'

const DETALLES_EN = [
  [/^efectivo tomado$/, 'cash taken'],
  [/^efectivo guardado$/, 'cash stored'],
  [/^0430 recibido \(parcial\)$/, '0430 received (partial)'],
  [/^0430 recibido$/, '0430 received'],
  [/^motivo ISO (\d+)$/, 'ISO reason $1'],
  [/^retiro$/, 'withdrawal'],
  [/^deposito$/, 'deposit'],
  [/^pago_credito$/, 'loan payment'],
  [/^remesa$/, 'remittance'],
]
function detalleLegible(d, idioma) {
  if (!d || idioma !== 'en') return d
  for (const [re, en] of DETALLES_EN) if (re.test(d)) return d.replace(re, en)
  return d
}

function desplazamiento(momento, t0, idioma) {
  const ms = Date.parse(momento) - t0
  if (!Number.isFinite(ms)) return ''
  if (ms < 1000) return `+${numero(Math.max(0, ms), idioma)} ms`
  return `+${numero(ms / 1000, idioma, { maximumFractionDigits: 1 })} s`
}

// AUTORIZAR → ENTREGAR (o GUARDAR) → CONFIRMAR | REVERTIR, a partir del historial real.
function pasos(det) {
  const h = new Set(det.historial.map((x) => x.a))
  const op = det.operacion
  const ingreso = op.tipo !== 'retiro'
  let aut = 'pendiente'
  if (h.has('AUTORIZADA')) aut = 'hecho'
  else if (h.has('RECHAZADA') || (h.has('AUTORIZANDO') && h.has('EN_REVERSA'))) aut = 'fallo'
  else if (h.has('AUTORIZANDO')) aut = 'en_curso'
  else if (h.has('CANCELADA')) aut = 'omitido'

  const fase = ingreso ? 'RECIBIENDO' : 'ENTREGANDO'
  let ent = 'pendiente'
  if (h.has(fase)) {
    if (h.has('CONFIRMADA')) ent = 'hecho'
    else if (h.has('EN_REVERSA')) ent = !ingreso && op.entregado > 0 ? 'parcial' : 'fallo'
    else ent = 'en_curso'
  } else if (aut === 'fallo' || aut === 'omitido' || h.has('EN_REVERSA')) ent = 'omitido'

  let cierre = ['paso_confirmar', 'pendiente']
  if (h.has('CONFIRMADA')) cierre = ['paso_confirmar', 'hecho']
  else if (h.has('REVERTIDA') || h.has('PARCIAL')) cierre = ['paso_revertir', 'hecho']
  else if (h.has('EN_REVERSA')) cierre = ['paso_revertir', 'en_curso']
  else if (h.has('RECHAZADA') || h.has('CANCELADA')) cierre = ['paso_confirmar', 'omitido']

  return [
    ['paso_autorizar', aut],
    [ingreso ? 'paso_recibir' : 'paso_entregar', ent],
    cierre,
  ]
}

const ICONO_PASO = { hecho: Palomita, fallo: Tache, parcial: Alerta, en_curso: Reloj, omitido: Sin, pendiente: Sin }

function Pasos({ det }) {
  const { t } = useIdioma()
  const lista = pasos(det)
  return (
    <ol className="pasos">
      {lista.map(([clave, estado], k) => {
        const Icono = ICONO_PASO[estado]
        return (
          <li key={clave} className={`paso paso-${estado}`}>
            <span className="paso-icono"><Icono tam={16} /></span>
            <span className="paso-nombre">{t(clave)}</span>
            <span className="paso-estado">{t(`paso_${estado}`)}</span>
            {k < lista.length - 1 && <Flecha tam={14} clase="paso-flecha" />}
          </li>
        )
      })}
    </ol>
  )
}

function Mensaje({ m, t0 }) {
  const { t, idioma } = useIdioma()
  const s = m.solicitud
  const r = m.respuesta
  const campos = (msg) => (CAMPOS_PRINCIPALES[msg.t] || []).filter((n) => msg[n] !== undefined)
  const falla = {
    tiempo_agotado: t('res_tiempo_agotado'),
    host_caido: t('res_host_caido'),
    corte_energia: t('res_corte_energia'),
  }[m.resultado]
  const perdida = m.respuesta_perdida
  return (
    <li className={`iso ${m.resultado === 'respuesta' ? '' : 'iso-fallo'}`}>
      <div className="iso-linea">
        <span className="iso-t">{desplazamiento(m.momento, t0, idioma)}</span>
        <Flecha tam={14} clase="iso-dir" />
        <code className="iso-mti">{s.t}</code>
        <span className="iso-nombre">{nombreMti(s.t, idioma)}</span>
      </div>
      <dl className="iso-campos">
        {campos(s).map((n) => (
          <div key={n}><dt>{`${n} · ${nombreCampo(n, idioma)}`}</dt><dd>{valorCampo(n, s[n], s.t, idioma)}</dd></div>
        ))}
      </dl>
      {r ? (
        <>
          <div className="iso-linea iso-resp">
            <span className="iso-t">{`${numero(m.duracion_ms, idioma, { maximumFractionDigits: 1 })} ms`}</span>
            <FlechaIzq tam={14} clase="iso-dir" />
            <code className="iso-mti">{r.t}</code>
            <span className="iso-nombre">{nombreMti(r.t, idioma)}</span>
          </div>
          <dl className="iso-campos">
            {campos(r).map((n) => (
              <div key={n}><dt>{`${n} · ${nombreCampo(n, idioma)}`}</dt><dd>{valorCampo(n, r[n], r.t, idioma)}</dd></div>
            ))}
          </dl>
        </>
      ) : (
        <p className="iso-falla">
          <Tache tam={16} /> {falla}
          {perdida && ` ${t('res_respuesta_perdida', { codigo: perdida['39'], desc: descRespuesta(perdida['39'], idioma), mti: perdida.t })}`}
        </p>
      )}
      <details className="iso-detalle">
        <summary>{t('todos_campos')}</summary>
        <TablaCampos titulo={`${s.t} · ${t('enviado')}`} msg={s} />
        {r && <TablaCampos titulo={`${r.t} · ${t('recibido')}`} msg={r} />}
        {perdida && <TablaCampos titulo={`${perdida.t}`} msg={perdida} />}
      </details>
    </li>
  )
}

function TablaCampos({ titulo, msg }) {
  const { t, idioma } = useIdioma()
  const filas = Object.keys(msg).filter((k) => k !== 't').sort((a, b) => Number(a) - Number(b))
  return (
    <table className="tabla tabla-iso">
      <caption>{titulo}</caption>
      <thead><tr><th scope="col">{t('campo')}</th><th scope="col">{t('valor')}</th></tr></thead>
      <tbody>
        {filas.map((k) => (
          <tr key={k}><th scope="row">{`${k} · ${nombreCampo(k, idioma)}`}</th><td><code>{msg[k]}</code></td></tr>
        ))}
      </tbody>
    </table>
  )
}

function Recuperacion({ rec }) {
  const { t } = useIdioma()
  const encendidos = Object.keys(rec.arranque_dispositivos || {})
  const ef = rec.arranque_dispositivos?.efectivo || {}
  return (
    <div className="bloque bloque-recuperacion" role="status">
      <h3 className="bloque-titulo">{t('recuperacion')}</h3>
      <ul className="lista">
        {encendidos.length > 0 && <li>{t('recuperacion_dispositivos', { lista: encendidos.map((d) => t(`dispositivo_${d}`)).join(', ') })}</li>}
        {ef.rechazo && <li>{t('recuperacion_purga', { mezcla: mezclaTexto(ef.rechazo) })}</li>}
        {ef.retenido && <li>{t('recuperacion_retenido', { mezcla: mezclaTexto(ef.retenido) })}</li>}
        {rec.acciones.length === 0 && <li>{t('recuperado_nada')}</li>}
        <li>{t('recuperacion_reversas', { acusadas: rec.reversas.acusadas, pendientes: rec.reversas.pendientes })}</li>
      </ul>
    </div>
  )
}

const TIPO = { retiro: 'tipo_retiro', deposito: 'tipo_deposito', pago_credito: 'tipo_pago_credito', remesa: 'tipo_remesa' }

export default function Taller({ tablero, detalle, armar, reiniciar, ocupado, recuperacion, apagado }) {
  const { t, idioma } = useIdioma()
  const armada = fallaArmada(tablero)
  const datosFalla = FALLAS.find((f) => f.clave === armada)
  const restantes = restantesEnlace(tablero)
  const t0 = detalle ? Date.parse(detalle.historial[0]?.momento) : 0
  const op = detalle?.operacion
  const rev = detalle?.reversa

  return (
    <section className="zona zona-taller" aria-labelledby="titulo-taller">
      <h2 className="zona-titulo" id="titulo-taller"><span className="zona-num">2</span>{t('zona_taller')}</h2>
      <p className="zona-intro">{t('taller_intro')}</p>

      <div className="fallas" role="group" aria-labelledby="fallas-titulo">
        <p className="campo-etiqueta" id="fallas-titulo">{t('fallas_grupo')}</p>
        {FALLAS.map((f) => (
          <button
            key={f.clave}
            type="button"
            className="falla"
            aria-pressed={armada === f.clave}
            disabled={ocupado === 'armar' || ocupado === 'operar' || apagado}
            onClick={() => armar(armada === f.clave ? null : f.clave)}
          >
            <span className="falla-titulo">
              {t(`falla_${f.clave}`)}
              {armada === f.clave && <span className="etiqueta-armada">{t('armada')}</span>}
            </span>
            <span className="falla-d">{t(`falla_${f.clave}_d`)}</span>
          </button>
        ))}
      </div>
      <div className="armada" role="status">
        <p>
          {armada
            ? t('armada_estado', { falla: t(`falla_${armada}`), cuando: t(datosFalla?.dispositivo ? 'cuando_retiro' : 'cuando_cualquiera') })
            : t('sin_falla')}
          {armada === 'enlace_caido' && restantes < 4 && ` ${t('enlace_restantes', { n: restantes })}`}
        </p>
        {armada && (
          <button type="button" className="boton boton-chico" onClick={() => armar(null)} disabled={ocupado === 'armar'}>
            {t('quitar_falla')}
          </button>
        )}
      </div>

      <div className="reinicio">
        <button type="button" className={`boton ${apagado ? 'boton-primario' : ''}`} onClick={reiniciar} disabled={ocupado === 'reiniciar' || ocupado === 'operar'}>
          <Reiniciar tam={18} /> {t('reiniciar_cajero')}
        </button>
        <p className="nota">{t('reiniciar_ayuda')}</p>
      </div>
      {recuperacion && <Recuperacion rec={recuperacion} />}

      <div className="bloque">
        <h3 className="bloque-titulo">{t('linea_tiempo')}</h3>
        {!detalle ? (
          <p className="nota">{t('sin_operacion')}</p>
        ) : (
          <>
            <p className="op-resumen">
              {t('op_resumen', { tipo: t(TIPO[op.tipo]), monto: dinero(op.monto), estado: t(`estado_${op.estado}`) })}
              {op.enmascarado && <span className="tenue">{` · ${op.enmascarado}`}</span>}
            </p>
            <Pasos det={detalle} />
            <ol className="transiciones">
              {detalle.historial.map((x, k) => (
                <li key={k}>
                  <span className="iso-t">{desplazamiento(x.momento, t0, idioma)}</span>
                  <code>{x.de ? `${x.de} → ${x.a}` : x.a}</code>
                  {x.detalle && <span className="tenue">{detalleLegible(x.detalle, idioma)}</span>}
                </li>
              ))}
            </ol>
            {op.motivo && <p className="nota">{t('motivo', { motivo: textoNucleo(op.motivo, idioma) })}</p>}
          </>
        )}
      </div>

      {detalle && (
        <div className="bloque">
          <h3 className="bloque-titulo">{t('mensajes_iso')}</h3>
          <p className="nota">{t('mensajes_iso_nota')}</p>
          {detalle.mensajes.length === 0 ? (
            <p className="nota">{t('sin_mensajes')}</p>
          ) : (
            <ol className="isos">
              {detalle.mensajes.map((m) => <Mensaje key={m.id} m={m} t0={t0} />)}
            </ol>
          )}
        </div>
      )}

      {rev && (
        <div className={`bloque bloque-reversa ${rev.estado === 'ACUSADA' ? 'reversa-ok' : 'reversa-pendiente'}`}>
          <h3 className="bloque-titulo">{t('reversa')}</h3>
          <p className="reversa-estado" role="status">
            {rev.estado === 'ACUSADA' ? <Palomita tam={18} /> : <Reloj tam={18} />}
            {' '}{t(rev.estado === 'ACUSADA' ? 'reversa_acuse' : 'reversa_pendiente')}
          </p>
          <ul className="lista">
            <li>{t('reversa_intentos', { n: rev.intentos })}</li>
            <li>{`${t('motivo', { motivo: valorCampo(39, rev.motivo, '0420', idioma) })}`}</li>
            {rev.reemplazo > 0 && <li>{t('reversa_reemplazo', { m: dinero(rev.reemplazo) })}</li>}
            {rev.ultimo_error && <li>{t('reversa_ultimo_error', { e: textoNucleo(rev.ultimo_error, idioma) })}</li>}
          </ul>
        </div>
      )}
    </section>
  )
}
