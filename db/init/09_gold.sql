-- Capa Oro: tabla consolidada para BI y Driver Score (D-19).
--
-- Privacidad por diseño: Oro nunca guarda el ID real del vehículo, solo la clave sustituta y
-- el ID enmascarado. Por eso el analista puede leer este esquema directamente.

-- Índice para asociar cada lectura con su episodio de alerta.
CREATE INDEX IF NOT EXISTS alerts_vehicle_started_idx
    ON silver.alerts (vehicle_id, started_at);
-- Marca de agua de Oro: Plata se lee por processed_at.
CREATE INDEX IF NOT EXISTS telemetry_enriched_processed_brin
    ON silver.telemetry_enriched USING brin (processed_at);

CREATE TABLE IF NOT EXISTS gold.fleet_events (
    vehicle_key       integer NOT NULL,
    vehicle_id_masked text NOT NULL,
    vehicle_type      text NOT NULL,
    ts                timestamptz NOT NULL,
    event_date        date NOT NULL,  -- día de negocio en Colombia
    geom              geometry(Point, 4326) NOT NULL,
    speed_kmh         real NOT NULL,
    speed_limit_kmh   smallint NOT NULL,
    excess_kmh        real NOT NULL,  -- velocidad - límite (negativo = por debajo)
    is_over_threshold boolean NOT NULL,
    zone_type         text,
    geofence_code     text,
    alert_id          bigint,  -- episodio de velocidad o de geocerca al que pertenece
    alert_type        text,
    severity          text,
    fuel_pct          real NOT NULL,
    fuel_l            real NOT NULL,
    remaining_km      real,
    is_low_fuel       boolean,
    route_id          text,
    step_m            real NOT NULL,
    PRIMARY KEY (vehicle_key, ts)
) PARTITION BY RANGE (ts);

CREATE TABLE IF NOT EXISTS gold.fleet_events_default PARTITION OF gold.fleet_events DEFAULT;

SELECT public.ensure_daily_partitions(
    'gold.fleet_events',
    (now() AT TIME ZONE 'America/Bogota')::date - 7,
    (now() AT TIME ZONE 'America/Bogota')::date + 2
);

-- GiST: "¿qué pasó en esta zona o en este rectángulo del mapa?".
CREATE INDEX IF NOT EXISTS fleet_events_geom_gist ON gold.fleet_events USING gist (geom);
-- GiST parcial: solo las lecturas que forman parte de una alerta (la capa del heatmap).
-- Indexa una fracción de las filas: más pequeño y más rápido para esa consulta.
CREATE INDEX IF NOT EXISTS fleet_events_alerts_geom_gist
    ON gold.fleet_events USING gist (geom) WHERE alert_type IS NOT NULL;
-- BRIN: rangos de tiempo dentro de cada partición, con un costo de KB.
CREATE INDEX IF NOT EXISTS fleet_events_ts_brin ON gold.fleet_events USING brin (ts);


-- Carga incremental Plata -> Oro. Procesa las lecturas de Plata actualizadas desde la última
-- marca de agua, más las cubiertas por alertas que cambiaron (un episodio puede extenderse
-- en un lote posterior). Upsert: reprocesar no duplica.
--
-- Marca de agua estricta, sin solape: Plata sella cada lote con la hora en que obtiene su
-- bloqueo (silver.process_new), así que un lote que se confirme después de esta lectura
-- siempre tendrá un sello mayor. Un solape volvería a escribir todo lo reciente en cada
-- ejecución (medido: 112 934 filas reescritas para cargar 70 lecturas nuevas).
DROP FUNCTION IF EXISTS gold.process_new(interval);
CREATE OR REPLACE FUNCTION gold.process_new()
RETURNS TABLE (events_upserted bigint, new_watermark timestamptz)
LANGUAGE plpgsql
AS $$
DECLARE
    v_from timestamptz;
    v_to   timestamptz;
    v_rows bigint;
    v_min  timestamptz;
    v_max  timestamptz;
BEGIN
    DROP TABLE IF EXISTS _gold_candidates;

    SELECT s.watermark INTO v_from
    FROM silver.processing_state AS s WHERE s.job = 'gold' FOR UPDATE;
    IF NOT FOUND THEN
        v_from := '-infinity';
        INSERT INTO silver.processing_state (job, watermark) VALUES ('gold', v_from);
    END IF;

    SELECT max(processed_at) INTO v_to
    FROM silver.telemetry_enriched WHERE processed_at > v_from;
    IF v_to IS NULL THEN
        RETURN QUERY SELECT 0::bigint, v_from;
        RETURN;
    END IF;

    CREATE TEMP TABLE _gold_candidates ON COMMIT DROP AS
    SELECT e.vehicle_id, e.ts
    FROM silver.telemetry_enriched AS e
    WHERE e.processed_at > v_from AND e.processed_at <= v_to
    UNION
    SELECT e.vehicle_id, e.ts
    FROM silver.alerts AS a
    JOIN silver.telemetry_enriched AS e
      ON e.vehicle_id = a.vehicle_id AND e.ts BETWEEN a.started_at AND a.ended_at
    WHERE a.updated_at > v_from AND a.updated_at <= v_to
      AND a.alert_type <> 'LOW_FUEL_FOR_ROUTE';

    SELECT min(ts), max(ts) INTO v_min, v_max FROM _gold_candidates;
    PERFORM public.ensure_daily_partitions(
        'gold.fleet_events',
        (v_min AT TIME ZONE 'America/Bogota')::date,
        (v_max AT TIME ZONE 'America/Bogota')::date
    );

    INSERT INTO gold.fleet_events AS g (
        vehicle_key, vehicle_id_masked, vehicle_type, ts, event_date, geom, speed_kmh,
        speed_limit_kmh, excess_kmh, is_over_threshold, zone_type, geofence_code, alert_id,
        alert_type, severity, fuel_pct, fuel_l, remaining_km, is_low_fuel, route_id, step_m
    )
    SELECT
        v.vehicle_key,
        analytics.mask_vehicle_id(v.vehicle_id),
        v.vehicle_type,
        e.ts,
        (e.ts AT TIME ZONE 'America/Bogota')::date,
        e.geom, e.speed_kmh, e.speed_limit_kmh,
        e.speed_kmh - e.speed_limit_kmh,
        e.is_over_threshold, e.zone_type, gf.code,
        al.alert_id, al.alert_type, al.severity,
        e.fuel_pct, e.fuel_l, e.remaining_km, e.is_low_fuel, e.route_id, e.step_m
    FROM _gold_candidates AS c
    JOIN silver.telemetry_enriched AS e USING (vehicle_id, ts)
    JOIN bronze.vehicles AS v USING (vehicle_id)
    LEFT JOIN silver.geofences AS gf USING (geofence_id)
    LEFT JOIN LATERAL (
        SELECT a.alert_id, a.alert_type, a.severity
        FROM silver.alerts AS a
        WHERE a.vehicle_id = e.vehicle_id
          AND a.alert_type <> 'LOW_FUEL_FOR_ROUTE'
          AND e.ts BETWEEN a.started_at AND a.ended_at
        ORDER BY a.started_at DESC
        LIMIT 1
    ) AS al ON true
    WHERE e.ts >= v_min  -- límite global: permite descartar particiones de Plata
    ON CONFLICT (vehicle_key, ts) DO UPDATE SET
        speed_limit_kmh = EXCLUDED.speed_limit_kmh, excess_kmh = EXCLUDED.excess_kmh,
        is_over_threshold = EXCLUDED.is_over_threshold, zone_type = EXCLUDED.zone_type,
        geofence_code = EXCLUDED.geofence_code, alert_id = EXCLUDED.alert_id,
        alert_type = EXCLUDED.alert_type, severity = EXCLUDED.severity,
        remaining_km = EXCLUDED.remaining_km, is_low_fuel = EXCLUDED.is_low_fuel,
        step_m = EXCLUDED.step_m;
    GET DIAGNOSTICS v_rows = ROW_COUNT;

    UPDATE silver.processing_state SET watermark = v_to, run_at = now() WHERE job = 'gold';
    RETURN QUERY SELECT v_rows, v_to;
END;
$$;


-- Puntaje de 0 a 100 a partir de puntos de penalización y km (D-19). Una sola definición
-- en SQL, usada por la vista materializada y por el ranking del dashboard; replica
-- rules.driver_score (Python) y un test de integración verifica que coinciden.
CREATE OR REPLACE FUNCTION gold.score_value(p_points numeric, p_km numeric)
RETURNS numeric
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT CASE WHEN p_km IS NULL OR p_km < 5 THEN NULL
                ELSE round((100 * exp(-0.005 * p_points / p_km * 100))::numeric, 1)
           END;
$$;

-- Directorio de vehículos para BI: clave, ID enmascarado, tipo y región de operación según su
-- ruta asignada. Permite filtrar por ciudad sin dar acceso a Bronce ni a Plata.
CREATE OR REPLACE VIEW gold.vehicle_directory AS
SELECT
    v.vehicle_key,
    analytics.mask_vehicle_id(v.vehicle_id) AS vehicle_id_masked,
    v.vehicle_type,
    vr.route_id,
    CASE r.kind
        WHEN 'urban_bogota' THEN 'bogota'
        WHEN 'urban_medellin' THEN 'medellin'
        WHEN 'interurban' THEN 'corridor'
    END AS region
FROM bronze.vehicles AS v
LEFT JOIN silver.vehicle_routes AS vr USING (vehicle_id)
LEFT JOIN silver.routes AS r USING (route_id);

-- Alertas de combustible para BI, con el ID ya enmascarado. Es una vista: se ejecuta con
-- los permisos de su dueño, así que el analista la lee sin acceso a Plata ni a Bronce.
-- `destination` dice hacia dónde iba el vehículo cuando saltó la alerta: el nombre de la ruta
-- es "Origen – Destino" y el sentido sale de la lectura en ese instante.
CREATE OR REPLACE VIEW gold.fuel_alerts AS
SELECT
    a.alert_id, v.vehicle_key, analytics.mask_vehicle_id(v.vehicle_id) AS vehicle_id_masked,
    v.vehicle_type, a.severity, a.started_at, a.ended_at, a.geom,
    (a.details->>'fuel_l')::real AS fuel_l,
    (a.details->>'fuel_needed_l')::real AS fuel_needed_l,
    (a.details->>'remaining_km')::real AS remaining_km,
    a.details->>'route_id' AS route_id,
    split_part(r.name, ' – ', CASE WHEN e.direction = -1 THEN 1 ELSE 2 END) AS destination
FROM silver.alerts AS a
JOIN bronze.vehicles AS v USING (vehicle_id)
LEFT JOIN silver.telemetry_enriched AS e ON e.vehicle_id = a.vehicle_id AND e.ts = a.started_at
LEFT JOIN silver.routes AS r ON r.route_id = a.details->>'route_id'
WHERE a.alert_type = 'LOW_FUEL_FOR_ROUTE';

-- Driver Score diario por vehículo (D-19). Pequeña (vehículos × días): se recalcula entera.
-- Los pesos replican processing/rules.py (ScoreParams); un test de integración verifica que
-- ambos dan el mismo puntaje.
CREATE MATERIALIZED VIEW IF NOT EXISTS gold.driver_score AS
WITH daily AS (
    SELECT vehicle_key, vehicle_id_masked, vehicle_type, event_date,
           sum(step_m) / 1000 AS km,
           count(*) AS readings,
           count(*) FILTER (WHERE is_over_threshold) AS readings_over
    FROM gold.fleet_events
    GROUP BY vehicle_key, vehicle_id_masked, vehicle_type, event_date
), episodes AS (
    SELECT DISTINCT vehicle_key, event_date, alert_id, alert_type, severity
    FROM gold.fleet_events
    WHERE alert_id IS NOT NULL
), counted AS (
    SELECT vehicle_key, event_date,
        count(*) FILTER (WHERE alert_type = 'SPEEDING' AND severity = 'minor') AS speeding_minor,
        count(*) FILTER (WHERE alert_type = 'SPEEDING' AND severity = 'moderate') AS speeding_moderate,
        count(*) FILTER (WHERE alert_type = 'SPEEDING' AND severity = 'severe') AS speeding_severe,
        count(*) FILTER (WHERE alert_type = 'GEOFENCE_VIOLATION' AND severity = 'minor') AS geofence_minor,
        count(*) FILTER (WHERE alert_type = 'GEOFENCE_VIOLATION' AND severity = 'moderate') AS geofence_moderate,
        count(*) FILTER (WHERE alert_type = 'GEOFENCE_VIOLATION' AND severity = 'severe') AS geofence_severe
    FROM episodes
    GROUP BY vehicle_key, event_date
), low_fuel AS (
    SELECT v.vehicle_key, (a.started_at AT TIME ZONE 'America/Bogota')::date AS event_date,
           count(*) AS low_fuel_alerts
    FROM silver.alerts AS a JOIN bronze.vehicles AS v USING (vehicle_id)
    WHERE a.alert_type = 'LOW_FUEL_FOR_ROUTE'
    GROUP BY 1, 2
), scored AS (
    SELECT
        d.*,
        coalesce(c.speeding_minor, 0) AS speeding_minor,
        coalesce(c.speeding_moderate, 0) AS speeding_moderate,
        coalesce(c.speeding_severe, 0) AS speeding_severe,
        coalesce(c.geofence_minor, 0) AS geofence_minor,
        coalesce(c.geofence_moderate, 0) AS geofence_moderate,
        coalesce(c.geofence_severe, 0) AS geofence_severe,
        coalesce(f.low_fuel_alerts, 0) AS low_fuel_alerts,
        -- SPEEDING leve/moderado/grave = 1/3/6; GEOFENCE_VIOLATION vale el doble.
        coalesce(c.speeding_minor, 0) * 1 + coalesce(c.speeding_moderate, 0) * 3
            + coalesce(c.speeding_severe, 0) * 6
            + 2 * (coalesce(c.geofence_minor, 0) * 1 + coalesce(c.geofence_moderate, 0) * 3
                   + coalesce(c.geofence_severe, 0) * 6) AS penalty_points
    FROM daily AS d
    LEFT JOIN counted AS c USING (vehicle_key, event_date)
    LEFT JOIN low_fuel AS f USING (vehicle_key, event_date)
)
SELECT
    s.*,
    round((s.penalty_points / nullif(s.km, 0) * 100)::numeric, 1) AS points_per_100km,
    gold.score_value(s.penalty_points, s.km::numeric) AS score
FROM scored AS s
WITH NO DATA;

-- Índice único: requisito de REFRESH MATERIALIZED VIEW CONCURRENTLY (no bloquea lecturas).
CREATE UNIQUE INDEX IF NOT EXISTS driver_score_pk ON gold.driver_score (vehicle_key, event_date);
REFRESH MATERIALIZED VIEW gold.driver_score;

-- Permisos: Oro no tiene IDs reales, así que el analista lo lee directamente.
GRANT USAGE ON SCHEMA gold TO bi_analyst;
GRANT SELECT ON ALL TABLES IN SCHEMA gold TO bi_analyst, bi_admin;
ALTER DEFAULT PRIVILEGES IN SCHEMA gold GRANT SELECT ON TABLES TO bi_analyst;
