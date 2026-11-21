-- Una capa por esquema: los permisos se asignan por esquema (mínimo privilegio).
CREATE SCHEMA IF NOT EXISTS bronze;  -- dato crudo, append-only, contiene el ID real
CREATE SCHEMA IF NOT EXISTS silver;  -- dato enriquecido: geocercas, alertas
CREATE SCHEMA IF NOT EXISTS gold;    -- modelo para BI

-- Crea las particiones diarias faltantes de una tabla particionada por rango de tiempo,
-- entre dos fechas (ambas incluidas). Idempotente. Devuelve cuántas particiones creó.
-- Nombre de cada partición: <tabla>_AAAAMMDD, en el mismo esquema de la tabla padre.
-- Los cortes siguen el día de negocio de Colombia (D-10): de medianoche a medianoche en
-- America/Bogota, sin importar la zona horaria de la sesión. Colombia no tiene horario de
-- verano, así que todos los días duran 24 h.
CREATE OR REPLACE FUNCTION public.ensure_daily_partitions(
    p_parent regclass, p_from date, p_to date
)
RETURNS integer
LANGUAGE plpgsql
AS $$
DECLARE
    v_schema  text;
    v_table   text;
    v_day     date;
    v_name    text;
    v_created integer := 0;
BEGIN
    IF p_from > p_to THEN
        RAISE EXCEPTION 'p_from (%) no puede ser posterior a p_to (%)', p_from, p_to;
    END IF;

    SELECT n.nspname, c.relname INTO v_schema, v_table
    FROM pg_class AS c JOIN pg_namespace AS n ON n.oid = c.relnamespace
    WHERE c.oid = p_parent;

    FOR v_day IN SELECT generate_series(p_from, p_to, interval '1 day')::date LOOP
        v_name := format('%s_%s', v_table, to_char(v_day, 'YYYYMMDD'));
        IF to_regclass(format('%I.%I', v_schema, v_name)) IS NULL THEN
            -- %I y %L escapan identificadores y literales: no hay SQL concatenado a mano.
            EXECUTE format(
                'CREATE TABLE %I.%I PARTITION OF %I.%I FOR VALUES FROM (%L) TO (%L)',
                v_schema, v_name, v_schema, v_table,
                v_day::timestamp AT TIME ZONE 'America/Bogota',
                (v_day + 1)::timestamp AT TIME ZONE 'America/Bogota'
            );
            v_created := v_created + 1;
        END IF;
    END LOOP;

    RETURN v_created;
END;
$$;
