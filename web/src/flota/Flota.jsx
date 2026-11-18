// Zona 4 · la flota: corre el simulador agregado de N cajeros (hasta 15 000) en el servidor
// y la muestra de alta fidelidad con la máquina de estados real.
import { useState } from 'react'
import { useIdioma } from '../i18n.js'
import { api } from '../api.js'
import { compacto, dinero, fechaCorta, numero, porcentaje, segundos } from '../formato.js'

const FALLAS = ['atasco', 'entrega_parcial', 'tiempo_agotado', 'corte_energia', 'efectivo_no_retirado', 'enlace']

function Tile({ etiqueta, valor, detalle }) {
  return (
    <div className="tile">
      <dt>{etiqueta}</dt>
      <dd>
        <span className="tile-valor">{valor}</span>
        {detalle && <span className="tile-detalle">{detalle}</span>}
      </dd>
    </div>
  )
}

function tope(max) {
  // Un tope "redondo" para el eje con aire arriba para la etiqueta del máximo; su mitad también es redonda.
  const objetivo = Math.max(1, max * 1.12)
  const p = 10 ** Math.floor(Math.log10(objetivo))
  for (const m of [1, 1.2, 1.6, 2, 3, 4, 5, 6, 8, 10]) if (m * p >= objetivo) return m * p
  return 10 * p
}

function Reabasto({ r }) {
  const { t, idioma } = useIdioma()
  const dias = Object.entries(r.cajeros_por_dia_estimado).filter(([k]) => k !== 'despues_de_7_dias')
  const despues = r.cajeros_por_dia_estimado.despues_de_7_dias || 0
  const max = Math.max(0, ...dias.map(([, n]) => n))
  const y = tope(max)
  const iMax = dias.findIndex(([, n]) => n === max)
  return (
    <figure className="grafica">
      <figcaption>
        <span className="grafica-titulo">{t('reabasto_titulo')}</span>
        <span className="nota">{t('reabasto_sub', { umbral: dinero(r.umbral * 100) })}</span>
      </figcaption>
      {dias.length > 0 && (
        <div className="columnas" role="img" aria-label={dias.map(([d, n]) => t('reabasto_tooltip', { dia: fechaCorta(d, idioma), n: numero(n, idioma) })).join('; ')}>
          <div className="columnas-eje" aria-hidden="true">
            <span>{numero(y, idioma)}</span>
            <span>{numero(y / 2, idioma)}</span>
            <span>0</span>
          </div>
          <div className="columnas-area">
            <div className="columnas-rejilla" aria-hidden="true"><span /><span /><span /></div>
            {dias.map(([d, n], k) => (
              <div className="columna" key={d}>
                <div className="columna-barra-zona">
                  {k === iMax && n > 0 && <span className="columna-valor" style={{ bottom: `${(100 * n) / y}%` }}>{numero(n, idioma)}</span>}
                  <span className="columna-barra" style={{ height: `${(100 * n) / y}%` }} />
                  <span className="tooltip" aria-hidden="true" style={{ bottom: `${(100 * n) / y}%` }}>
                    {t('reabasto_tooltip', { dia: fechaCorta(d, idioma), n: numero(n, idioma) })}
                  </span>
                </div>
                <span className="columna-etiqueta">{fechaCorta(d, idioma)}</span>
              </div>
            ))}
          </div>
        </div>
      )}
      <p className="nota">{t('reabasto_despues', { n: numero(despues, idioma), sin: numero(r.sin_necesidad_estimada, idioma) })}</p>
      <details className="tabla-vista">
        <summary>{t('ver_tabla')}</summary>
        <table className="tabla">
          <thead><tr><th scope="col">{t('dia')}</th><th scope="col" className="num">{t('cajeros_col')}</th></tr></thead>
          <tbody>
            {dias.map(([d, n]) => <tr key={d}><th scope="row">{d}</th><td className="num">{numero(n, idioma)}</td></tr>)}
            <tr><th scope="row">{'> 7'}</th><td className="num">{numero(despues, idioma)}</td></tr>
          </tbody>
        </table>
      </details>
    </figure>
  )
}

function Resultado({ m, ida }) {
  const { t, idioma } = useIdioma()
  const fallasDispositivo = FALLAS.filter((f) => f !== 'enlace').reduce((s, f) => s + (m.fallas[f] || 0), 0)
  const totalFallas = fallasDispositivo + (m.fallas.enlace || 0)
  return (
    <div className="flota-resultado" role="status" aria-live="polite">
      <dl className="tiles">
        <Tile
          etiqueta={t('kpi_disponibilidad')}
          valor={porcentaje(m.disponibilidad.flota, idioma)}
          detalle={t('kpi_disponibilidad_d', { p5: porcentaje(m.disponibilidad.p5, idioma, 1), n: numero(m.disponibilidad.cajeros_99_o_mas, idioma) })}
        />
        <Tile
          etiqueta={t('kpi_operaciones')}
          valor={compacto(m.operaciones.atendidas, idioma)}
          detalle={t('kpi_operaciones_d', { n: numero(m.operaciones.por_cajero_por_dia, idioma) })}
        />
        <Tile
          etiqueta={t('kpi_fallas')}
          valor={numero(totalFallas, idioma)}
          detalle={t('kpi_fallas_d', { dispositivo: numero(fallasDispositivo, idioma), enlace: numero(m.fallas.enlace || 0, idioma) })}
        />
        <Tile
          etiqueta={t('kpi_reversas')}
          valor={numero(m.reversas.total, idioma)}
          detalle={t('kpi_reversas_d', { parciales: numero(m.reversas.parciales, idioma) })}
        />
        <Tile
          etiqueta={t('kpi_tiempo')}
          valor={segundos(m.rendimiento.segundos, idioma)}
          detalle={t('kpi_tiempo_d', { ida: segundos(ida, idioma), cps: compacto(m.rendimiento.cajero_horas_por_segundo || 0, idioma) })}
        />
      </dl>
      <div className="flota-columnas">
        <Reabasto r={m.reabasto} />
        <div>
          <h3 className="bloque-titulo">{t('fallas_tipo')}</h3>
          <table className="tabla">
            <tbody>
              {FALLAS.map((f) => (
                <tr key={f}><th scope="row">{t(`ff_${f}`)}</th><td className="num">{numero(m.fallas[f] || 0, idioma)}</td></tr>
              ))}
            </tbody>
          </table>
          <h3 className="bloque-titulo">{t('urgentes')}</h3>
          <table className="tabla">
            <thead>
              <tr><th scope="col">{t('cajero_col')}</th><th scope="col" className="num">{t('efectivo_col')}</th><th scope="col" className="num">{t('dias_col')}</th></tr>
            </thead>
            <tbody>
              {m.reabasto.mas_urgentes.slice(0, 5).map((u) => (
                <tr key={u.cajero}>
                  <th scope="row"><code>{u.cajero}</code></th>
                  <td className="num">{`$${numero(u.efectivo, idioma)}`}</td>
                  <td className="num">{numero(u.dias_restantes, idioma, { maximumFractionDigits: 1 })}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

export default function Flota() {
  const { t, idioma } = useIdioma()
  const [cajeros, setCajeros] = useState('15000')
  const [horas, setHoras] = useState('24')
  const [res, setRes] = useState(null)
  const [ida, setIda] = useState(0)
  const [corriendo, setCorriendo] = useState(null)
  const [error, setError] = useState('')
  const [muestra, setMuestra] = useState(null)

  const n = Number(cajeros)
  const valido = Number.isInteger(n) && n >= 1 && n <= 15000

  const simular = async (e) => {
    e.preventDefault()
    if (!valido) return setError(t('flota_rango'))
    setCorriendo('flota')
    setError('')
    const t0 = performance.now()
    try {
      const r = await api.post('/flota/simulacion', { cajeros: n, horas: Number(horas) })
      setIda((performance.now() - t0) / 1000)
      setRes(r)
    } catch (err) {
      setError(typeof err.detalle === 'string' ? err.detalle : err.message)
    } finally {
      setCorriendo(null)
    }
  }

  const correrMuestra = async () => {
    setCorriendo('muestra')
    try {
      setMuestra(await api.post('/flota/muestra', { cajeros: 20, operaciones_por_cajero: 15 }))
    } catch (err) {
      setError(typeof err.detalle === 'string' ? err.detalle : err.message)
    } finally {
      setCorriendo(null)
    }
  }

  return (
    <section className="zona zona-flota" aria-labelledby="titulo-flota">
      <h2 className="zona-titulo" id="titulo-flota"><span className="zona-num">4</span>{t('zona_flota')}</h2>
      <p className="zona-intro">{t('flota_intro')}</p>
      <form className="flota-form" onSubmit={simular} noValidate>
        <div className="campo">
          <label className="campo-etiqueta" htmlFor="flota-cajeros">{t('flota_cajeros')}</label>
          <input
            id="flota-cajeros" className="entrada" inputMode="numeric" value={cajeros}
            onChange={(e) => { setCajeros(e.target.value.replace(/[^0-9]/g, '')); setError('') }}
            aria-describedby="flota-ayuda" aria-invalid={!valido || undefined}
          />
        </div>
        <div className="campo">
          <label className="campo-etiqueta" htmlFor="flota-horas">{t('flota_horas')}</label>
          <select id="flota-horas" className="selector" value={horas} onChange={(e) => setHoras(e.target.value)}>
            <option value="24">{t('horas_24')}</option>
            <option value="72">{t('horas_72')}</option>
            <option value="168">{t('horas_168')}</option>
          </select>
        </div>
        <button type="submit" className="boton boton-primario" disabled={corriendo !== null}>
          {corriendo === 'flota' ? t('corriendo') : t('correr_simulacion')}
        </button>
        <p id="flota-ayuda" className={error ? 'nota nota-alerta' : 'nota'} role={error ? 'alert' : undefined}>{error || t('flota_rango')}</p>
      </form>
      {res && <Resultado m={res} ida={ida} />}

      <div className="bloque muestra">
        <h3 className="bloque-titulo">{t('muestra_titulo')}</h3>
        <p className="nota">{t('muestra_texto')}</p>
        <button type="button" className="boton" onClick={correrMuestra} disabled={corriendo !== null}>
          {corriendo === 'muestra' ? t('corriendo') : t('correr_muestra')}
        </button>
        {muestra && (
          <p className="muestra-resultado" role="status">
            {t('muestra_resultado', {
              cuadran: muestra.cajeros_que_cuadran,
              cajeros: muestra.cajeros,
              ops: numero(muestra.operaciones, idioma),
              reinicios: muestra.reinicios_por_corte,
              reversas: muestra.reversas.acusadas,
              libro: t(muestra.libro_balanceado ? 'si_balanceado' : 'no_balanceado'),
              saldos: t(muestra.saldos_de_clientes_correctos ? 'exactos' : 'con_error'),
              seg: segundos(muestra.segundos, idioma),
            })}
          </p>
        )}
      </div>
    </section>
  )
}
