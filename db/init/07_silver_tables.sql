-- Capa Plata: rutas planificadas, telemetría enriquecida, alertas y marca de agua (D-17).

-- Rutas planificadas de la flota. geom_9377 se guarda para medir en metros reales
-- (ST_LineLocatePoint, km restantes) sin transformar la línea en cada consulta.
CREATE TABLE IF NOT EXISTS silver.routes (
    route_id  text PRIMARY KEY,
    name      text NOT NULL,
    kind      text NOT NULL CHECK (kind IN ('urban_bogota', 'urban_medellin', 'interurban')),
    length_m  double precision NOT NULL CHECK (length_m > 0),
    geom      geometry(LineString, 4326) NOT NULL,
    geom_9377 geometry(LineString, 9377) NOT NULL
);

-- Ruta asignada a cada vehículo.
CREATE TABLE IF NOT EXISTS silver.vehicle_routes (
    vehicle_id text PRIMARY KEY REFERENCES bronze.vehicles (vehicle_id),
    route_id   text NOT NULL REFERENCES silver.routes (route_id)
);

-- Cada lectura válida de Bronce con su contexto espacial y predictivo.
-- Particionada por día como Bronce: las consultas por fecha leen solo lo necesario.
CREATE TABLE IF NOT EXISTS silver.telemetry_enriched (
    vehicle_id          text NOT NULL,
    ts                  timestamptz NOT NULL,
    geom                geometry(Point, 4326) NOT NULL,
    speed_kmh           real NOT NULL,
    fuel_pct            real NOT NULL,
    fuel_l              real NOT NULL,
    -- Geocerca aplicada: la más restrictiva que contiene el punto (NULL = fuera de todas).
    geofence_id         integer REFERENCES silver.geofences (geofence_id),
    zone_type           text,
    speed_limit_kmh     smallint NOT NULL,
    is_over_threshold   boolean NOT NULL,
    step_m              real NOT NULL,  -- distancia efectiva desde la lectura anterior
    route_id            text,
    route_fraction      real,  -- 0 = inicio de la ruta, 1 = final
    direction           smallint CHECK (direction IN (-1, 1)),
    remaining_km        real,
    consumption_l_100km real,
    fuel_needed_l       real,
    is_low_fuel         boolean,
    quality_flags       text[] NOT NULL DEFAULT '{}',
    processed_at        timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (vehicle_id, ts)
) PARTITION BY RANGE (ts);

CREATE INDEX IF NOT EXISTS telemetry_enriched_ts_brin
    ON silver.telemetry_enriched USING brin (ts);

CREATE TABLE IF NOT EXISTS silver.telemetry_enriched_default
    PARTITION OF silver.telemetry_enriched DEFAULT;

SELECT public.ensure_daily_partitions(
    'silver.telemetry_enriched',
    (now() AT TIME ZONE 'America/Bogota')::date - 7,
    (now() AT TIME ZONE 'America/Bogota')::date + 2
);

-- Una fila por episodio de alerta. La unicidad hace idempotente el reprocesamiento.
CREATE TABLE IF NOT EXISTS silver.alerts (
    alert_id    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    vehicle_id  text NOT NULL REFERENCES bronze.vehicles (vehicle_id),
    alert_type  text NOT NULL
                CHECK (alert_type IN ('SPEEDING', 'GEOFENCE_VIOLATION', 'LOW_FUEL_FOR_ROUTE')),
    severity    text NOT NULL CHECK (severity IN ('minor', 'moderate', 'severe')),
    started_at  timestamptz NOT NULL,
    ended_at    timestamptz NOT NULL CHECK (ended_at >= started_at),
    geofence_id integer REFERENCES silver.geofences (geofence_id),
    geom        geometry(Point, 4326) NOT NULL,
    details     jsonb NOT NULL DEFAULT '{}',
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (vehicle_id, alert_type, started_at)
);

CREATE INDEX IF NOT EXISTS alerts_geom_gist ON silver.alerts USING gist (geom);
CREATE INDEX IF NOT EXISTS alerts_started_at_idx ON silver.alerts (started_at);

-- Marca de agua del procesamiento incremental: hasta qué received_at se procesó Bronce.
CREATE TABLE IF NOT EXISTS silver.processing_state (
    job       text PRIMARY KEY,
    watermark timestamptz NOT NULL,
    run_at    timestamptz NOT NULL DEFAULT now()
);
