-- Capa Bronce: catálogo de vehículos, telemetría cruda particionada por día y rechazos.

-- Catálogo de vehículos (D-08). La capacidad del tanque permite derivar litros desde fuel_pct.
-- vehicle_key es una clave sustituta sin significado (D-13): permite analizar por vehículo
-- sin conocer el ID real. La relación key <-> ID solo es visible para bi_admin.
CREATE TABLE IF NOT EXISTS bronze.vehicles (
    vehicle_id             text PRIMARY KEY
                           CHECK (vehicle_id ~ '^VHC-[0-9]{4}-[A-Z]{3}$'),
    vehicle_key            integer GENERATED ALWAYS AS IDENTITY UNIQUE,
    vehicle_type           text NOT NULL
                           CHECK (vehicle_type IN ('car', 'van', 'truck')),
    tank_capacity_l        numeric(6, 1) NOT NULL CHECK (tank_capacity_l > 0),
    ref_consumption_l_100km numeric(5, 2) NOT NULL CHECK (ref_consumption_l_100km > 0),
    created_at             timestamptz NOT NULL DEFAULT now()
);

-- Telemetría cruda (D-10, D-11).
-- La geometría se deriva de lat/lon en la base: una sola fuente de verdad.
-- La clave primaria incluye ts porque PostgreSQL exige la clave de partición en las claves únicas;
-- además garantiza la idempotencia (un vehículo no puede reportar dos veces el mismo instante).
CREATE TABLE IF NOT EXISTS bronze.telemetry_raw (
    id            bigserial,
    vehicle_id    text NOT NULL REFERENCES bronze.vehicles (vehicle_id),
    ts            timestamptz NOT NULL,
    lat           double precision NOT NULL CHECK (lat BETWEEN -90 AND 90),
    lon           double precision NOT NULL CHECK (lon BETWEEN -180 AND 180),
    speed_kmh     numeric(5, 1) NOT NULL CHECK (speed_kmh >= 0),
    fuel_pct      numeric(5, 2) NOT NULL CHECK (fuel_pct BETWEEN 0 AND 100),
    geom          geometry(Point, 4326)
                  GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(lon, lat), 4326)) STORED,
    quality_flags text[] NOT NULL DEFAULT '{}',
    received_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (vehicle_id, ts)
) PARTITION BY RANGE (ts);

-- BRIN: guarda el mínimo y el máximo de ts por bloque de páginas. Pesa KB y sirve porque
-- los datos llegan en orden de tiempo. Se crea en la tabla padre y se propaga a cada partición.
CREATE INDEX IF NOT EXISTS telemetry_raw_ts_brin
    ON bronze.telemetry_raw USING brin (ts);

-- Partición de seguridad: recibe filas con fechas sin partición propia, para no perder datos.
CREATE TABLE IF NOT EXISTS bronze.telemetry_raw_default
    PARTITION OF bronze.telemetry_raw DEFAULT;

-- Crea las particiones diarias faltantes entre dos fechas (ambas incluidas). Idempotente.
-- Devuelve cuántas particiones creó.
-- Los cortes siguen el día de negocio de Colombia (D-10): cada partición va de medianoche a
-- medianoche en America/Bogota, sin importar la zona horaria de la sesión. Colombia no tiene
-- horario de verano, así que todos los días duran 24 h.
CREATE OR REPLACE FUNCTION bronze.ensure_partitions(p_from date, p_to date)
RETURNS integer
LANGUAGE plpgsql
AS $$
DECLARE
    v_day     date;
    v_name    text;
    v_created integer := 0;
BEGIN
    IF p_from > p_to THEN
        RAISE EXCEPTION 'p_from (%) no puede ser posterior a p_to (%)', p_from, p_to;
    END IF;

    FOR v_day IN SELECT generate_series(p_from, p_to, interval '1 day')::date LOOP
        v_name := format('telemetry_raw_%s', to_char(v_day, 'YYYYMMDD'));
        IF to_regclass(format('bronze.%I', v_name)) IS NULL THEN
            -- %I y %L escapan identificadores y literales: no hay SQL concatenado a mano.
            EXECUTE format(
                'CREATE TABLE bronze.%I PARTITION OF bronze.telemetry_raw
                 FOR VALUES FROM (%L) TO (%L)',
                v_name,
                v_day::timestamp AT TIME ZONE 'America/Bogota',
                (v_day + 1)::timestamp AT TIME ZONE 'America/Bogota'
            );
            v_created := v_created + 1;
        END IF;
    END LOOP;

    RETURN v_created;
END;
$$;

-- Ventana inicial: los 7 días que acepta la validación (D-09) más 2 días hacia adelante.
-- Se usa la fecha de Colombia, no current_date, que depende de la zona horaria de la sesión.
SELECT bronze.ensure_partitions(
    (now() AT TIME ZONE 'America/Bogota')::date - 7,
    (now() AT TIME ZONE 'America/Bogota')::date + 2
);

-- Paquetes rechazados con su motivo (D-09). El payload se guarda tal cual llegó (jsonb),
-- porque un paquete inválido puede no encajar en los tipos de telemetry_raw.
-- Sin particiones: su volumen es una fracción pequeña del total.
CREATE TABLE IF NOT EXISTS bronze.telemetry_rejected (
    id          bigserial PRIMARY KEY,
    vehicle_id  text,
    payload     jsonb NOT NULL,
    reasons     text[] NOT NULL CHECK (cardinality(reasons) > 0),
    received_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS telemetry_rejected_received_at_idx
    ON bronze.telemetry_rejected (received_at);
