-- Capa Plata: geocercas (D-06, D-15).
-- Se cargan desde data/geofences/geofences.geojson con: python -m fleet_pipeline.geofences load

CREATE TABLE IF NOT EXISTS silver.geofences (
    geofence_id     integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code            text NOT NULL UNIQUE,
    name            text NOT NULL,
    city            text NOT NULL CHECK (city IN ('bogota', 'medellin', 'corridor')),
    zone_type       text NOT NULL
                    CHECK (zone_type IN ('urban', 'school', 'high_risk', 'road_hazard', 'highway')),
    speed_limit_kmh smallint NOT NULL CHECK (speed_limit_kmh BETWEEN 5 AND 130),
    -- 'law': límite legal (Ley 2251 de 2022). 'fleet_policy': política más estricta de la flota.
    limit_source    text NOT NULL CHECK (limit_source IN ('law', 'fleet_policy')),
    source          text NOT NULL,
    geom            geometry(MultiPolygon, 4326) NOT NULL CHECK (ST_IsValid(geom)),
    loaded_at       timestamptz NOT NULL DEFAULT now()
);

-- GiST: permite que el cruce punto-polígono descarte en microsegundos las geocercas cuyo
-- rectángulo envolvente no contiene el punto, antes de la prueba geométrica exacta.
CREATE INDEX IF NOT EXISTS geofences_geom_gist ON silver.geofences USING gist (geom);

-- Las geocercas no son datos sensibles: el analista las ve completas.
CREATE OR REPLACE VIEW analytics.geofences AS
SELECT geofence_id, code, name, city, zone_type, speed_limit_kmh, limit_source, source, geom
FROM silver.geofences;
