# Registro de decisiones técnicas

Decisiones de arquitectura con su contexto, las alternativas consideradas y la razón de la elección. El código las cita por su número (por ejemplo, `D-13`). La visión de conjunto está en [DESIGN.md](../DESIGN.md).

| # | Decisión | Capa |
| --- | --- | --- |
| [D-01](#d-01--postgis-en-docker) | PostGIS en Docker | Plataforma |
| [D-02](#d-02--python-312-con-uv) | Python 3.12 con uv | Plataforma |
| [D-03](#d-03--stack-de-la-aplicación) | Stack de la aplicación | Plataforma |
| [D-04](#d-04--comandos-multiplataforma) | Comandos multiplataforma | Plataforma |
| [D-05](#d-05--ámbito-geográfico-bogotá-medellín-y-corredor) | Ámbito geográfico | Datos |
| [D-06](#d-06--catálogo-de-geocercas-y-límites-de-velocidad) | Catálogo de geocercas y límites | Datos |
| [D-07](#d-07--sistemas-de-coordenadas) | Sistemas de coordenadas | Datos |
| [D-08](#d-08--combustible-porcentaje-en-el-paquete-litros-derivados) | Combustible en porcentaje y litros | Bronce |
| [D-09](#d-09--reglas-de-calidad-del-dato) | Reglas de calidad del dato | Bronce |
| [D-10](#d-10--partición-diaria-alineada-al-día-de-colombia) | Partición diaria | Bronce |
| [D-11](#d-11--índices-de-bronce-sin-gist) | Índices de Bronce sin GiST | Bronce |
| [D-12](#d-12--ingesta-por-lotes-y-autenticación-por-rol) | Ingesta por lotes y roles | Bronce |
| [D-13](#d-13--enmascaramiento-con-seudonimización-y-defensa-en-profundidad) | Enmascaramiento del ID | Privacidad |
| [D-14](#d-14--simulador-de-flota) | Simulador de flota | Bronce |
| [D-15](#d-15--construcción-de-las-geocercas) | Construcción de las geocercas | Plata |
| [D-16](#d-16--geocercas-subdivididas-y-rutas-por-zonas-críticas) | Geocercas subdivididas | Plata |
| [D-17](#d-17--reglas-y-procesamiento-de-la-capa-plata) | Reglas y procesamiento de Plata | Plata |
| [D-18](#d-18--simulador-consciente-de-las-zonas) | Simulador consciente de las zonas | Plata |
| [D-19](#d-19--capa-oro-driver-score-y-benchmark) | Capa Oro, Driver Score y benchmark | Oro |
| [D-20](#d-20--diseño-del-tablero) | Diseño del tablero | Visualización |
| [D-21](#d-21--deckgl-directo-en-un-componente-html) | deck.gl en un componente HTML | Visualización |
| [D-22](#d-22--mapa-base-sin-clave-esri-dark-gray-canvas) | Mapa base sin clave | Visualización |
| [D-23](#d-23--un-tablero-que-responde-como-un-todo-a-los-filtros) | Tablero coherente con los filtros | Visualización |
| [D-24](#d-24--integración-continua-con-postgis-real) | Integración continua | Calidad |

---

## D-01 · PostGIS en Docker

- **Opciones:** (a) Docker con la imagen oficial `postgis/postgis`; (b) instalación nativa de PostgreSQL y PostGIS.
- **Decisión:** (a), con la imagen `postgis/postgis:16-3.5`.
- **Por qué:** es reproducible. Cualquier persona levanta la misma versión con `docker compose up`, la base se inicializa sola con los scripts de `db/init/`, y el CI usa la misma imagen.

## D-02 · Python 3.12 con uv

- **Opciones:** (a) uv con Python 3.12; (b) pip y venv con 3.12; (c) la versión más reciente de Python.
- **Decisión:** (a).
- **Por qué:** las librerías geoespaciales (Shapely, pyproj, psycopg) tardan en publicar binarios para cada versión nueva de Python; 3.12 es estable en todas. `uv.lock` fija versiones exactas, así que el mismo entorno corre en local, en el CI y en la máquina de quien revisa.

## D-03 · Stack de la aplicación

- **Decisión:**
  - Ingesta: FastAPI y Pydantic.
  - Acceso a datos: psycopg 3 con SQL parametrizado, sin ORM.
  - Lógica geométrica testeable en Python: Shapely y pyproj.
  - Tablero: Streamlit y deck.gl.
  - Calidad: pytest, pytest-cov, ruff, pre-commit y GitHub Actions.
- **Por qué:**
  - La lógica espacial vive en PostGIS y debe verse explícita, cosa que un ORM esconde.
  - FastAPI valida los paquetes y documenta la API sin código extra.
  - GeoPandas queda solo para preparar datos (grupo de dependencias `prep`), porque es pesado y en el pipeline no aporta frente a Shapely y PostGIS.

## D-04 · Comandos multiplataforma

- **Contexto:** `make` no viene instalado en Windows.
- **Decisión:** `tasks.py`, en Python puro, con atajos cortos (`up`, `api`, `seed`, `dashboard`, `test`). SETUP.md documenta el comando equivalente de cada atajo.
- **Por qué:** funciona igual en Windows, macOS y Linux sin herramientas extra.

## D-05 · Ámbito geográfico: Bogotá, Medellín y corredor

- **Opciones:** (a) solo Bogotá; (b) Bogotá, Medellín y el corredor interurbano; (c) Bogotá y una ruta corta.
- **Decisión:** (b). Hay geocercas detalladas en las dos ciudades y en el corredor que las une. La flota es mixta: vehículos urbanos y camiones interurbanos.
- **Por qué:**
  - La alerta de autonomía de combustible solo tiene sentido de negocio en trayectos largos (~415 km), y el caso B2B típico es interurbano.
  - La regla de límite dinámico se ejercita con zonas urbanas, escolares y de carretera.

## D-06 · Catálogo de geocercas y límites de velocidad

- **Límite legal:** urbano 50 km/h, escolar 30 km/h y carretera nacional 90 km/h, según la Ley 2251 de 2022 (art. 106 del Código Nacional de Tránsito) y la Resolución 25995 de 2023 del Ministerio de Transporte.
- **Política de la flota**, más estricta, para zonas de riesgo que la ley no define:
  - punto crítico urbano: 40 km/h;
  - tramo crítico del corredor (Guarne): 60 km/h;
  - vía deteriorada (El Santuario–Caño Alegre): 70 km/h.
- Cada geocerca registra el origen de su límite: `limit_source = law | fleet_policy`.
- **Por qué:** no se inventan normas. La política de flota más estricta es una práctica real del sector y alimenta el Driver Score.

## D-07 · Sistemas de coordenadas

- **Contexto:** la imagen de PostGIS trae PROJ 7.2.1, que no incluye EPSG:9377 (MAGNA-SIRGAS Origen Nacional).
- **Opciones:** (a) solo 4326 y `geography`; (b) 4326 y `geography`, más 9377 registrado a mano; (c) cambiar de imagen.
- **Decisión:** (b).
  - Se almacena en **EPSG:4326**, el estándar del GPS y de los mapas web.
  - Las distancias y el consumo se calculan con **`geography`**, sobre el elipsoide.
  - Las operaciones planas (DBSCAN, buffers, `ST_LineLocatePoint`) se hacen en **EPSG:9377**, registrado en `db/init/02_srid_9377.sql` con la definición oficial del IGAC.
- **Por qué:** cada sistema se usa para lo que sirve. 9377 cubre todo el país con un solo plano métrico.
- **Validación:** el origen (4° N, 73° O) da exactamente (5 000 000, 2 000 000), y la ida y vuelta 4326 → 9377 → 4326 tiene un error de 0 m.

## D-08 · Combustible: porcentaje en el paquete, litros derivados

- **Opciones:** (a) `fuel_pct` en el paquete y la capacidad del tanque en un catálogo de vehículos; (b) solo litros; (c) ambos en el paquete.
- **Decisión:** (a). Los litros se calculan con `tank_capacity_l` del catálogo `bronze.vehicles`.
- **Por qué:**
  - Hay una sola fuente de verdad: dos medidas del mismo dato pueden contradecirse.
  - Es lo realista, porque los sensores de nivel miden porcentaje.
  - El catálogo también guarda el tipo de vehículo y el consumo de referencia.

## D-09 · Reglas de calidad del dato

- **Contexto:** los dispositivos GPS reales envían datos corruptos: coordenadas (0, 0), fechas absurdas, saltos imposibles.
- **Decisión:** cada paquete tiene uno de tres resultados.
  - **Rechazar** (va a `bronze.telemetry_rejected` con su motivo):
    - campos vacíos o nulos;
    - ID que no cumple el formato `VHC-0000-AAA`;
    - timestamp sin zona horaria, más de 5 min en el futuro o de hace más de 7 días;
    - latitud o longitud exactamente 0;
    - coordenadas fuera de Colombia;
    - velocidad fuera de 0–250 km/h o combustible fuera de 0–100%;
    - **salto imposible:** más de 200 km/h implícitos contra la lectura anterior y al menos 100 m de distancia. Por debajo de 100 m es ruido del GPS: 60 m en 1 s dan 216 km/h implícitos.
  - **Ignorar sin error:** el duplicado exacto (`vehicle_id`, `ts`). El reenvío es normal en los dispositivos (idempotencia).
  - **Guardar con bandera** (`quality_flags`):
    - cambio de velocidad mayor a 30 km/h por segundo;
    - caída de combustible de 5 puntos o más en 10 minutos o menos.

    Pueden ser errores del sensor, pero también eventos reales (frenada de emergencia, robo de combustible).
- **Aclaraciones:**
  - Velocidad 0 y combustible 0% son válidos. El cero solo se rechaza en las coordenadas.
  - Los campos extra del paquete se ignoran, para que un firmware nuevo no rompa la ingesta.
- **Por qué:** se separa lo físicamente imposible (se rechaza) de lo sospechoso pero posible (se marca). Nada se descarta en silencio, salvo los duplicados.
- **Validación:** las distancias se calculan con pyproj sobre WGS84 y coinciden al milímetro con `geography` de PostGIS.

## D-10 · Partición diaria alineada al día de Colombia

- **Contexto:** una flota de 10 000 vehículos que reporta cada 30 s genera ~28,8 millones de filas por día (estimación).
- **Opciones:** (a) partición diaria; (b) mensual; (c) sin particiones.
- **Decisión:** (a), por rango de `ts`, con una partición `DEFAULT` de seguridad. La función genérica `public.ensure_daily_partitions()` crea las particiones de la ventana válida de ingesta.
- **Cortes a la medianoche de Colombia:** los límites se calculan con `AT TIME ZONE 'America/Bogota'`. Con cortes en UTC, un viaje de las 7:00 p. m. quedaba partido en dos días, y el Driver Score diario mezclaba jornadas. Los timestamps se siguen guardando como `timestamptz`.
- **Por qué:**
  - Las consultas por fecha leen solo las particiones necesarias (*partition pruning*).
  - La retención es un `DROP` instantáneo y no un `DELETE` masivo.
  - El resultado no depende de la zona horaria de la sesión, y Colombia no tiene horario de verano.

## D-11 · Índices de Bronce sin GiST

- **Decisión:** Bronce solo lleva la PK `(vehicle_id, ts)`, que da idempotencia y acceso por vehículo, y BRIN en `ts` y `received_at`. El GiST va en `silver.geofence_parts` y en `gold.fleet_events`.
- **Por qué:** Bronce es de escritura intensiva, y cada índice encarece cada inserción. Nadie hace consultas espaciales sobre el dato crudo. En el cruce punto–polígono, el índice que trabaja es el GiST de las geocercas.

## D-12 · Ingesta por lotes y autenticación por rol

- **Opciones de envío:** (a) lotes; (b) un paquete por petición. **Opciones de autenticación:** (a) API key por rol en un header; (b) sin autenticación.
- **Decisión:** (a) y (a).
  - `POST /telemetry` recibe hasta 1 000 paquetes y responde un resumen: aceptados, duplicados, rechazados y marcados.
  - Header `X-API-Key` con tres roles: `device` envía; `admin` y `analyst` consultan.
- **Por qué:**
  - Los dispositivos reales acumulan lecturas cuando pierden señal y las envían juntas, y los lotes reducen las idas y vueltas por red.
  - Un solo mecanismo de roles resuelve la autenticación y el enmascaramiento.

## D-13 · Enmascaramiento con seudonimización y defensa en profundidad

- **Contexto:** la máscara `VHC-****-ABC` colisiona. Dos vehículos con las mismas letras se vuelven indistinguibles, y se rompe el análisis por vehículo.
- **Opciones:** (a) solo la máscara; (b) máscara y clave sustituta; (c) máscara y hash del ID.
- **Decisión:**
  - (b): `bronze.vehicles.vehicle_key` es un entero sin significado. Solo un administrador puede unirlo con el ID real.
  - Se descarta el hash: solo hay 10⁴ × 26³ ≈ 175 millones de IDs posibles, y se revierte por fuerza bruta en segundos.
  - El esquema `analytics` contiene solo vistas enmascaradas. `bi_analyst` tiene permiso únicamente sobre ese esquema y sobre Oro; `bi_admin` lee todo.
  - `analytics.mask_vehicle_id()` es a prueba de fallos: un ID con formato inesperado se devuelve totalmente enmascarado.
  - La API y el tablero asumen en la base el rol de quien consulta (`SET LOCAL ROLE`).
  - La ingesta usa `fleet_ingest`, que solo puede insertar y leer en Bronce (*append-only*).
- **Por qué:**
  - La seudonimización conserva el análisis por vehículo sin revelar la identidad.
  - La base aplica la regla aunque el código de la aplicación tenga un error.
  - Es pertinente frente a la Ley 1581 de 2012 (protección de datos personales) si el vehículo se asocia a su conductor.

## D-14 · Simulador de flota

- **Rutas:** (a) OSRM, generadas una vez; (b) puntos en línea recta; (c) OSMnx local. **Decisión: (a).** `scripts/build_routes.py` consulta el servidor público de OSRM (datos de OpenStreetMap, ODbL) y guarda las rutas con la velocidad típica de cada tramo. Así la simulación es reproducible sin conexión.
- **Flota:** 20 vehículos:
  - 10 urbanos en Bogotá, 6 en Medellín y 4 camiones Bogotá ↔ Medellín;
  - perfiles de conductor prudente (~60%), normal (~25%) y agresivo (~15%).
- **Eventos inyectados:**
  - un camión sale con combustible insuficiente para el trayecto;
  - ~2% de paquetes defectuosos;
  - ruido GPS de unos metros.
- **Modos:** histórico (genera días pasados en minutos) y en vivo. Ambos envían a la API, nunca directo a la base, así que todo pasa por las reglas de calidad.
- **Por qué:** las alertas surgen del comportamiento simulado y no de datos forzados, y las rutas reales hacen creíbles las distancias y el consumo.

## D-15 · Construcción de las geocercas

- **Puntos críticos:** `ST_ClusterDBSCAN` en EPSG:9377, calibrado por densidad:
  - Bogotá, con siniestros con víctimas fatales: radio de 250 m y mínimo de 8 puntos.
  - Medellín, con siniestros con heridos: radio de 75 m y mínimo de 100 puntos.
  - Se toman los 5 clusters más grandes por ciudad. El polígono es la envolvente convexa + 50 m.
  - Las fuentes miden cosas distintas, así que los puntos críticos solo son comparables dentro de cada ciudad.
- **Depuración de Medellín:** 30 153 registros caen en solo 6 490 coordenadas, porque se geocodifican a intersecciones. Las coordenadas con más de 150 registros resultaron ser ubicaciones por defecto de direcciones no geocodificadas, así que se excluyen. En Bogotá la repetición máxima es 4.
- **Zonas escolares:** el mismo DBSCAN sobre colegios, con buffer de ~100 m por colegio.
- **Herramientas:** GeoPandas solo para leer las fuentes; el clustering se hace en PostGIS. El resultado se versiona en `data/geofences/geofences.geojson`.
- **Licencias:**
  - Las zonas escolares de Medellín derivan de datos CC BY-SA 4.0 y se publican con la misma licencia.
  - Solo se publican los polígonos derivados, nunca los registros de siniestros, que contienen datos de las víctimas.

## D-16 · Geocercas subdivididas y rutas por zonas críticas

- **Rendimiento:** el GiST filtra por rectángulo envolvente. La autopista tiene 33 427 vértices y un rectángulo que cubre las dos ciudades, así que no se descartaba para ningún punto.
- **Decisión:** `silver.geofences` queda como tabla de negocio, y el cargador genera `silver.geofence_parts` con `ST_Subdivide`: 439 piezas de 256 vértices como máximo, con su propio GiST. El cruce espacial usa las piezas.
- **Medido** con `EXPLAIN ANALYZE` sobre 113 000 lecturas: 10 653 ms → 1 736 ms (6,1×). Un test comprueba con 3 000 puntos que las piezas dan el mismo resultado que los polígonos completos.
- **Rutas:** cada ruta urbana pasa por las zonas críticas que tiene a menos de ~2 km, como lo haría una ruta de reparto. Así el escenario ejercita las reglas en varias zonas.

## D-17 · Reglas y procesamiento de la capa Plata

- **Procesamiento:** (a) SQL por lotes en PostGIS; (b) Python fila por fila. **Decisión: (a).**
  - `silver.process_new()` procesa lo recibido desde la última marca de agua, con 5 minutos de solape para datos tardíos y una ventana de contexto de 3 horas por vehículo.
  - Hace `UPSERT`, así que reprocesar no duplica.
  - Las reglas también están escritas en Python puro (`processing/rules.py`) como especificación con unit tests, y los tests de integración verifican que el SQL da los mismos resultados.
- **Velocidad:**
  - El límite es el de la geocerca más restrictiva; fuera de toda geocerca, 90 km/h.
  - Tolerancia: +5 km/h.
  - Se exigen 2 lecturas seguidas sobre el umbral.
  - Una alerta por episodio. El episodio se corta si cambia la geocerca o si pasan más de 30 s entre lecturas.
  - Severidad según el exceso sobre el límite: leve ≤ 20%, moderada ≤ 50%, grave > 50%.
- **Infracción de geocerca:** si el límite viene de una zona especial (escolar, punto crítico o vía deteriorada), el episodio es `GEOFENCE_VIOLATION`; si viene del límite general, es `SPEEDING`. Así el heatmap distingue los dos tipos de infracción.
- **Consumo predictivo:**
  - Los km restantes salen de `ST_LineLocatePoint` sobre la ruta planificada, en EPSG:9377, y del sentido del viaje.
  - El consumo real se mide en los últimos 20 km, con el combustible suavizado y reiniciando la ventana en cada recarga.
  - No se suma distancia si el vehículo está detenido o se mueve menos de 15 m (ruido GPS).
  - Con menos de 5 km de historia se usa el consumo de referencia.
  - Alerta `LOW_FUEL_FOR_ROUTE` si `litros < km restantes × consumo × 1,15`, con 2 lecturas seguidas y una sola vez por trayecto.
  - Severidad según la fracción de lo necesario que hay en el tanque: < 50% grave, < 80% moderada.
- **Validación:** el consumo estimado (35,4–35,9 L/100 km) coincide con el del simulador (35 L/100 km más la penalización por conducción brusca).
- **Carga incremental sin reescrituras:**
  - El solape de Plata recalcula lecturas ya procesadas, pero el `UPSERT` solo reescribe las que cambiaron (`IS DISTINCT FROM`).
  - Plata sella cada lote con `clock_timestamp()` tomado después de obtener su bloqueo. Como sus ejecuciones van de a una, el sello crece de lote en lote, y Oro lee con una marca de agua estricta.
  - Se detectó al probar sobre una base recién cargada: Oro, con un solape de 5 minutos, reescribía 112 934 filas para cargar 70 lecturas nuevas (3 939 ms). Ahora escribe 70 en 17 ms.

## D-18 · Simulador consciente de las zonas

- **Contexto:** sin conocer las geocercas, todos los conductores excedían el límite escolar, y el Driver Score no distinguía perfiles.
- **Decisión:** cada ruta precalcula el límite de la geocerca más restrictiva en cada segmento, reutilizando `rules.applied_zone` con un STRtree de Shapely. El conductor prudente va a límite − 2, el normal a límite + 8, y el agresivo ignora la zona.
- **Resultado (24 h), alertas promedio por vehículo:**
  - prudente: 0 excesos y 0,8 infracciones;
  - normal: 166 excesos, casi todos leves, y 59 infracciones moderadas;
  - agresivo: 187 excesos, la mitad graves, y 56 infracciones casi todas graves.

## D-19 · Capa Oro, Driver Score y benchmark

- **Tabla consolidada:** `gold.fleet_events`. Opciones: (a) vista materializada; (b) tabla incremental. **Decisión: (b)**, porque una vista materializada recalcula todo en cada refresco.
  - Una fila por lectura, con clave sustituta e ID ya enmascarado: **Oro nunca guarda el ID real**.
  - Índices: GiST en `geom`, GiST parcial en las filas con alerta, BRIN en `ts`, partición diaria y PK `(vehicle_key, ts)`.
- **Driver Score diario (0–100):**
  - Puntos por episodio: `SPEEDING` leve, moderado y grave = 1, 3 y 6. `GEOFENCE_VIOLATION` vale el doble.
  - Puntaje = `100 × e^(−0,005 × puntos por 100 km)`.
  - Se descartó la resta lineal porque se satura en 0. Con la exponencial, los perfiles se separan: prudentes 89–100, normales 46–72, agresivos 18–44.
  - La alerta de combustible no resta: es planeación, no conducción.
  - Con menos de 5 km en el día no hay puntaje.
  - Es una vista materializada pequeña con índice único, para refrescarla `CONCURRENTLY`.
- **Benchmark:** replica las lecturas simuladas, corridas en el tiempo, hasta 1 y 5 millones de filas. Mide 4 consultas de BI con `EXPLAIN ANALYZE` (mediana de 5) en tres configuraciones: sin índices, con índices, y particionada con índices.
- **Hallazgos:**
  - Con 1 millón de filas, el heatmap de 7 días fue **más lento con índices** (58 → 102 ms): la consulta no era selectiva. La partición lo resolvió (25 ms).
  - La partición penaliza las consultas sin filtro de tiempo: el KNN pasa de 0,1 a 3,5 ms. Se acepta, porque el tablero siempre filtra por fecha.

## D-20 · Diseño del tablero

- **Estructura:** (a) una sola página "Centro de control"; (b) pestañas. **Decisión: (a).** Filtros en la barra lateral, KPIs arriba, mapa al centro, ranking de Driver Score y alertas debajo, y el tiempo de cada consulta al pie.
- **Datos:** (a) lectura directa de PostgreSQL asumiendo el rol (`SET LOCAL ROLE`); (b) a través de la API. **Decisión: (a).**
  - El tablero solo lee Oro y las geocercas, y el enmascaramiento lo aplica la base.
  - Las consultas viven en `fleet_pipeline/queries/dashboard.py`, con tests de integración.
- **Heatmap:** un punto por episodio, en el lugar de máxima velocidad, con el peso de severidad del Driver Score (1, 3 o 6). Con un punto por lectura, un solo episodio largo inflaba el calor. Hay interruptor a hexágonos 3D.
- **Paleta:**
  - zonas especiales con tres tonos fijos y un patrón de achurado distinto, como codificación secundaria para daltonismo;
  - severidad con colores de estado y su etiqueta;
  - heatmap de un solo tono.

## D-21 · deck.gl directo en un componente HTML

- **Opciones:** (a) pydeck; (b) deck.gl en JavaScript dentro de un componente HTML de Streamlit.
- **Decisión:** (b). Es el mismo deck.gl que usa pydeck por dentro, pero permite animación fluida (estelas con TripsLayer, alertas que pulsan) y control total del estilo. La versión de deck.gl está fijada.
- **Por qué:** el mapa se dibuja en el navegador de quien lo abre, así que funciona igual en local o publicado.

## D-22 · Mapa base sin clave: Esri Dark Gray Canvas

- **Contexto:** el mapa base debe verse sin crear cuentas ni configurar secretos. Las direcciones públicas de CARTO pasaron a exigir clave.
- **Opciones:** (a) Esri World Dark Gray Canvas; (b) CARTO o Mapbox con clave en `.env`; (c) sin mapa base.
- **Decisión:** (a), con dos capas: el fondo y las etiquetas, dibujadas sobre el heatmap y bajo la ruta. Atribución visible: "Esri, HERE, Garmin, © OpenStreetMap y la comunidad de usuarios GIS".
- **Por qué:** con (b), quien clone el repositorio vería el mapa en blanco si no crea su propia clave; con (c) se pierde el contexto urbano. Los términos de Esri permiten este uso para demostración con atribución. En un producto comercial correspondería una cuenta de ArcGIS.

## D-23 · Un tablero que responde como un todo a los filtros

- **Alcance común:** todas las consultas reciben el mismo alcance (periodo y vehículos) y el mismo filtro de alertas (tipos y severidades). KPIs, mapa, ranking y alertas responden juntos.
- **Filtros:**
  - periodo de un día o varios;
  - ciudad (vista `gold.vehicle_directory`);
  - vehículo, que tiene prioridad sobre la ciudad;
  - tipos de infracción y severidad.

  Van en un formulario con botón **Aplicar filtros**: una sola recarga por cambio.
- **Dentro del mapa, sin recargar:** las capas, el cambio de heatmap a hexágonos y el encuadre.
- **Mapa:**
  - con todos los vehículos se ve la flota completa, con recorridos muestreados y estelas animadas;
  - con un vehículo, su ruta en detalle, coloreada por la severidad que pasa los filtros, y flechas de sentido cuando el periodo es de un día.
- **Combustible:** cada alerta se dibuja en el mapa con su destino, los km que faltan, los litros que tiene y los que necesita. En la tabla aparece fija arriba.
- **Experiencia:**
  - guía de bienvenida con driver.js (MIT), que se puede omitir y desactivar con `?tour=0`;
  - barra de avance real de la carga del mapa base;
  - enlace directo a un vehículo con `?vehicle=<clave>`;
  - el servidor escucha solo en `127.0.0.1`.

## D-24 · Integración continua con PostGIS real

- **Contexto:** la lógica espacial vive en SQL. Un CI con solo tests unitarios no la probaría.
- **Decisión:** GitHub Actions en cada push y pull request:
  1. `ruff` para lint y formato;
  2. `postgis/postgis:16-3.5` como servicio, inicializado con `db/init/*.sql` en orden (`psql -v ON_ERROR_STOP=1`);
  3. la suite completa con cobertura.
- **Protecciones:**
  - La cobertura mínima es 80% (`fail_under`).
  - En local, los tests de integración se omiten si no hay base. En el CI (`CI=true`), **fallan**, para evitar un verde falso.
