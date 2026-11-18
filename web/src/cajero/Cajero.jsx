// Zona 1 · el cajero: la pantalla del cliente (la misma del render) y el lector sin contacto.
// La pantalla es una pequeña máquina de estados de la UI; la transacción de verdad la
// decide el núcleo de Till a través de la API.
import { useEffect, useRef, useState } from 'react'
import { useIdioma } from '../i18n.js'
import { dinero, pesos, mezclaTexto, tarjetaCorta, descRespuesta, textoNucleo } from '../formato.js'
import { Chevron, Energia, Menos, Mas, Microfono, Palomita, Tarjeta } from '../iconos.jsx'

const RAPIDOS = [200, 500, 1000, 2000, 2500, 5000]
const DENOMINACIONES = [1000, 500, 200, 100, 50, 20]
const MAX_RETIRO = 9000

function useReloj() {
  const formato = () => {
    const d = new Date()
    return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  }
  const [hora, setHora] = useState(formato)
  useEffect(() => {
    const id = setInterval(() => setHora(formato()), 15_000)
    return () => clearInterval(id)
  }, [])
  return hora
}

function saludo(t) {
  const h = new Date().getHours()
  if (h >= 5 && h < 12) return t('buenos_dias')
  if (h >= 12 && h < 19) return t('buenas_tardes')
  return t('buenas_noches')
}

function marca(pan) {
  if (pan.startsWith('4')) return 'Visa'
  if (pan.startsWith('5')) return 'Mastercard'
  if (pan.startsWith('3')) return 'Amex'
  return ''
}

const TITULO_OP = { retiro: 'op_retiro', deposito: 'op_deposito', pago_credito: 'op_pago', remesa: 'op_remesa' }

export default function Cajero({ catalogo, tablero, detalle, operar, recuperacion, apagado, reinicioClave, ocupado, tarjetaSel, setTarjetaSel }) {
  const { t, idioma } = useIdioma()
  const hora = useReloj()
  const [p, setP] = useState({ pantalla: 'reposo' })
  const pantallaRef = useRef(null)
  const tituloRef = useRef(null)
  const moverFoco = useRef(false) // acercar la tarjeta (fuera de la pantalla) lleva el foco a la pantalla

  // Lo que viene de afuera manda sobre la pantalla: un corte la apaga, un reinicio la vuelve a encender.
  useEffect(() => {
    if (apagado) setP({ pantalla: 'apagado' })
  }, [apagado])
  useEffect(() => {
    if (recuperacion) setP({ pantalla: 'recuperado', recuperacion })
  }, [recuperacion])
  useEffect(() => {
    if (reinicioClave) setP({ pantalla: 'reposo' })
  }, [reinicioClave])

  // Al cambiar de pantalla, el foco va al título si estaba dentro del cajero (o se perdió).
  useEffect(() => {
    const activo = document.activeElement
    if (tituloRef.current && (moverFoco.current || activo === document.body || pantallaRef.current?.contains(activo))) {
      tituloRef.current.focus({ preventScroll: true })
    }
    moverFoco.current = false
  }, [p.pantalla])

  const tarjetas = catalogo?.tarjetas_de_prueba || []
  const cuentas = tablero?.banco?.cuentas || {}
  const creditos = tablero?.banco?.creditos || {}

  const ir = (cambios) => setP((x) => ({ ...x, ...cambios }))
  const acercar = () => {
    if (!tarjetas[tarjetaSel]) return
    moverFoco.current = true
    setP({ pantalla: 'menu', tarjeta: tarjetas[tarjetaSel] })
  }
  const terminar = () => setP({ pantalla: 'reposo' })
  const elegir = (tipo) => {
    if (tipo === 'retiro') ir({ pantalla: 'monto', tipo })
    else ir({ pantalla: 'billetes', tipo, billetes: {}, destino: tipo === 'pago_credito' ? Object.keys(creditos)[0] || 'CRED-0001' : '' })
  }

  const ejecutar = async () => {
    const x = p
    ir({ pantalla: 'procesando' })
    const cuerpo = { tipo: x.tipo, pan: x.tarjeta.pan }
    if (x.tipo === 'retiro') cuerpo.monto_pesos = x.montoPesos
    else {
      cuerpo.billetes = x.billetes
      if (x.destino) cuerpo.destino = x.destino
    }
    const r = await operar(cuerpo)
    if (r.corte) return // el tablero llega con el cajero fuera de servicio y la pantalla se apaga
    if (r.fuera) return setP({ pantalla: 'apagado' })
    if (r.error) return ir({ pantalla: 'error', error: r.error })
    ir({ pantalla: 'resultado', resultado: r.resultado })
  }

  const enPantalla = (() => {
    switch (p.pantalla) {
      case 'reposo':
        return <Reposo t={t} tituloRef={tituloRef} />
      case 'menu':
        return <Menu t={t} tarjeta={p.tarjeta} elegir={elegir} terminar={terminar} tituloRef={tituloRef} />
      case 'monto':
        return <Monto t={t} tituloRef={tituloRef} regresar={() => ir({ pantalla: 'menu' })} elegir={(m) => ir({ pantalla: 'confirmar', montoPesos: m })} />
      case 'billetes':
        return (
          <Billetes
            t={t} tituloRef={tituloRef} p={p} creditos={creditos}
            regresar={() => ir({ pantalla: 'menu' })}
            seguir={(billetes, destino) => ir({ pantalla: 'confirmar', billetes, destino })}
          />
        )
      case 'confirmar':
        return <Confirmar t={t} tituloRef={tituloRef} p={p} confirmar={ejecutar} cancelar={() => ir({ pantalla: 'menu' })} />
      case 'procesando':
        return (
          <div className="p-cuerpo p-centro" role="status">
            <h3 className="p-titulo" tabIndex={-1} ref={tituloRef}>{t('un_momento')}</h3>
            <p className="p-sub">{t('hablando_banco')}</p>
          </div>
        )
      case 'resultado':
        return <Resultado t={t} idioma={idioma} tituloRef={tituloRef} p={p} detalle={detalle} seguir={() => ir({ pantalla: 'comprobante' })} terminar={terminar} />
      case 'comprobante':
        return (
          <Comprobante
            t={t} tituloRef={tituloRef} p={p} detalle={detalle}
            otra={() => setP({ pantalla: 'menu', tarjeta: p.tarjeta })} terminar={terminar}
          />
        )
      case 'error':
        return (
          <div className="p-cuerpo">
            <h3 className="p-titulo" tabIndex={-1} ref={tituloRef}>{t('cancelada_titulo')}</h3>
            <p className="p-sub">{t('error_operacion', { detalle: typeof p.error?.detalle === 'string' ? p.error.detalle : p.error?.message })}</p>
            <div className="p-acciones"><button type="button" className="p-boton p-activo" onClick={() => setP({ pantalla: 'menu', tarjeta: p.tarjeta })}>{t('regresar')}</button></div>
          </div>
        )
      case 'apagado':
        return (
          <div className="p-cuerpo p-centro p-apagado" role="alert">
            <Energia tam={40} />
            <h3 className="p-titulo" tabIndex={-1} ref={tituloRef}>{t('sin_energia')}</h3>
            <p className="p-sub">{t('sin_energia_texto')}</p>
          </div>
        )
      case 'recuperado':
        return <Recuperado t={t} tituloRef={tituloRef} rec={p.recuperacion} detalle={detalle} seguir={terminar} />
      default:
        return null
    }
  })()

  const sinEncabezado = p.pantalla === 'reposo' || p.pantalla === 'apagado'
  return (
    <section className="zona zona-cajero" aria-labelledby="titulo-cajero" id="cajero">
      <h2 className="zona-titulo" id="titulo-cajero"><span className="zona-num">1</span>{t('zona_cajero')}</h2>
      <div className="maquina">
        <div
          className={`pantalla ${p.pantalla === 'apagado' ? 'pantalla-apagada' : ''}`}
          ref={pantallaRef}
          role="region"
          aria-label={t('pantalla_aria')}
          data-pantalla={p.pantalla}
        >
          {!sinEncabezado && (
            <div className="p-encabezado" aria-hidden="true">
              <span className="p-marca">Till</span>
              <span className="p-hora">{hora}</span>
            </div>
          )}
          {enPantalla}
        </div>
        <div className="lector">
          <p className="lector-titulo"><Tarjeta tam={18} /> {t('lector_titulo')}</p>
          <label className="campo-etiqueta" htmlFor="tarjeta-prueba">{t('lector_tarjeta')}</label>
          <select
            id="tarjeta-prueba"
            className="selector"
            value={tarjetaSel}
            onChange={(e) => setTarjetaSel(Number(e.target.value))}
            disabled={p.pantalla !== 'reposo'}
          >
            {tarjetas.map((tj, k) => (
              <option key={tj.pan} value={k}>{`${marca(tj.pan)} ${tarjetaCorta(tj.enmascarado)} · ${tj.cuenta}`}</option>
            ))}
          </select>
          {tarjetas[tarjetaSel] && cuentas[tarjetas[tarjetaSel].cuenta] !== undefined && (
            <p className="lector-saldo">{t('saldo_en_banco')} <strong>{dinero(cuentas[tarjetas[tarjetaSel].cuenta])}</strong></p>
          )}
          <button
            type="button"
            className="boton boton-primario boton-ancho"
            onClick={acercar}
            disabled={p.pantalla !== 'reposo' || apagado || !tarjetas.length || ocupado === 'operar'}
          >
            <Tarjeta tam={20} /> {t('lector_boton')}
          </button>
          <p className="nota">{apagado ? t('lector_sin_servicio') : t('lector_nota')}</p>
        </div>
      </div>
    </section>
  )
}

function Reposo({ t, tituloRef }) {
  return (
    <div className="p-cuerpo p-centro p-reposo">
      <h3 className="p-logo" tabIndex={-1} ref={tituloRef}>Till</h3>
      <p className="p-sub">{t('abierto')}</p>
      <p className="p-tenue">{t('acerca_tarjeta')}</p>
    </div>
  )
}

function Menu({ t, tarjeta, elegir, terminar, tituloRef }) {
  const ops = [
    ['retiro', 'op_retiro', 'op_retiro_sub'],
    ['deposito', 'op_deposito', 'op_deposito_sub'],
    ['pago_credito', 'op_pago', 'op_pago_sub'],
    ['remesa', 'op_remesa', 'op_remesa_sub'],
  ]
  return (
    <div className="p-cuerpo">
      <h3 className="p-saludo" tabIndex={-1} ref={tituloRef}>{saludo(t)}</h3>
      <p className="p-pregunta">{t('que_quieres')}</p>
      <p className="p-pastilla"><Palomita tam={18} /> {t('tarjeta_leida', { tarjeta: tarjetaCorta(tarjeta.enmascarado) })}</p>
      <ul className="p-opciones">
        {ops.map(([tipo, titulo, sub], k) => (
          <li key={tipo}>
            <button type="button" className={`p-opcion ${k === 0 ? 'p-activo' : ''}`} onClick={() => elegir(tipo)}>
              <span className="p-opcion-texto">
                <span className="p-opcion-titulo">{t(titulo)}</span>
                <span className="p-opcion-sub">{t(sub)}</span>
              </span>
              <Chevron tam={22} />
            </button>
          </li>
        ))}
      </ul>
      <div className="p-voz">
        <span className="p-orbe" aria-hidden="true"><Microfono tam={20} /></span>
        <span>
          <span className="p-voz-titulo">{t('voz_titulo')}</span>
          <span className="p-voz-texto">{t('voz_texto')}</span>
        </span>
      </div>
      <p className="p-pie">
        {t('toca_opcion')} · <button type="button" className="p-enlace" onClick={terminar}>{t('terminar')}</button>
      </p>
    </div>
  )
}

function Monto({ t, tituloRef, regresar, elegir }) {
  const [otro, setOtro] = useState('')
  const [error, setError] = useState('')
  const enviar = (e) => {
    e.preventDefault()
    const n = Number(otro)
    if (!Number.isInteger(n) || n < 20 || n > MAX_RETIRO) return setError(t('monto_invalido'))
    elegir(n)
  }
  return (
    <div className="p-cuerpo">
      <h3 className="p-titulo" tabIndex={-1} ref={tituloRef}>{t('cuanto_retirar')}</h3>
      <p className="p-sub">{t('billetes_disponibles')}</p>
      <div className="p-rejilla">
        {RAPIDOS.map((m) => (
          <button type="button" key={m} className="p-boton" onClick={() => elegir(m)}>{pesos(m)}</button>
        ))}
      </div>
      <form className="p-otro" onSubmit={enviar} noValidate>
        <label htmlFor="otro-monto" className="p-etiqueta">{t('otro_monto')}</label>
        <div className="p-fila">
          <span className="p-prefijo" aria-hidden="true">$</span>
          <input
            id="otro-monto" className="p-entrada" inputMode="numeric" autoComplete="off"
            value={otro} onChange={(e) => { setOtro(e.target.value.replace(/[^0-9]/g, '')); setError('') }}
            aria-describedby="otro-monto-ayuda" aria-invalid={error ? true : undefined}
          />
          <button type="submit" className="p-boton p-activo">{t('continuar')}</button>
        </div>
        <p id="otro-monto-ayuda" className={error ? 'p-error' : 'p-tenue'} role={error ? 'alert' : undefined}>{error || t('otro_monto_ayuda')}</p>
      </form>
      <p className="p-pie"><button type="button" className="p-enlace" onClick={regresar}>{t('regresar')}</button></p>
    </div>
  )
}

function Billetes({ t, tituloRef, p, creditos, regresar, seguir }) {
  const [b, setB] = useState(p.billetes || {})
  const [destino, setDestino] = useState(p.destino || '')
  const total = Object.entries(b).reduce((s, [d, n]) => s + Number(d) * n, 0)
  const cambiar = (d, delta) => setB((x) => ({ ...x, [d]: Math.max(0, Math.min(100, (x[d] || 0) + delta)) }))
  const titulo = { deposito: 'titulo_deposito', pago_credito: 'titulo_pago', remesa: 'titulo_remesa' }[p.tipo]
  const listo = total > 0 && (p.tipo !== 'remesa' || destino.trim().length > 0)
  return (
    <div className="p-cuerpo">
      <h3 className="p-titulo" tabIndex={-1} ref={tituloRef}>{t(titulo)}</h3>
      {p.tipo === 'pago_credito' && (
        <p className="p-sub">{t('credito_info', { credito: p.destino, adeudo: dinero(creditos[p.destino] || 0) })}</p>
      )}
      {p.tipo === 'remesa' && (
        <div className="p-otro">
          <label htmlFor="beneficiario" className="p-etiqueta">{t('beneficiario')}</label>
          <input
            id="beneficiario" className="p-entrada p-entrada-ancha" maxLength={28} autoComplete="off"
            value={destino} onChange={(e) => setDestino(paraCampo103(e.target.value))} aria-describedby="beneficiario-ayuda"
          />
          <p id="beneficiario-ayuda" className="p-tenue">{t('beneficiario_ayuda')}</p>
        </div>
      )}
      <ul className="p-billetes">
        {DENOMINACIONES.map((d) => (
          <li key={d} className="p-billete">
            <span className="p-billete-d">{pesos(d)}</span>
            <button type="button" className="p-mini" onClick={() => cambiar(d, -1)} disabled={!b[d]} aria-label={t('quitar_billete', { d: pesos(d) })}><Menos tam={18} /></button>
            <output className="p-billete-n" aria-label={t('billetes_de', { d: pesos(d) })}>{b[d] || 0}</output>
            <button type="button" className="p-mini" onClick={() => cambiar(d, 1)} aria-label={t('poner_billete', { d: pesos(d) })}><Mas tam={18} /></button>
          </li>
        ))}
      </ul>
      <p className="p-total"><span>{t('total')}</span> <strong>{pesos(total)}</strong></p>
      <div className="p-acciones">
        <button type="button" className="p-boton p-activo" disabled={!listo} onClick={() => seguir(Object.fromEntries(Object.entries(b).filter(([, n]) => n > 0)), p.tipo === 'remesa' ? destino.trim() : p.destino)}>
          {t('continuar')}
        </button>
        <button type="button" className="p-boton" onClick={regresar}>{t('regresar')}</button>
      </div>
    </div>
  )
}

// El beneficiario viaja en el campo 103 de ISO 8583 (ASCII): se quitan acentos y lo no imprimible.
function paraCampo103(texto) {
  return texto.normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/[^\x20-\x7E]/g, '').slice(0, 28)
}

function montoDe(p) {
  if (p.tipo === 'retiro') return p.montoPesos * 100
  return Object.entries(p.billetes || {}).reduce((s, [d, n]) => s + Number(d) * 100 * n, 0)
}

function Confirmar({ t, tituloRef, p, confirmar, cancelar }) {
  return (
    <div className="p-cuerpo">
      <h3 className="p-titulo" tabIndex={-1} ref={tituloRef}>{t('confirma')}</h3>
      <p className="p-monto">{dinero(montoDe(p))}</p>
      <dl className="p-datos">
        <div><dt>{t('conf_operacion')}</dt><dd>{t(TITULO_OP[p.tipo])}</dd></div>
        <div><dt>{t('conf_tarjeta')}</dt><dd>{tarjetaCorta(p.tarjeta.enmascarado)}</dd></div>
        {p.tipo === 'pago_credito' && <div><dt>{t('conf_destino_credito')}</dt><dd>{p.destino}</dd></div>}
        {p.tipo === 'remesa' && <div><dt>{t('conf_destino_remesa')}</dt><dd>{p.destino}</dd></div>}
        {p.tipo !== 'retiro' && <div><dt>{t('billetes')}</dt><dd>{mezclaTexto(p.billetes)}</dd></div>}
      </dl>
      <div className="p-acciones">
        <button type="button" className="p-boton p-activo" onClick={confirmar}>{t('confirmar')}</button>
        <button type="button" className="p-boton" onClick={cancelar}>{t('cancelar')}</button>
      </div>
    </div>
  )
}

function EstadoReversa({ t, rev, parcial }) {
  if (!rev) return null
  return (
    <p className={`p-reversa ${rev.estado === 'ACUSADA' ? 'p-reversa-ok' : ''}`} role="status">
      {rev.estado === 'ACUSADA'
        ? <><Palomita tam={18} /> {t(parcial ? 'reversa_parcial_acusada' : 'reversa_acusada')}</>
        : t('reversa_en_curso', { n: rev.intentos + 1 })}
    </p>
  )
}

function Resultado({ t, idioma, tituloRef, p, detalle, seguir, terminar }) {
  const propio = detalle && detalle.operacion?.id === p.resultado.tx_id
  const op = propio ? detalle.operacion : p.resultado
  const rev = propio ? detalle.reversa : null
  const { tipo, estado } = op
  const ingreso = tipo !== 'retiro'
  let titulo, cuerpo = null, monto = null, boton = t('ver_comprobante')

  if (estado === 'CONFIRMADA' && !ingreso) {
    titulo = t('toma_efectivo')
    monto = dinero(op.entregado)
    cuerpo = (
      <ul className="p-fajo" aria-label={mezclaTexto(op.mezcla)}>
        {Object.entries(op.mezcla || {}).sort((a, b) => b[0] - a[0]).map(([d, n]) => (
          <li key={d} className="p-fajo-billete"><span className="p-fajo-n">{n} ×</span> {pesos(d)}</li>
        ))}
      </ul>
    )
    boton = t('ya_lo_tome')
  } else if (!ingreso && (estado === 'PARCIAL' || (estado === 'EN_REVERSA' && op.entregado > 0))) {
    titulo = t('parcial_titulo', { entregado: dinero(op.entregado) })
    cuerpo = <><p className="p-sub">{t('parcial_texto', { monto: dinero(op.monto) })}</p><EstadoReversa t={t} rev={rev} parcial /></>
    boton = t('ya_lo_tome')
  } else if (estado === 'EN_REVERSA' || estado === 'REVERTIDA') {
    titulo = t(ingreso ? 'ingreso_fallo_titulo' : 'sin_entrega_titulo')
    cuerpo = (
      <>
        <p className="p-sub">{t(ingreso ? 'ingreso_fallo_texto' : 'sin_entrega_texto')}</p>
        {op.aclaracion > 0 && <p className="p-sub">{t('ingreso_retenido', { monto: dinero(op.aclaracion), folio: op.tx_id?.slice(0, 8) || op.id?.slice(0, 8) })}</p>}
        <EstadoReversa t={t} rev={rev} />
      </>
    )
  } else if (estado === 'CONFIRMADA') {
    const clave = { deposito: 'deposito_ok', pago_credito: 'pago_ok', remesa: 'remesa_ok' }[tipo]
    titulo = t(clave, { monto: dinero(op.monto), destino: op.destino })
    cuerpo = (
      <>
        {tipo === 'deposito' && op.saldo != null && <p className="p-sub">{t('nuevo_saldo', { saldo: dinero(op.saldo) })}</p>}
        {tipo === 'remesa' && <p className="p-clave">{t('clave_cobro', { clave: op.autorizacion })}</p>}
        {tipo !== 'remesa' && op.autorizacion && <p className="p-tenue">{t('autorizacion', { n: op.autorizacion })}</p>}
      </>
    )
  } else if (estado === 'RECHAZADA') {
    titulo = t('rechazada_titulo')
    cuerpo = (
      <>
        <p className="p-sub">{`${op.respuesta} · ${descRespuesta(op.respuesta, idioma)}`}</p>
        {ingreso && <p className="p-sub">{t('ingreso_fallo_texto')}</p>}
      </>
    )
  } else {
    titulo = t('cancelada_titulo')
    cuerpo = <p className="p-sub">{textoNucleo(op.motivo, idioma)}</p>
  }
  const hayComprobante = Boolean(op.enmascarado)
  return (
    <div className="p-cuerpo">
      <h3 className="p-titulo" tabIndex={-1} ref={tituloRef}>{titulo}</h3>
      {monto && <p className="p-monto">{monto}</p>}
      {cuerpo}
      <div className="p-acciones">
        {hayComprobante
          ? <button type="button" className="p-boton p-activo" onClick={seguir}>{boton}</button>
          : <button type="button" className="p-boton p-activo" onClick={terminar}>{t('terminar')}</button>}
      </div>
    </div>
  )
}

function Comprobante({ t, tituloRef, p, detalle, otra, terminar }) {
  const propio = detalle && detalle.operacion?.id === p.resultado?.tx_id
  const texto = propio ? detalle.comprobante : null
  return (
    <div className="p-cuerpo">
      <h3 className="p-titulo" tabIndex={-1} ref={tituloRef}>{t('comprobante')}</h3>
      {texto ? <pre className="p-ticket">{texto}</pre> : <p className="p-sub">{t('comprobante_no_impreso')}</p>}
      <div className="p-acciones">
        <button type="button" className="p-boton p-activo" onClick={otra}>{t('otra_operacion')}</button>
        <button type="button" className="p-boton" onClick={terminar}>{t('terminar')}</button>
      </div>
    </div>
  )
}

const ACCION = {
  'reversa total': 'accion_reversa_total',
  reversa: 'accion_reversa',
  confirmada: 'accion_confirmada',
  cancelada: 'accion_cancelada',
  'reversa ya encolada': 'accion_ya_encolada',
}

// La acción «reversa» puede ser total o parcial: lo dice lo que el reciclador entregó.
function accionLegible(t, a, detalle) {
  if (a.accion === 'reversa' && detalle?.operacion?.id === a.tx_id) {
    return t(detalle.operacion.entregado > 0 ? 'accion_reversa_parcial' : 'accion_reversa_total')
  }
  return t(ACCION[a.accion] || a.accion)
}

function Recuperado({ t, tituloRef, rec, detalle, seguir }) {
  return (
    <div className="p-cuerpo">
      <h3 className="p-titulo" tabIndex={-1} ref={tituloRef}>{t('recuperado_titulo')}</h3>
      {rec.acciones.length === 0 ? (
        <p className="p-sub">{t('recuperado_nada')}</p>
      ) : (
        <ul className="p-lista">
          {rec.acciones.map((a) => (
            <li key={a.tx_id}>{t('recuperado_accion', { estado: t(`estado_${a.estado_previo}`), accion: accionLegible(t, a, detalle) })}</li>
          ))}
        </ul>
      )}
      <div className="p-acciones">
        <button type="button" className="p-boton p-activo" onClick={seguir}>{t('continuar')}</button>
      </div>
    </div>
  )
}
