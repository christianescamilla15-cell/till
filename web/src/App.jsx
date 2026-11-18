// La demo: cuatro zonas sobre la API real de Till (cajero, taller de fallas, libros y flota).
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, idAleatorio, nuevaSesion } from './api.js'
import { Idioma, IDIOMAS, idiomaInicial, traductor } from './i18n.js'
import Cajero from './cajero/Cajero.jsx'
import Taller from './taller/Taller.jsx'
import Libros from './libros/Libros.jsx'
import Flota from './flota/Flota.jsx'
import { FALLAS } from './taller/fallas.js'
import { Alerta, Reiniciar } from './iconos.jsx'

const TERMINAL = 'TILL0001'
const ESPERA_REVERSAS = 1000 // ms entre reintentos pedidos por la UI mientras haya reversas pendientes

export default function App() {
  const [idioma, setIdioma] = useState(idiomaInicial)
  const t = useMemo(() => traductor(idioma), [idioma])
  const [catalogo, setCatalogo] = useState(null)
  const [tablero, setTablero] = useState(null)
  const [detalle, setDetalle] = useState(null)
  const [recuperacion, setRecuperacion] = useState(null)
  const [cuadre, setCuadre] = useState(null)
  const [tarjetaSel, setTarjetaSel] = useState(0)
  const [ocupado, setOcupado] = useState(null)
  const [error, setError] = useState(null)
  const [aviso, setAviso] = useState(false)
  const [reinicioClave, setReinicioClave] = useState(0)
  const [epoca, setEpoca] = useState(0) // sube al empezar cada operación (los «cambios» de los libros se cuentan desde ahí)
  const txRef = useRef(null)
  const instanciaRef = useRef(null)

  useEffect(() => {
    document.documentElement.lang = idioma
    document.title = t('titulo_documento')
    try {
      localStorage.setItem('till-idioma', idioma)
    } catch {
      // sin almacenamiento: el idioma vale sólo para esta visita
    }
  }, [idioma, t])

  const limpiarVista = () => {
    txRef.current = null
    setDetalle(null)
    setCuadre(null)
    setRecuperacion(null)
    setReinicioClave((k) => k + 1)
  }

  const refrescar = useCallback(async (tx = txRef.current) => {
    try {
      const [tab, det] = await Promise.all([
        api.get(`/cajeros/${TERMINAL}/tablero`),
        tx ? api.get(`/operaciones/${tx}`).catch((e) => (e.status === 404 ? null : Promise.reject(e))) : null,
      ])
      if (instanciaRef.current && tab.instancia !== instanciaRef.current) {
        // Otra instancia del servidor (en Vercel puede pasar): su red empezó de cero.
        setAviso(true)
        limpiarVista()
      } else if (tx && tx === txRef.current) {
        setDetalle(det)
      }
      instanciaRef.current = tab.instancia
      setTablero(tab)
      setError(null)
      return tab
    } catch (e) {
      setError(e)
      return null
    }
  }, [])

  const cargar = useCallback(async () => {
    try {
      setCatalogo(await api.get('/catalogo'))
      await refrescar()
    } catch (e) {
      setError(e)
    }
  }, [refrescar])

  useEffect(() => {
    cargar()
  }, [cargar])

  // Mientras haya reversas pendientes, la UI pide un reintento por segundo (respetando la espera
  // exponencial del núcleo) y vuelve a pintar: así se ve la 0421 repetirse hasta el 0430, aunque
  // la plataforma congele el ciclo de fondo del servidor entre solicitudes.
  const hayPendientes = (tablero?.reversas_pendientes?.length || 0) > 0
  useEffect(() => {
    if (!hayPendientes) return undefined
    let vivo = true
    let id
    const tick = async () => {
      try {
        await api.post('/reversas/drenar?respetar_espera=true')
      } catch {
        // el siguiente refresco muestra el error si la API no responde
      }
      if (!vivo) return
      await refrescar()
      if (vivo) id = setTimeout(tick, ESPERA_REVERSAS)
    }
    id = setTimeout(tick, ESPERA_REVERSAS)
    return () => {
      vivo = false
      clearTimeout(id)
    }
  }, [hayPendientes, refrescar])

  const operar = async (cuerpo) => {
    setOcupado('operar')
    setEpoca((e) => e + 1)
    setCuadre(null)
    setRecuperacion(null)
    try {
      const r = await api.post(`/cajeros/${TERMINAL}/operaciones`, cuerpo, { 'Idempotency-Key': 'op-' + idAleatorio() })
      txRef.current = r.tx_id
      await refrescar(r.tx_id)
      return { resultado: r }
    } catch (e) {
      if (e.status === 503 && e.detalle && typeof e.detalle === 'object' && e.detalle.tx_id) {
        txRef.current = e.detalle.tx_id
        await refrescar(e.detalle.tx_id)
        return { corte: true }
      }
      if (e.status === 503) {
        await refrescar()
        return { fuera: true }
      }
      if (e.status === 0) setError(e)
      return { error: e }
    } finally {
      setOcupado(null)
    }
  }

  const armar = async (clave) => {
    setOcupado('armar')
    try {
      await api.del(`/cajeros/${TERMINAL}/fallas`)
      await api.del('/enlace/fallas')
      const f = FALLAS.find((x) => x.clave === clave)
      if (f?.dispositivo) await api.post(`/cajeros/${TERMINAL}/fallas`, f.dispositivo)
      if (f?.enlace) await api.post('/enlace/fallas', f.enlace)
      await refrescar()
    } catch (e) {
      setError(e)
    } finally {
      setOcupado(null)
    }
  }

  const reiniciar = async () => {
    setOcupado('reiniciar')
    try {
      const r = await api.post(`/cajeros/${TERMINAL}/reiniciar`)
      const tx = r.acciones[0]?.tx_id || txRef.current
      txRef.current = tx
      setCuadre(null)
      setRecuperacion(r)
      await refrescar(tx)
    } catch (e) {
      setError(e)
    } finally {
      setOcupado(null)
    }
  }

  const cuadrar = async () => {
    setOcupado('cuadrar')
    try {
      setCuadre(await api.get(`/cajeros/${TERMINAL}/cuadre`))
      await refrescar()
    } catch (e) {
      if (e.status === 503) setCuadre({ error: 'fuera' })
      else setError(e)
    } finally {
      setOcupado(null)
    }
  }

  const empezarDeNuevo = async () => {
    nuevaSesion()
    instanciaRef.current = null
    setAviso(false)
    setTablero(null)
    limpiarVista()
    await cargar()
  }

  const apagado = tablero ? !tablero.en_servicio : false
  const tarjetas = catalogo?.tarjetas_de_prueba || []
  const cuentaCliente = tarjetas[tarjetaSel]?.cuenta || 'CTA-0001'

  return (
    <Idioma.Provider value={{ idioma, t }}>
      <div className="demo">
        <a className="saltar" href="#cajero">{t('saltar')}</a>
        <header className="barra">
          <p className="marca">Till <span className="marca-sub">· {t('marca_sub')}</span></p>
          <div className="barra-acciones">
            <div className="idiomas" role="group" aria-label={t('idioma')}>
              {IDIOMAS.map((i) => (
                <button key={i} type="button" className="idioma" aria-pressed={idioma === i} lang={i} onClick={() => setIdioma(i)}>
                  {i.toUpperCase()}
                </button>
              ))}
            </div>
            <button type="button" className="boton boton-chico" onClick={empezarDeNuevo} title={t('empezar_de_nuevo_ayuda')}>
              <Reiniciar tam={16} /> {t('empezar_de_nuevo')}
            </button>
          </div>
        </header>

        <main>
          <section className="intro">
            <p className="bajada">{t('intro_bajada')}</p>
            <h1>{t('intro_titulo')}</h1>
            <p className="intro-texto">{t('intro_texto')}</p>
            <p className="intro-regla">{t('intro_regla')}</p>
          </section>

          {aviso && (
            <div className="aviso" role="status">
              <p>{t('aviso_instancia')}</p>
              <button type="button" className="boton boton-chico" onClick={() => setAviso(false)}>{t('cerrar')}</button>
            </div>
          )}
          {error && (
            <div className="aviso aviso-error" role="alert">
              <p><Alerta tam={18} /> {t('error_api', { detalle: typeof error.detalle === 'string' ? error.detalle : error.message })}</p>
              <button type="button" className="boton boton-chico" onClick={cargar}>{t('reintentar')}</button>
            </div>
          )}

          <div className="zonas">
            <Cajero
              catalogo={catalogo}
              tablero={tablero}
              detalle={detalle}
              operar={operar}
              recuperacion={recuperacion}
              apagado={apagado}
              reinicioClave={reinicioClave}
              ocupado={ocupado}
              tarjetaSel={tarjetaSel}
              setTarjetaSel={setTarjetaSel}
            />
            <Taller
              tablero={tablero}
              detalle={detalle}
              armar={armar}
              reiniciar={reiniciar}
              ocupado={ocupado}
              recuperacion={recuperacion}
              apagado={apagado}
            />
            <Libros
              tablero={tablero}
              cuentaCliente={cuentaCliente}
              cuadre={cuadre}
              cuadrar={cuadrar}
              ocupado={ocupado}
              apagado={apagado}
              epoca={epoca}
            />
          </div>
          <Flota />
        </main>

        <footer className="pie">
          <p>{t('pie_simulado')}</p>
          <p>{t('pie_autor')} <a href="/api/openapi.json">{t('pie_api')}</a></p>
        </footer>
      </div>
    </Idioma.Provider>
  )
}
