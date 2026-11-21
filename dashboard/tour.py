"""Recorrido guiado de bienvenida (driver.js 1.10.2, licencia MIT).

Se muestra cada vez que se carga o recarga la página (no al aplicar filtros: la ventana guarda
una marca mientras está abierta) y se puede repetir con el botón "Ver guía". Cada paso resalta
un contenedor de Streamlit identificado con `st.container(key=...)`, que Streamlit expone como
`.st-key-<key>`.
"""

from __future__ import annotations

import json
from pathlib import Path

DRIVER = "https://cdn.jsdelivr.net/npm/driver.js@1.10.2/dist"

STEPS = [
    {
        "popover": {
            "title": "Bienvenido al centro de control 🛰️",
            "description": (
                "Telemetría de 20 vehículos en Bogotá, Medellín y el corredor Ruta 60, leída "
                "de la capa Oro de PostGIS. En 1 minuto te muestro cómo usarlo."
            ),
        }
    },
    {
        "element": ".st-key-tour-role",
        "popover": {
            "title": "1 · Elige tu rol",
            "description": (
                "<b>Analista</b> ve los IDs enmascarados (VHC-****-ABC). <b>Administrador</b> ve "
                "los IDs reales. La regla la aplica PostgreSQL con <code>SET ROLE</code>: no es un "
                "filtro visual, la base se niega a entregar el dato."
            ),
            "side": "right",
        },
    },
    {
        "element": ".st-key-tour-period",
        "popover": {
            "title": "2 · Periodo y ciudad",
            "description": (
                "Rango de días y ciudades (Bogotá, Medellín y el corredor). Todo el tablero "
                "responde: KPIs, mapa, ranking y alertas."
            ),
            "side": "right",
        },
    },
    {
        "element": ".st-key-tour-vehicle",
        "popover": {
            "title": "3 · Vehículo",
            "description": (
                "Con <b>Todos los vehículos</b> ves la flota completa en movimiento. 💡 Elige "
                "uno para ver su ruta en detalle: tramos por severidad, flechas de sentido y una "
                "estela animada."
            ),
            "side": "right",
        },
    },
    {
        "element": ".st-key-tour-filters",
        "popover": {
            "title": "4 · Infracciones",
            "description": (
                "Combina tipos (excesos por tipo de vía y geocercas escolar, alta siniestralidad "
                "o vía deteriorada) y severidad. ⛽ <b>Combustible insuficiente</b>: camiones del "
                "corredor cuyo combustible no alcanza para terminar la ruta. Pulsa <b>Aplicar "
                "filtros</b>: todo se recalcula de una sola vez."
            ),
            "side": "right",
        },
    },
    {
        "element": ".st-key-tour-kpis",
        "popover": {
            "title": "5 · Indicadores",
            "description": (
                "Resumen del periodo. El Driver Score de la flota pondera las infracciones por "
                "cada 100 km recorridos."
            ),
            "side": "bottom",
        },
    },
    {
        "element": ".st-key-tour-map",
        "popover": {
            "title": "6 · Mapa",
            "description": (
                "Geocercas achuradas, recorridos y heatmap de infracciones. El mapa se encuadra "
                "solo en lo filtrado. Las capas se encienden arriba a la derecha, al instante. "
                "Pasa el mouse para ver detalles."
            ),
            "side": "top",
        },
    },
    {
        "element": ".st-key-tour-ranking",
        "popover": {
            "title": "7 · Driver Score",
            "description": "Primero los conductores de mayor riesgo, de 0 a 100.",
            "side": "top",
        },
    },
    {
        "element": ".st-key-tour-alerts",
        "popover": {
            "title": "8 · Alertas recientes",
            "description": (
                "Cada fila es un episodio sostenido (no una lectura suelta), con su severidad."
            ),
            "side": "top",
        },
    },
    {
        "element": ".st-key-tour-timings",
        "popover": {
            "title": "9 · Rendimiento",
            "description": (
                "Milisegundos reales de cada consulta a la capa Oro: tablas particionadas e "
                "indexadas con GiST y BRIN."
            ),
            "side": "top",
        },
    },
]

TOUR_CSS = Path(__file__).with_name("tour.css").read_text(encoding="utf-8")

_TEMPLATE = """<!doctype html><html><body><script>
(function () {
  const P = window.parent, D = P.document;
  const FORCE = __FORCE__, STEPS = __STEPS__, CSS = __CSS__;
  // La marca vive en la ventana: se borra al recargar la página, no al aplicar filtros.
  if (!FORCE && P.__fleetTourShown) return;
  P.__fleetTourShown = true;

  function load(done) {
    if (P.driver && P.driver.js) return done();
    const style = D.createElement('style'); style.textContent = CSS; D.head.appendChild(style);
    const css = D.createElement('link'); css.rel = 'stylesheet'; css.href = '__DRIVER__/driver.css';
    D.head.appendChild(css);
    const js = D.createElement('script'); js.src = '__DRIVER__/driver.js.iife.js'; js.onload = done;
    D.head.appendChild(js);
  }
  // Sugerencia junto a los filtros de día y vehículo, que se iluminan mientras está visible.
  function showTip() {
    D.querySelectorAll('.fleet-tip').forEach(el => el.remove());
    const targets = ['.st-key-tour-period', '.st-key-tour-vehicle']
      .map(sel => D.querySelector(sel)).filter(Boolean);
    const vehicle = D.querySelector('.st-key-tour-vehicle');
    if (vehicle) vehicle.scrollIntoView({behavior: 'smooth', block: 'center'});
    targets.forEach(el => el.classList.add('fleet-attention'));

    const tip = D.createElement('div');
    tip.className = 'fleet-tip';
    tip.innerHTML = '<span class="icon">💡</span><div><b>Consejo</b>Elige un <b>día</b> y un '
      + '<b>vehículo</b> aquí y pulsa <b>Aplicar filtros</b>: verás su ruta con flechas de '
      + 'sentido y los tramos coloreados por severidad.</div>'
      + '<button aria-label="Cerrar">×</button>';
    const close = () => {
      tip.classList.add('out');
      targets.forEach(el => el.classList.remove('fleet-attention'));
      setTimeout(() => tip.remove(), 500);
    };
    tip.querySelector('button').onclick = close;
    D.body.appendChild(tip);
    // Se ubica a la derecha de la sección de vehículo, cuando el desplazamiento terminó.
    setTimeout(() => {
      const box = (vehicle || targets[0] || D.body).getBoundingClientRect();
      tip.style.left = `${Math.round(box.right + 16)}px`;
      const top = box.top + box.height / 2 - tip.offsetHeight / 2;
      tip.style.top = `${Math.max(16, Math.round(top))}px`;
      tip.classList.add('in');
    }, 650);
    setTimeout(close, 12000);
  }
  function start() {
    const steps = STEPS.filter(s => !s.element || D.querySelector(s.element));
    const tour = P.driver.js.driver({
      steps, showProgress: true, progressText: '{{current}} de {{total}}',
      nextBtnText: 'Siguiente →', prevBtnText: '← Anterior', doneBtnText: '¡Listo!',
      overlayColor: '#03050a', overlayOpacity: 0.74, stagePadding: 8, stageRadius: 14,
      popoverClass: 'fleet-tour', smoothScroll: true,
      onPopoverRender: (popover) => {
        const skip = D.createElement('button');
        skip.className = 'fleet-skip'; skip.textContent = 'Omitir guía';
        skip.onclick = () => tour.destroy();
        popover.footerButtons.prepend(skip);
      },
      // Al terminar u omitir: ubicar la pantalla en el mapa y sugerir elegir un vehículo.
      onDestroyed: () => {
        const map = D.querySelector('.st-key-tour-map');
        if (map) map.scrollIntoView({behavior: 'smooth', block: 'start'});
        setTimeout(showTip, 900);
      },
    });
    tour.drive();
  }
  // Espera a que Streamlit termine de dibujar el mapa antes de empezar.
  function waitReady(tries) {
    if (D.querySelector('.st-key-tour-map iframe') || tries > 40) load(start);
    else setTimeout(() => waitReady(tries + 1), 250);
  }
  setTimeout(() => waitReady(0), 700);
})();
</script></body></html>"""


# En la demo pública no hay PostgreSQL: los textos que lo mencionan se ajustan.
_SNAPSHOT_TEXTS = {
    "leída de la capa Oro de PostGIS": "leída de una foto de la capa Oro (DuckDB)",
    "La regla la aplica PostgreSQL con <code>SET ROLE</code>: no es un filtro visual, la base "
    "se niega a entregar el dato.": "En esta demo, la consulta del analista nunca lee los IDs "
    "reales; en la instalación local lo garantiza PostgreSQL con <code>SET ROLE</code>.",
}


def _steps(snapshot: bool) -> list[dict]:
    if not snapshot:
        return STEPS
    text = json.dumps(STEPS, ensure_ascii=False)
    for original, replacement in _SNAPSHOT_TEXTS.items():
        text = text.replace(original, replacement)
    return json.loads(text)


def tour_html(force: bool, nonce: int = 0, snapshot: bool = False) -> str:
    """HTML del iframe invisible que inicia el recorrido en la página principal."""
    return (
        _TEMPLATE.replace("__FORCE__", "true" if force else "false")
        .replace("__STEPS__", json.dumps(_steps(snapshot), ensure_ascii=False))
        .replace("__CSS__", json.dumps(TOUR_CSS))
        .replace("__DRIVER__", DRIVER)
        # El nonce cambia el contenido para que Streamlit vuelva a montar el iframe al repetir.
        + f"<!-- {nonce} -->"
    )
