-- Una capa por esquema: los permisos se asignan por esquema (mínimo privilegio).
CREATE SCHEMA IF NOT EXISTS bronze;  -- dato crudo, append-only, contiene el ID real
CREATE SCHEMA IF NOT EXISTS silver;  -- dato enriquecido: geocercas, alertas
CREATE SCHEMA IF NOT EXISTS gold;    -- modelo para BI
