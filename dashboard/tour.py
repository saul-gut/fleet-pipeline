"""Recorrido guiado de bienvenida (driver.js 1.10.2, licencia MIT).

Se muestra la primera vez que alguien abre el tablero (queda registrado en el localStorage de
su navegador) y se puede repetir con el botón "Ver guía". Cada paso resalta un contenedor de
Streamlit identificado con `st.container(key=...)`, que Streamlit expone como `.st-key-<key>`.
"""

from __future__ import annotations

import json
from pathlib import Path

DRIVER = "https://cdn.jsdelivr.net/npm/driver.js@1.10.2/dist"
STORAGE_KEY = "fleet-tour-v1"

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
                "o vía deteriorada) y severidad. Pulsa <b>Aplicar filtros</b>: todo se recalcula "
                "de una sola vez."
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
  const FORCE = __FORCE__, KEY = __KEY__, STEPS = __STEPS__, CSS = __CSS__;
  try { if (!FORCE && P.localStorage.getItem(KEY)) return; } catch (e) {}

  function load(done) {
    if (P.driver && P.driver.js) return done();
    const style = D.createElement('style'); style.textContent = CSS; D.head.appendChild(style);
    const css = D.createElement('link'); css.rel = 'stylesheet'; css.href = '__DRIVER__/driver.css';
    D.head.appendChild(css);
    const js = D.createElement('script'); js.src = '__DRIVER__/driver.js.iife.js'; js.onload = done;
    D.head.appendChild(js);
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
      onDestroyed: () => { try { P.localStorage.setItem(KEY, '1'); } catch (e) {} },
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


def tour_html(force: bool, nonce: int = 0) -> str:
    """HTML del iframe invisible que inicia el recorrido en la página principal."""
    return (
        _TEMPLATE.replace("__FORCE__", "true" if force else "false")
        .replace("__KEY__", json.dumps(STORAGE_KEY))
        .replace("__STEPS__", json.dumps(STEPS, ensure_ascii=False))
        .replace("__CSS__", json.dumps(TOUR_CSS))
        .replace("__DRIVER__", DRIVER)
        # El nonce cambia el contenido para que Streamlit vuelva a montar el iframe al repetir.
        + f"<!-- {nonce} -->"
    )
