-- Procesamiento incremental Bronce -> Plata (D-17).
--
-- Uso: SELECT * FROM silver.process_new();   (los umbrales llegan desde Python: RuleParams)
--
-- Pasos:
--   1. Marca de agua: qué llegó a Bronce desde la última ejecución (received_at), con solape.
--   2. Contexto: para cada vehículo afectado, sus lecturas desde un poco antes (p_lookback),
--      porque distancias, consumo y episodios necesitan las lecturas anteriores.
--   3. Geocerca aplicada: cruce punto-polígono con ST_Within sobre las piezas con GiST;
--      gana la más restrictiva.
--   4. Distancias, recargas, consumo real y km restantes de la ruta (window functions).
--   5. Upsert en silver.telemetry_enriched (solo lecturas nuevas; el contexto no se reescribe).
--   6. Alertas de velocidad por episodio y alertas de combustible, con upsert idempotente.
--   7. Avanzar la marca de agua.

CREATE OR REPLACE FUNCTION silver.process_new(
    p_default_limit_kmh          integer          DEFAULT 90,
    p_tolerance_kmh              double precision DEFAULT 5,
    p_min_consecutive            integer          DEFAULT 2,
    p_max_gap                    interval         DEFAULT '30 seconds',
    p_minor_max_excess           double precision DEFAULT 0.20,
    p_moderate_max_excess        double precision DEFAULT 0.50,
    p_fuel_window_m              double precision DEFAULT 20000,
    p_min_fuel_window_m          double precision DEFAULT 5000,
    p_min_step_m                 double precision DEFAULT 15,
    p_refuel_jump_pct            double precision DEFAULT 5,
    p_fuel_safety_factor         double precision DEFAULT 1.15,
    p_low_fuel_severe_coverage   double precision DEFAULT 0.5,
    p_low_fuel_moderate_coverage double precision DEFAULT 0.8,
    p_overlap                    interval         DEFAULT '5 minutes',
    p_lookback                   interval         DEFAULT '3 hours'
)
RETURNS TABLE (readings_enriched bigint, alerts_upserted bigint, new_watermark timestamptz)
LANGUAGE plpgsql
AS $$
DECLARE
    v_from     timestamptz;
    v_to       timestamptz;
    v_min_ts   timestamptz;
    v_max_ts   timestamptz;
    v_rows     bigint := 0;
    v_alerts   bigint := 0;
    v_count    bigint;
    v_island   record;
BEGIN
    DROP TABLE IF EXISTS _affected, _zoned, _steps, _cum, _smooth, _calc;

    -- 1. Marca de agua (FOR UPDATE: dos ejecuciones simultáneas no procesan lo mismo).
    SELECT s.watermark INTO v_from
    FROM silver.processing_state AS s WHERE s.job = 'silver' FOR UPDATE;
    IF NOT FOUND THEN
        v_from := '-infinity';
        INSERT INTO silver.processing_state (job, watermark) VALUES ('silver', v_from);
    END IF;

    SELECT max(received_at) INTO v_to
    FROM bronze.telemetry_raw WHERE received_at > v_from - p_overlap;
    IF v_to IS NULL THEN
        RETURN QUERY SELECT 0::bigint, 0::bigint, v_from;
        RETURN;
    END IF;

    CREATE TEMP TABLE _affected ON COMMIT DROP AS
    SELECT vehicle_id, min(ts) AS min_ts
    FROM bronze.telemetry_raw
    WHERE received_at > v_from - p_overlap AND received_at <= v_to
    GROUP BY vehicle_id;

    SELECT min(min_ts) - p_lookback INTO v_min_ts FROM _affected;
    SELECT max(ts) INTO v_max_ts
    FROM bronze.telemetry_raw WHERE received_at > v_from - p_overlap AND received_at <= v_to;
    PERFORM public.ensure_daily_partitions(
        'silver.telemetry_enriched',
        (v_min_ts AT TIME ZONE 'America/Bogota')::date,
        (v_max_ts AT TIME ZONE 'America/Bogota')::date
    );

    -- 2 y 3. Lecturas con contexto y geocerca aplicada.
    CREATE TEMP TABLE _zoned ON COMMIT DROP AS
    SELECT
        t.vehicle_id, t.ts, t.geom, t.quality_flags,
        t.speed_kmh::real AS speed_kmh,
        t.fuel_pct::real AS fuel_pct,
        (t.fuel_pct / 100 * v.tank_capacity_l)::real AS fuel_l,
        v.ref_consumption_l_100km::real AS ref_l_100km,
        t.ts >= a.min_ts AS is_new,
        z.geofence_id, z.code AS zone_code, z.zone_type,
        coalesce(z.speed_limit_kmh, p_default_limit_kmh)::smallint AS speed_limit_kmh
    FROM bronze.telemetry_raw AS t
    JOIN _affected AS a USING (vehicle_id)
    JOIN bronze.vehicles AS v USING (vehicle_id)
    LEFT JOIN LATERAL (
        -- El GiST de las piezas descarta casi todas las geocercas por rectángulo envolvente;
        -- ST_Within hace la prueba exacta solo con las candidatas.
        SELECT g.geofence_id, g.code, g.zone_type, g.speed_limit_kmh
        FROM silver.geofence_parts AS p
        JOIN silver.geofences AS g USING (geofence_id)
        WHERE ST_Within(t.geom, p.geom)
        ORDER BY g.speed_limit_kmh,
                 CASE g.zone_type WHEN 'school' THEN 0 WHEN 'high_risk' THEN 1
                      WHEN 'road_hazard' THEN 2 WHEN 'urban' THEN 3 WHEN 'highway' THEN 4
                      ELSE 99 END,
                 g.code
        LIMIT 1
    ) AS z ON true
    WHERE t.ts >= v_min_ts            -- límite global: permite descartar particiones
      AND t.ts >= a.min_ts - p_lookback;

    -- 4a. Distancia efectiva, recargas y posición sobre la ruta.
    CREATE TEMP TABLE _steps ON COMMIT DROP AS
    SELECT
        z.*,
        CASE WHEN z.speed_kmh = 0
                  OR coalesce(ST_Distance(z.geom::geography, (lag(z.geom) OVER w)::geography), 0)
                     < p_min_step_m
             THEN 0
             ELSE ST_Distance(z.geom::geography, (lag(z.geom) OVER w)::geography)
        END::real AS step_m,
        (z.fuel_pct - lag(z.fuel_pct) OVER w > p_refuel_jump_pct) AS is_refuel,
        vr.route_id,
        r.length_m,
        ST_LineLocatePoint(r.geom_9377, ST_Transform(z.geom, 9377))::real AS route_fraction
    FROM _zoned AS z
    LEFT JOIN silver.vehicle_routes AS vr USING (vehicle_id)
    LEFT JOIN silver.routes AS r USING (route_id)
    WINDOW w AS (PARTITION BY z.vehicle_id ORDER BY z.ts);

    -- 4b. Distancia acumulada, tramo entre recargas y sentido del viaje.
    CREATE TEMP TABLE _cum ON COMMIT DROP AS
    SELECT
        s.*,
        sum(s.step_m) OVER w AS cum_m,
        sum(coalesce(s.is_refuel, false)::int) OVER w AS refuel_seq,
        -- Sentido solo cuando el avance sobre la ruta es mayor a 5 m (si no, es ruido).
        CASE WHEN abs(s.route_fraction - lag(s.route_fraction) OVER w) * s.length_m >= 5
             THEN sign(s.route_fraction - lag(s.route_fraction) OVER w)::smallint
        END AS raw_direction
    FROM _steps AS s
    WINDOW w AS (PARTITION BY s.vehicle_id ORDER BY s.ts);

    -- 4c. Combustible suavizado (promedio de 6 lecturas dentro del mismo tramo sin recarga)
    --     y sentido arrastrado desde la última lectura en que se pudo medir.
    CREATE TEMP TABLE _smooth ON COMMIT DROP AS
    SELECT
        c.*,
        avg(c.fuel_l) OVER (PARTITION BY c.vehicle_id, c.refuel_seq ORDER BY c.ts
                            ROWS BETWEEN 5 PRECEDING AND CURRENT ROW)::real AS fuel_smooth_l,
        count(c.raw_direction) OVER (PARTITION BY c.vehicle_id ORDER BY c.ts) AS direction_grp
    FROM _cum AS c;

    -- 4d. Consumo real en los últimos p_fuel_window_m metros y predicción de autonomía.
    CREATE TEMP TABLE _calc ON COMMIT DROP AS
    WITH windowed AS (
        SELECT
            s.*,
            first_value(s.raw_direction) OVER (PARTITION BY s.vehicle_id, s.direction_grp
                                               ORDER BY s.ts) AS direction,
            first_value(s.fuel_smooth_l) OVER fw - s.fuel_smooth_l AS fuel_used_l,
            s.cum_m - first_value(s.cum_m) OVER fw AS window_m
        FROM _smooth AS s
        WINDOW fw AS (PARTITION BY s.vehicle_id, s.refuel_seq ORDER BY s.cum_m
                      RANGE BETWEEN p_fuel_window_m PRECEDING AND CURRENT ROW)
    ), predicted AS (
        SELECT
            w.*,
            CASE WHEN w.window_m >= p_min_fuel_window_m AND w.fuel_used_l > 0
                 THEN w.fuel_used_l / (w.window_m / 1000)
                 ELSE w.ref_l_100km / 100
            END AS consumption_l_km,
            CASE w.direction
                WHEN 1 THEN (1 - w.route_fraction) * w.length_m / 1000
                WHEN -1 THEN w.route_fraction * w.length_m / 1000
            END AS remaining_km
        FROM windowed AS w
    )
    SELECT
        p.*,
        (p.remaining_km * p.consumption_l_km * p_fuel_safety_factor)::real AS fuel_needed_l,
        p.speed_kmh > p.speed_limit_kmh + p_tolerance_kmh AS is_over_threshold
    FROM predicted AS p;

    -- 5. Upsert de las lecturas nuevas.
    INSERT INTO silver.telemetry_enriched AS e (
        vehicle_id, ts, geom, speed_kmh, fuel_pct, fuel_l, geofence_id, zone_type,
        speed_limit_kmh, is_over_threshold, step_m, route_id, route_fraction, direction,
        remaining_km, consumption_l_100km, fuel_needed_l, is_low_fuel, quality_flags
    )
    SELECT
        c.vehicle_id, c.ts, c.geom, c.speed_kmh, c.fuel_pct, c.fuel_l, c.geofence_id, c.zone_type,
        c.speed_limit_kmh, c.is_over_threshold, c.step_m, c.route_id, c.route_fraction,
        c.direction, c.remaining_km, (c.consumption_l_km * 100)::real, c.fuel_needed_l,
        c.fuel_smooth_l < c.fuel_needed_l, c.quality_flags
    FROM _calc AS c
    WHERE c.is_new
    ON CONFLICT (vehicle_id, ts) DO UPDATE SET
        geofence_id = EXCLUDED.geofence_id, zone_type = EXCLUDED.zone_type,
        speed_limit_kmh = EXCLUDED.speed_limit_kmh,
        is_over_threshold = EXCLUDED.is_over_threshold, step_m = EXCLUDED.step_m,
        route_id = EXCLUDED.route_id, route_fraction = EXCLUDED.route_fraction,
        direction = EXCLUDED.direction, remaining_km = EXCLUDED.remaining_km,
        consumption_l_100km = EXCLUDED.consumption_l_100km,
        fuel_needed_l = EXCLUDED.fuel_needed_l, is_low_fuel = EXCLUDED.is_low_fuel,
        processed_at = now();
    GET DIAGNOSTICS v_rows = ROW_COUNT;

    -- 6a. Episodios de exceso: lecturas consecutivas sobre el umbral, en la misma geocerca
    --     y sin pausas mayores a p_max_gap. Técnica "gaps and islands".
    WITH flagged AS (
        SELECT
            c.*,
            c.is_over_threshold AND (
                lag(c.is_over_threshold) OVER w IS NOT TRUE
                OR c.zone_code IS DISTINCT FROM lag(c.zone_code) OVER w
                OR c.ts - lag(c.ts) OVER w > p_max_gap
            ) AS starts_episode,
            min(c.ts) OVER (PARTITION BY c.vehicle_id) AS context_start
        FROM _calc AS c
        WINDOW w AS (PARTITION BY c.vehicle_id ORDER BY c.ts)
    ), numbered AS (
        SELECT f.*, sum(f.starts_episode::int) OVER (PARTITION BY f.vehicle_id ORDER BY f.ts) AS episode
        FROM flagged AS f
    ), episodes AS (
        SELECT
            n.vehicle_id, n.episode,
            min(n.ts) AS started_at, max(n.ts) AS ended_at, count(*) AS readings,
            max(n.speed_kmh) AS max_speed, min(n.speed_limit_kmh) AS limit_kmh,
            min(n.zone_type) AS zone_type, min(n.zone_code) AS zone_code,
            min(n.geofence_id) AS geofence_id,
            (array_agg(n.geom ORDER BY n.speed_kmh DESC))[1] AS geom,
            bool_or(n.is_new) AS touches_new,
            min(n.context_start) AS context_start
        FROM numbered AS n
        WHERE n.is_over_threshold
        GROUP BY n.vehicle_id, n.episode
        HAVING count(*) >= p_min_consecutive
    )
    INSERT INTO silver.alerts AS al (
        vehicle_id, alert_type, severity, started_at, ended_at, geofence_id, geom, details
    )
    SELECT
        e.vehicle_id,
        CASE WHEN e.zone_type IN ('school', 'high_risk', 'road_hazard')
             THEN 'GEOFENCE_VIOLATION' ELSE 'SPEEDING' END,
        CASE WHEN (e.max_speed - e.limit_kmh) / e.limit_kmh <= p_minor_max_excess THEN 'minor'
             WHEN (e.max_speed - e.limit_kmh) / e.limit_kmh <= p_moderate_max_excess THEN 'moderate'
             ELSE 'severe' END,
        e.started_at, e.ended_at, e.geofence_id, e.geom,
        jsonb_build_object(
            'max_speed_kmh', round(e.max_speed::numeric, 1),
            'limit_kmh', e.limit_kmh,
            'excess_pct', round(((e.max_speed - e.limit_kmh) / e.limit_kmh * 100)::numeric, 1),
            'readings', e.readings,
            'duration_s', extract(epoch FROM e.ended_at - e.started_at)::int,
            'zone_code', e.zone_code,
            'zone_type', e.zone_type
        )
    FROM episodes AS e
    -- Solo episodios que tocan datos nuevos y cuyo inicio real se conoce. Si el episodio
    -- empieza en el borde de la ventana de contexto y hay una lectura justo antes, podría
    -- venir de antes: ese caso ya lo registró una ejecución anterior con más contexto.
    WHERE e.touches_new
      AND (
          e.started_at > e.context_start
          OR NOT EXISTS (
              SELECT 1 FROM bronze.telemetry_raw AS b
              WHERE b.vehicle_id = e.vehicle_id
                AND b.ts < e.context_start
                AND b.ts >= e.context_start - p_max_gap
          )
      )
    ON CONFLICT (vehicle_id, alert_type, started_at) DO UPDATE SET
        severity = EXCLUDED.severity, ended_at = EXCLUDED.ended_at,
        geom = EXCLUDED.geom, details = EXCLUDED.details, updated_at = now();
    GET DIAGNOSTICS v_count = ROW_COUNT;
    v_alerts := v_alerts + v_count;

    -- 6b. Combustible insuficiente: islas de lecturas nuevas con déficit. Si ya hay una
    --     alerta abierta del mismo vehículo que llega hasta el inicio de la isla (la
    --     condición continúa desde el lote anterior), se extiende; si no, se crea.
    FOR v_island IN
        WITH low AS (
            SELECT c.*,
                   c.fuel_smooth_l < c.fuel_needed_l AS low_now,
                   lag(c.fuel_smooth_l < c.fuel_needed_l) OVER w AS low_before
            FROM _calc AS c
            WINDOW w AS (PARTITION BY c.vehicle_id ORDER BY c.ts)
        ), islands AS (
            SELECT l.*, sum((l.low_now AND l.low_before IS NOT TRUE)::int)
                        OVER (PARTITION BY l.vehicle_id ORDER BY l.ts) AS island
            FROM low AS l
        )
        SELECT
            i.vehicle_id, min(i.ts) AS started_at, max(i.ts) AS ended_at,
            (array_agg(i.geom ORDER BY i.ts))[1] AS geom,
            (array_agg(i.fuel_smooth_l ORDER BY i.ts))[1] AS fuel_l,
            (array_agg(i.fuel_needed_l ORDER BY i.ts))[1] AS needed_l,
            (array_agg(i.remaining_km ORDER BY i.ts))[1] AS remaining_km,
            (array_agg(i.consumption_l_km ORDER BY i.ts))[1] AS consumption_l_km,
            min(i.route_id) AS route_id
        FROM islands AS i
        WHERE i.low_now AND i.is_new
        GROUP BY i.vehicle_id, i.island
        -- Igual que en velocidad: una sola lectura bajo el umbral puede ser ruido del sensor.
        HAVING count(*) >= p_min_consecutive
    LOOP
        UPDATE silver.alerts AS al
        SET ended_at = greatest(al.ended_at, v_island.ended_at), updated_at = now()
        WHERE al.vehicle_id = v_island.vehicle_id
          AND al.alert_type = 'LOW_FUEL_FOR_ROUTE'
          AND al.ended_at >= v_island.started_at - p_lookback
          AND al.started_at <= v_island.started_at;
        IF NOT FOUND THEN
            INSERT INTO silver.alerts (
                vehicle_id, alert_type, severity, started_at, ended_at, geom, details
            ) VALUES (
                v_island.vehicle_id, 'LOW_FUEL_FOR_ROUTE',
                CASE WHEN v_island.fuel_l / v_island.needed_l < p_low_fuel_severe_coverage
                         THEN 'severe'
                     WHEN v_island.fuel_l / v_island.needed_l < p_low_fuel_moderate_coverage
                         THEN 'moderate'
                     ELSE 'minor' END,
                v_island.started_at, v_island.ended_at, v_island.geom,
                jsonb_build_object(
                    'fuel_l', round(v_island.fuel_l::numeric, 1),
                    'fuel_needed_l', round(v_island.needed_l::numeric, 1),
                    'remaining_km', round(v_island.remaining_km::numeric, 1),
                    'consumption_l_100km', round((v_island.consumption_l_km * 100)::numeric, 1),
                    'route_id', v_island.route_id
                )
            )
            ON CONFLICT (vehicle_id, alert_type, started_at) DO NOTHING;
        END IF;
        v_alerts := v_alerts + 1;
    END LOOP;

    -- 7. Avanzar la marca de agua.
    UPDATE silver.processing_state SET watermark = v_to, run_at = now() WHERE job = 'silver';

    RETURN QUERY SELECT v_rows, v_alerts, v_to;
END;
$$;
