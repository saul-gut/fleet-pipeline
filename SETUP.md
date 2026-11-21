# SETUP · Despliegue local paso a paso

Esta guía levanta todo el pipeline en tu máquina: PostGIS, la API de ingesta, el simulador de flota, el procesamiento Bronce → Plata → Oro y el dashboard.

Funciona en **Windows, macOS y Linux**. Los atajos de `tasks.py` son Python puro, así que no hace falta `make`. Cada atajo ejecuta comandos estándar, y la [tabla final](#referencia-qué-hace-cada-atajo) muestra cuáles, por si prefieres correrlos a mano.

---

## 1. Requisitos

| Herramienta | Versión | Para qué |
| --- | --- | --- |
| [Docker Desktop](https://www.docker.com/products/docker-desktop/) (o Docker Engine + Compose v2) | Compose ≥ 2.20 | PostgreSQL 16 + PostGIS 3.5 |
| [Git](https://git-scm.com/) | cualquiera reciente | clonar el repositorio |
| [uv](https://docs.astral.sh/uv/) | ≥ 0.5 | instala Python 3.12 y las dependencias exactas de `uv.lock` |

No necesitas instalar Python a mano: `uv` descarga la versión correcta (3.12) si no la tienes.

Para instalar `uv`:

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

```powershell
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

> Si ya tienes Python, también sirve `pip install uv`. En Windows, si después `uv` no se reconoce como comando, usa `py -m uv` en lugar de `uv` en todos los pasos.

Antes de seguir, **abre Docker Desktop** y espera a que diga que está corriendo.

---

## 2. Clonar y configurar

```bash
git clone https://github.com/<usuario>/fleet-pipeline.git
cd fleet-pipeline
```

Crea tu archivo de configuración a partir de la plantilla:

```bash
cp .env.example .env              # macOS / Linux
```

```powershell
Copy-Item .env.example .env       # Windows
```

Abre `.env` y cambia los valores `change_me*`:

- `POSTGRES_PASSWORD`: la contraseña de la base de datos.
- `API_KEY_DEVICE`, `API_KEY_ADMIN`, `API_KEY_ANALYST`: una key por rol (dispositivo, administrador, analista).

Para generar valores aleatorios:

```bash
uv run python -c "import secrets; print(secrets.token_urlsafe(32))"
```

> `.env` está en `.gitignore` y nunca se sube al repositorio. Si el puerto 5432 ya lo usa otro PostgreSQL, cambia `POSTGRES_PORT` (por ejemplo, a `5433`).

Instala las dependencias, con las mismas versiones exactas que usa el CI:

```bash
uv sync
```

---

## 3. Levantar PostGIS e inicializar la base

```bash
uv run python tasks.py up
```

Esto ejecuta `docker compose up -d --wait`. Descarga la imagen `postgis/postgis:16-3.5` y espera a que la base esté saludable.

### Cómo se inicializa PostGIS

La primera vez que se crea el volumen de datos, la imagen oficial ejecuta en orden alfabético los scripts SQL de [`db/init/`](db/init/), montados en `/docker-entrypoint-initdb.d`:

| Script | Qué crea |
| --- | --- |
| `01_extensions.sql` | extensión `postgis` |
| `02_srid_9377.sql` | registra EPSG:9377 (MAGNA-SIRGAS Origen Nacional), que el PROJ de la imagen no trae |
| `03_schemas.sql` | esquemas `bronze`, `silver`, `gold`, `analytics` y la función de particiones diarias |
| `04_bronze.sql` | telemetría cruda particionada por día, rechazos, catálogo de vehículos, índices BRIN |
| `05_security.sql` | roles `fleet_ingest`, `bi_admin`, `bi_analyst`, función de enmascaramiento y vistas enmascaradas |
| `06_silver_geofences.sql` | geocercas con índice GiST y subdivisión de polígonos |
| `07_silver_tables.sql` | telemetría enriquecida, alertas y rutas planificadas |
| `08_silver_process.sql` | procesamiento incremental Bronce → Plata (`silver.process_new`) |
| `09_gold.sql` | tabla consolidada `gold.fleet_events` (GiST), Driver Score y vistas para BI |

> **Importante:** Docker solo ejecuta estos scripts cuando el volumen está **vacío**. Si cambias un script o quieres empezar de cero, ejecuta `uv run python tasks.py reset-db`. Este comando **borra todos los datos** y recrea la base.

### Verificar

Abre una consola `psql` dentro del contenedor:

```bash
uv run python tasks.py psql
```

Y ejecuta:

```sql
SELECT postgis_full_version();                       -- PostGIS 3.5 responde
SELECT ST_AsText(ST_Transform(ST_SetSRID(ST_MakePoint(-73, 4), 4326), 9377));
                                                     -- POINT(5000000 2000000): origen de EPSG:9377
\dn                                                  -- bronze, silver, gold, analytics
\q
```

---

## 4. Cargar datos de prueba

La simulación envía la telemetría **por la API**, igual que lo harían los dispositivos reales. Por eso la API debe estar corriendo. Usa dos terminales.

### Terminal 1: API de ingesta

```bash
uv run python tasks.py api
```

Queda escuchando en <http://localhost:8000>. La documentación interactiva está en <http://localhost:8000/docs>. Déjala abierta.

### Terminal 2: datos

```bash
uv run python tasks.py seed
```

`seed` hace cuatro cosas en orden:

1. Carga las 25 geocercas de [`data/geofences/`](data/geofences/) (perímetros urbanos, zonas escolares, puntos críticos de siniestralidad, vía deteriorada).
2. Registra la flota de 20 vehículos y sus rutas en Bogotá, Medellín y el corredor que las une.
3. Simula las **últimas 24 horas** de telemetría, enviada por lotes a la API: un reporte cada 10 s por vehículo, dentro del horario de operación de la flota (5:00–21:00, hora de Colombia).
4. Procesa Bronce → Plata (geocercas, alertas de velocidad y combustible) → Oro (tabla consolidada y Driver Score).

Al terminar, el log muestra cuántos paquetes se aceptaron, se rechazaron o se ignoraron por duplicados, y cuántas lecturas y alertas se procesaron.

> Los datos simulados son relativos a la hora actual: la ingesta rechaza lecturas de más de 7 días o del futuro. Si vuelves al proyecto días después, ejecuta `seed` otra vez.

---

## 5. Abrir el dashboard

En la terminal 2 (la API ya no es necesaria para el dashboard):

```bash
uv run python tasks.py dashboard
```

Abre <http://localhost:8501>. La guía de bienvenida recorre cada sección; para ocultarla, agrega `?tour=0` a la dirección.

En el panel lateral se elige el **rol**. Como *Analista*, todos los IDs aparecen enmascarados (`VHC-****-ABC`): las consultas corren con el rol `bi_analyst` de PostgreSQL, que solo tiene permiso sobre vistas enmascaradas.

---

## 6. Probar la API a mano (opcional)

Con la API corriendo, en <http://localhost:8000/docs> cada endpoint tiene un botón *Try it out* y un campo `x-api-key`. También puedes probarla desde la terminal:

```bash
# Última posición de cada vehículo: con la key de analista el ID sale enmascarado
curl -H "X-API-Key: <API_KEY_ANALYST>" http://localhost:8000/vehicles/positions

# Enviar un paquete (key de dispositivo). Usa una hora reciente con zona horaria.
curl -X POST http://localhost:8000/telemetry \
  -H "X-API-Key: <API_KEY_DEVICE>" -H "Content-Type: application/json" \
  -d '[{"vehicle_id":"VHC-8472-ABC","ts":"2026-11-21T18:30:00-05:00","lat":4.59808,"lon":-74.07609,"speed_kmh":42.5,"fuel_pct":81.3}]'
```

La respuesta resume cuántos paquetes se aceptaron y cuántos se rechazaron, con el motivo. Los rechazados quedan en `bronze.telemetry_rejected`.

> En PowerShell, usa `curl.exe` en lugar de `curl`.

### Simulación en tiempo real

```bash
uv run python tasks.py simulate     # envía un reporte por vehículo cada 10 s hasta Ctrl+C
uv run python tasks.py process      # procesa lo nuevo (incremental: solo lo que llegó)
```

> Fuera del horario de operación (5:00–21:00, hora de Colombia), la flota está detenida: el simulador lo indica en el log y no envía reportes.

---

## 7. Tests y calidad de código

Con PostGIS levantado:

```bash
uv run python tasks.py lint         # ruff: estilo y formato, sin modificar archivos
uv run python tasks.py test         # unitarios + integración, con reporte de cobertura
```

- Los **tests unitarios** no necesitan base de datos.
- Los **tests de integración** usan la base real. Crean sus propios vehículos (`VHC-9001-TST`, `VHC-9002-TST`) y los borran al terminar, así que no alteran los datos del dashboard.
- Si PostGIS no está corriendo, los tests de integración se omiten con un aviso. En el CI, en cambio, fallan.
- La cobertura mínima exigida es 80%.

Para revisar el código automáticamente antes de cada commit (opcional):

```bash
uv run pre-commit install
```

El mismo lint y los mismos tests corren en GitHub Actions en cada push ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)), con PostGIS como servicio.

---

## 8. Benchmark de índices (opcional)

```bash
uv run python scripts/benchmark.py --rows 1000000
```

Genera 1 millón de puntos sintéticos en un esquema aparte (`bench`) y mide las consultas típicas con `EXPLAIN ANALYZE` en tres configuraciones:

1. Sin índices.
2. Con índices.
3. Particionada y con índices.

Los resultados se agregan a [`docs/benchmarks.md`](docs/benchmarks.md) y el esquema `bench` se borra al final (`--keep` lo conserva). No toca los datos del pipeline.

---

## 9. Detener y limpiar

```bash
uv run python tasks.py down         # detiene PostGIS; los datos se conservan
uv run python tasks.py reset-db     # borra el volumen y recrea la base desde db/init
```

---

## Problemas frecuentes

| Síntoma | Causa y solución |
| --- | --- |
| `Cannot connect to the Docker daemon` o `error during connect` | Docker Desktop no está abierto. Ábrelo y espera a que diga que está corriendo. |
| `port is already allocated` al levantar PostGIS | Otro PostgreSQL usa el puerto 5432. Cambia `POSTGRES_PORT` en `.env` y vuelve a ejecutar `up`. |
| Cambié un script de `db/init/` y no se aplica | Solo corren con el volumen vacío: `uv run python tasks.py reset-db`. |
| `seed` falla con `Connection refused` en el puerto 8000 | La API no está corriendo. Levántala en otra terminal con `tasks.py api`. |
| El dashboard dice que no hay datos | Falta `seed`, o los datos tienen más de 7 días. Ejecuta `seed` de nuevo. |
| `uv` no se reconoce como comando (Windows) | Usa `py -m uv ...`, o abre una terminal nueva después de instalarlo. |
| Faltan variables como `postgres_password` | No existe el archivo `.env`. Créalo a partir de `.env.example` (paso 2). |

---

## Referencia: qué hace cada atajo

| Atajo (`uv run python tasks.py …`) | Comando equivalente |
| --- | --- |
| `up` | `docker compose up -d --wait` |
| `down` | `docker compose down` |
| `reset-db` | `docker compose down -v` y `docker compose up -d --wait` |
| `psql` | `docker compose exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'` |
| `api` | `uv run uvicorn fleet_pipeline.api.main:app --port 8000` |
| `seed` | `python -m fleet_pipeline.geofences load` · `python -m fleet_pipeline.simulator register` · `python -m fleet_pipeline.simulator run --backfill-hours 24` · `python -m fleet_pipeline.processing all` |
| `simulate` | `python -m fleet_pipeline.simulator run --live` |
| `process` | `python -m fleet_pipeline.processing all` |
| `dashboard` | `uv run streamlit run dashboard/app.py --server.port 8501` |
| `lint` | `ruff check .` y `ruff format --check .` |
| `test` | `pytest --cov --cov-report=term-missing` |
