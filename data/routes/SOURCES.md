# Fuente de las rutas

`routes.geojson` se genera con `scripts/build_routes.py`, que consulta una sola vez el servidor público de demostración de [OSRM](https://project-osrm.org). La simulación no necesita conexión a internet.

- **Datos viales:** © colaboradores de [OpenStreetMap](https://www.openstreetmap.org/copyright), licencia [ODbL](https://opendatacommons.org/licenses/odbl/).
- **Fecha de generación:** 2026-11-20.
- **Contenido:** 9 rutas (5 en Bogotá, 3 en Medellín y el corredor Bogotá–Medellín por la Ruta 60, de 450 km). Cada ruta trae la velocidad típica de cada tramo según OSRM.
- **Nota:** las velocidades de OSRM son de flujo libre, no de tráfico real. El simulador las usa como indicador de la jerarquía de la vía y aplica topes por perfil de conductor.
