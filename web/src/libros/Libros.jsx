// Zona 3 · los libros: lo que dice el banco, lo que hay en el cajero, lo que registra el libro
// mayor de partida doble y el cuadre de fin de día a tres bandas.
import { useEffect, useRef, useState } from 'react'
import { useIdioma } from '../i18n.js'
import { cuentaLegible, dinero, pesos, textoNucleo } from '../formato.js'
import { Alerta, Palomita, Reloj, Tache } from '../iconos.jsx'

const DENOMINACIONES = [1000, 500, 200, 100, 50, 20]
const UBICACIONES = ['rechazo', 'retenido', 'excedente']

// Recuerda el valor anterior para mostrar el cambio que acaba de pasar (comunica estado).
// ``epoca`` cambia al empezar cada operación: el cambio anterior deja de mostrarse.
function useCambio(valor, clave, epoca) {
  const previo = useRef({ clave, valor })
  const [delta, setDelta] = useState(0)
  useEffect(() => setDelta(0), [epoca])
  useEffect(() => {
    if (valor === undefined) return
    if (previo.current.clave === clave && previo.current.valor !== undefined && previo.current.valor !== valor) {
      setDelta(valor - previo.current.valor)
    } else if (previo.current.clave !== clave) {
      setDelta(0)
    }
    previo.current = { clave, valor }
  }, [valor, clave])
  return delta
}

// Débitos primero y de mayor a menor: el orden de las líneas no cambia el asiento, sólo lo hace legible.
function ordenar(movimientos) {
  return [...movimientos].sort((a, b) => (a.monto < 0) - (b.monto < 0) || Math.abs(b.monto) - Math.abs(a.monto))
}

// La referencia de un asiento es una operación (32 hexadecimales) o, en el reabasto inicial, la terminal.
function referencia(t, r) {
  return /^[0-9a-f]{32}$/.test(r) ? t('op_corta', { id: r.slice(0, 8) }) : t('ref_terminal', { id: r })
}

function Cifra({ etiqueta, valor, delta }) {
  const { t } = useIdioma()
  return (
    <div className="cifra">
      <dt>{etiqueta}</dt>
      <dd>
        <span className="cifra-valor">{dinero(valor)}</span>
        {delta !== 0 && (
          <span className={`cifra-delta ${delta > 0 ? 'sube' : 'baja'}`}>
            {t('cambio', { delta: `${delta > 0 ? '+' : ''}${dinero(delta)}` })}
          </span>
        )}
      </dd>
    </div>
  )
}

function Estado({ estado }) {
  const { t } = useIdioma()
  const Icono = { CUADRA: Palomita, PRELIMINAR: Reloj, DIFERENCIAS: Alerta }[estado] || Alerta
  return (
    <span className={`insignia insignia-${estado.toLowerCase()}`}>
      <Icono tam={16} /> {t(`estado_${estado}`)}
    </span>
  )
}

function Cuadre({ c }) {
  const { t, idioma } = useIdioma()
  if (c.error) return <p className="nota nota-alerta" role="alert">{t('cuadre_fuera')}</p>
  const tipos = { retiro: 'tipo_retiro', deposito: 'tipo_deposito', pago_credito: 'tipo_pago_credito', remesa: 'tipo_remesa' }
  return (
    <div className="cuadre" role="status">
      <p className="cuadre-estado"><Estado estado={c.estado} /></p>
      <div className="banda">
        <h4>{t('banda1')}</h4>
        <p className="nota">{t('banda1_d')}</p>
        <dl className="banda-cifras">
          <div><dt>{t('fisico')}</dt><dd>{dinero(c.efectivo.fisico)}</dd></div>
          <div><dt>{t('libro')}</dt><dd>{dinero(c.efectivo.libro)}</dd></div>
          <div><dt>{t('diferencia')}</dt><dd className={c.efectivo.diferencia ? 'dif-mal' : 'dif-ok'}>{dinero(c.efectivo.diferencia)}</dd></div>
        </dl>
      </div>
      <div className="banda">
        <h4>{t('banda2')}</h4>
        <p className="nota">{t('banda2_d')}</p>
        <table className="tabla">
          <thead>
            <tr><th scope="col">{t('conf_operacion')}</th><th scope="col" className="num">{t('libro')}</th><th scope="col" className="num">{t('host')}</th><th scope="col" className="num">{t('diferencia')}</th></tr>
          </thead>
          <tbody>
            {c.host.map((x) => (
              <tr key={x.tipo}>
                <th scope="row">{t(tipos[x.tipo])}</th>
                <td className="num">{dinero(x.libro)}</td>
                <td className="num">{dinero(x.host)}</td>
                <td className={`num ${x.diferencia ? (x.explicable_por_reversas ? 'dif-aviso' : 'dif-mal') : ''}`}>{dinero(x.diferencia)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="banda">
        <h4>{t('banda3')}</h4>
        <p className="nota">{t('banda3_d')}</p>
        <dl className="banda-cifras">
          <div><dt>{t('reversas_sin_acuse')}</dt><dd>{c.reversas_pendientes.length}</dd></div>
          <div><dt>{t('transito')}</dt><dd>{dinero(c.transito)}</dd></div>
          <div><dt>{t('aclaraciones')}</dt><dd>{dinero(c.aclaraciones.saldo)}</dd></div>
        </dl>
      </div>
      <h4>{t('observaciones')}</h4>
      {c.observaciones.length === 0 ? (
        <p className="nota">{t('sin_observaciones')}</p>
      ) : (
        <ul className="lista">{c.observaciones.map((o) => <li key={o}>{textoNucleo(o, idioma)}</li>)}</ul>
      )}
    </div>
  )
}

export default function Libros({ tablero, cuentaCliente, cuadre, cuadrar, ocupado, apagado, epoca }) {
  const { t, idioma } = useIdioma()
  const banco = tablero?.banco
  const saldo = banco?.cuentas?.[cuentaCliente]
  const creditoId = banco ? Object.keys(banco.creditos)[0] : null
  const adeudo = creditoId ? banco.creditos[creditoId] : undefined
  const deltaSaldo = useCambio(saldo, cuentaCliente, epoca)
  const deltaAdeudo = useCambio(adeudo, creditoId, epoca)
  const ef = tablero?.efectivo
  const deltaEfectivo = useCambio(ef?.total, 'efectivo', epoca)
  const verif = tablero?.libro.verificacion
  const asientos = tablero?.libro.asientos || []

  return (
    <section className="zona zona-libros" aria-labelledby="titulo-libros">
      <h2 className="zona-titulo" id="titulo-libros"><span className="zona-num">3</span>{t('zona_libros')}</h2>
      <p className="zona-intro">{t('libros_intro')}</p>

      <div className="bloque">
        <h3 className="bloque-titulo">{t('banco')}</h3>
        {banco && (
          <dl className="cifras">
            <Cifra etiqueta={`${t('saldo_cliente')} · ${cuentaCliente}`} valor={saldo ?? 0} delta={deltaSaldo} />
            {creditoId && <Cifra etiqueta={t('adeudo_credito', { credito: creditoId })} valor={adeudo} delta={deltaAdeudo} />}
          </dl>
        )}
      </div>

      <div className="bloque">
        <h3 className="bloque-titulo">{t('efectivo_cajero')}</h3>
        {ef && (
          <>
            <table className="tabla tabla-casetes">
              <thead>
                <tr>
                  <th scope="col">{t('casete')}</th>
                  <th scope="col" className="num">{t('billetes')}</th>
                  <th scope="col" className="num">{t('valor_col')}</th>
                  <th scope="col" aria-hidden="true" />
                </tr>
              </thead>
              <tbody>
                {DENOMINACIONES.map((d) => {
                  const n = ef.casetes[d] ?? 0
                  return (
                    <tr key={d}>
                      <th scope="row">{pesos(d)}</th>
                      <td className="num">{n.toLocaleString('en-US')}</td>
                      <td className="num">{dinero(n * d * 100)}</td>
                      <td className="nivel-celda" aria-hidden="true">
                        <span className="nivel"><span className="nivel-lleno" style={{ width: `${Math.min(100, (100 * n) / ef.capacidad_por_casete)}%` }} /></span>
                      </td>
                    </tr>
                  )
                })}
                {UBICACIONES.map((u) => {
                  const m = ef[u] || {}
                  const n = Object.values(m).reduce((s, x) => s + x, 0)
                  if (!n) return null
                  return (
                    <tr key={u} className="fila-bolsa">
                      <th scope="row">{t(`ubic_${u}`)}</th>
                      <td className="num">{n}</td>
                      <td className="num">{dinero(Object.entries(m).reduce((s, [d, x]) => s + Number(d) * 100 * x, 0))}</td>
                      <td />
                    </tr>
                  )
                })}
              </tbody>
              <tfoot>
                <tr>
                  <th scope="row" colSpan={2}>{t('total_fisico')}</th>
                  <td className="num">
                    {dinero(ef.total)}
                    {deltaEfectivo !== 0 && <span className={`cifra-delta ${deltaEfectivo > 0 ? 'sube' : 'baja'}`}>{` ${deltaEfectivo > 0 ? '+' : ''}${dinero(deltaEfectivo)}`}</span>}
                  </td>
                  <td />
                </tr>
              </tfoot>
            </table>
          </>
        )}
      </div>

      <div className="bloque">
        <h3 className="bloque-titulo">{t('libro_mayor')}</h3>
        {verif && (
          <>
            <p className="libro-estado">
              <span className={`insignia ${verif.balanceado ? 'insignia-cuadra' : 'insignia-diferencias'}`}>
                {verif.balanceado ? <Palomita tam={16} /> : <Tache tam={16} />} {t(verif.balanceado ? 'balanceado_si' : 'balanceado_no')}
              </span>
              <span className="nota">{t('libro_resumen', { suma: dinero(verif.suma_total), asientos: verif.asientos, movimientos: verif.movimientos })}</span>
            </p>
            <table className="tabla tabla-libro">
              <caption>{t('asientos_recientes')}</caption>
              <thead>
                <tr><th scope="col">{t('cuenta')}</th><th scope="col" className="num">{t('debe')}</th><th scope="col" className="num">{t('haber')}</th></tr>
              </thead>
              {asientos.map((a) => (
                <tbody key={a.id}>
                  <tr className="grupo">
                    <th scope="colgroup" colSpan={3}>
                      {t('asiento', { id: a.id, tipo: t(`asiento_${a.tipo}`) })}
                      {a.referencia && <span className="tenue">{referencia(t, a.referencia)}</span>}
                    </th>
                  </tr>
                  {ordenar(a.movimientos).map((m, k) => (
                    <tr key={k}>
                      <th scope="row" title={m.cuenta}>{cuentaLegible(m.cuenta, idioma)}</th>
                      <td className="num">{m.monto > 0 ? dinero(m.monto) : ''}</td>
                      <td className="num">{m.monto < 0 ? dinero(-m.monto) : ''}</td>
                    </tr>
                  ))}
                </tbody>
              ))}
            </table>
          </>
        )}
      </div>

      <div className="bloque">
        <button type="button" className="boton boton-primario" onClick={cuadrar} disabled={ocupado === 'cuadrar' || apagado || !tablero}>
          {ocupado === 'cuadrar' ? t('cuadrando') : t('cuadrar')}
        </button>
        {apagado && !cuadre && <p className="nota">{t('cuadre_fuera')}</p>}
        {cuadre && <Cuadre c={cuadre} />}
      </div>
    </section>
  )
}
