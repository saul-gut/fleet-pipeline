-- Seguridad: roles con mínimo privilegio y enmascaramiento del ID del vehículo (D-13).
--
-- Roles (NOLOGIN: nadie se conecta con ellos directamente; la aplicación los asume con
-- SET ROLE según quién hace la petición):
--   fleet_ingest  inserta telemetría en Bronce. Sin UPDATE ni DELETE: Bronce es append-only.
--   bi_admin      lee todas las capas, incluido el ID real.
--   bi_analyst    solo lee el esquema analytics, donde todo está enmascarado.

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'fleet_ingest') THEN
        CREATE ROLE fleet_ingest NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'bi_admin') THEN
        CREATE ROLE bi_admin NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'bi_analyst') THEN
        CREATE ROLE bi_analyst NOLOGIN;
    END IF;
END;
$$;

-- El usuario de la aplicación puede asumir los tres roles.
GRANT fleet_ingest, bi_admin, bi_analyst TO CURRENT_USER;

CREATE SCHEMA IF NOT EXISTS analytics;

-- ---------------------------------------------------------------------------------------
-- Enmascaramiento
-- ---------------------------------------------------------------------------------------

-- VHC-8472-ABC -> VHC-****-ABC. A prueba de fallos: cualquier valor con un formato
-- inesperado se devuelve totalmente oculto, nunca tal cual.
CREATE OR REPLACE FUNCTION analytics.mask_vehicle_id(p_vehicle_id text)
RETURNS text
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT CASE
        WHEN p_vehicle_id ~ '^VHC-[0-9]{4}-[A-Z]{3}$'
            THEN overlay(p_vehicle_id PLACING '****' FROM 5 FOR 4)
        ELSE '****'
    END;
$$;

-- ---------------------------------------------------------------------------------------
-- Vistas
-- Las vistas se ejecutan con los permisos de su dueño, así que el analista lee a través de
-- ellas sin tener permiso sobre las tablas de Bronce.
-- ---------------------------------------------------------------------------------------

-- Última posición de cada vehículo en las últimas 24 h, con el ID real (solo bi_admin).
-- El filtro de tiempo permite descartar particiones antiguas.
CREATE OR REPLACE VIEW bronze.v_latest_positions AS
SELECT DISTINCT ON (t.vehicle_id)
    v.vehicle_key,
    t.vehicle_id,
    v.vehicle_type,
    t.ts,
    t.lat,
    t.lon,
    t.speed_kmh,
    t.fuel_pct,
    round(t.fuel_pct / 100 * v.tank_capacity_l, 1) AS fuel_l
FROM bronze.telemetry_raw AS t
JOIN bronze.vehicles AS v USING (vehicle_id)
WHERE t.ts >= now() - interval '24 hours'
ORDER BY t.vehicle_id, t.ts DESC;

-- Misma vista para el analista: se construye sobre la anterior, así la lógica no se duplica,
-- y solo cambia la columna del ID.
CREATE OR REPLACE VIEW analytics.latest_positions AS
SELECT
    vehicle_key,
    analytics.mask_vehicle_id(vehicle_id) AS vehicle_id,
    vehicle_type,
    ts,
    lat,
    lon,
    speed_kmh,
    fuel_pct,
    fuel_l
FROM bronze.v_latest_positions;

-- Catálogo de vehículos enmascarado.
CREATE OR REPLACE VIEW analytics.vehicles AS
SELECT
    vehicle_key,
    analytics.mask_vehicle_id(vehicle_id) AS vehicle_id,
    vehicle_type,
    tank_capacity_l,
    ref_consumption_l_100km
FROM bronze.vehicles;

-- ---------------------------------------------------------------------------------------
-- Permisos
-- ---------------------------------------------------------------------------------------

-- Ingesta: lee el catálogo y la lectura anterior, e inserta. Nada más.
GRANT USAGE ON SCHEMA bronze TO fleet_ingest;
GRANT SELECT ON bronze.vehicles TO fleet_ingest;
GRANT SELECT, INSERT ON bronze.telemetry_raw, bronze.telemetry_rejected TO fleet_ingest;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA bronze TO fleet_ingest;

-- Admin: lectura de todas las capas, incluidas las tablas que se creen en el futuro.
GRANT USAGE ON SCHEMA bronze, silver, gold, analytics TO bi_admin;
GRANT SELECT ON ALL TABLES IN SCHEMA bronze, silver, gold, analytics TO bi_admin;
ALTER DEFAULT PRIVILEGES IN SCHEMA bronze, silver, gold, analytics
    GRANT SELECT ON TABLES TO bi_admin;

-- Analista: únicamente el esquema analytics.
GRANT USAGE ON SCHEMA analytics TO bi_analyst;
GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO bi_analyst;
ALTER DEFAULT PRIVILEGES IN SCHEMA analytics GRANT SELECT ON TABLES TO bi_analyst;
