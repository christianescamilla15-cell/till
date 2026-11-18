// Íconos en SVG en línea (la tipografía subconjunto no trae flechas ni palomitas).
// Decorativos por omisión: el texto de al lado dice lo mismo.

function Svg({ children, tam = 18, titulo, clase = '', ...resto }) {
  return (
    <svg
      className={`icono ${clase}`}
      width={tam}
      height={tam}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden={titulo ? undefined : true}
      role={titulo ? 'img' : undefined}
      aria-label={titulo}
      focusable="false"
      {...resto}
    >
      {children}
    </svg>
  )
}

export const Palomita = (p) => <Svg {...p}><path d="M4 12.5l5 5L20 6.5" /></Svg>
export const Tache = (p) => <Svg {...p}><path d="M6 6l12 12M18 6L6 18" /></Svg>
export const Flecha = (p) => <Svg {...p}><path d="M4 12h15M13 6l6 6-6 6" /></Svg>
export const FlechaIzq = (p) => <Svg {...p}><path d="M20 12H5M11 6l-6 6 6 6" /></Svg>
export const Chevron = (p) => <Svg {...p}><path d="M9 5l7 7-7 7" /></Svg>
export const Reloj = (p) => <Svg {...p}><circle cx="12" cy="12" r="8.5" /><path d="M12 7.5V12l3 2" /></Svg>
export const Energia = (p) => <Svg {...p}><path d="M12 3v8" /><path d="M6.3 6.8a8 8 0 1 0 11.4 0" /></Svg>
export const Reiniciar = (p) => <Svg {...p}><path d="M20 11a8 8 0 1 0-2.3 5.7" /><path d="M20 4v7h-7" /></Svg>
export const Alerta = (p) => <Svg {...p}><path d="M12 4l9 16H3z" /><path d="M12 10v4M12 17.2v.1" /></Svg>
export const Sin = (p) => <Svg {...p}><path d="M6 12h12" /></Svg>
export const Mas = (p) => <Svg {...p}><path d="M12 6v12M6 12h12" /></Svg>
export const Menos = (p) => <Svg {...p}><path d="M6 12h12" /></Svg>
export const Tarjeta = (p) => (
  <Svg {...p}>
    <path d="M8.5 7.5a6 6 0 0 1 0 9" />
    <path d="M12 5a9.5 9.5 0 0 1 0 14" />
    <path d="M15.5 2.8a13 13 0 0 1 0 18.4" />
    <circle cx="5" cy="12" r="1.2" fill="currentColor" stroke="none" />
  </Svg>
)
export const Microfono = (p) => (
  <Svg {...p}>
    <rect x="9" y="3" width="6" height="11" rx="3" />
    <path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21" />
  </Svg>
)
