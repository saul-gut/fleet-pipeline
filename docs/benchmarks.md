# Benchmarks

Resultados reales generados por `scripts/benchmark.py` (no editar a mano). Cada sección corresponde a una corrida.

### 5 078 880 filas

- Fecha: 2026-11-20 21:13 · PostgreSQL 16.9 · PostGIS 3.5.2 · Docker en Windows 11 (portátil)
- Tabla: 785 MB · carga sin índices: 19.2 s · versión particionada: 46 particiones diarias
- Mediana de 5 ejecuciones de `EXPLAIN ANALYZE`, tras 1 de calentamiento

| Consulta | Pregunta de negocio | Sin índices | Con índices | Particionada + índices | Mejor vs. sin índices |
|---|---|---:|---:|---:|---:|
| Q1 Heatmap | Infracciones en un rectángulo del centro de Bogotá (~5 × 5 km) durante 7 días | 189.2 ms | 117.0 ms | 24.4 ms | 7,8× |
| Q2 Ruta | La ruta de un vehículo en un día, ordenada en el tiempo | 151.1 ms | 3.0 ms | 3.3 ms | 50× |
| Q3 Geocerca | Cuántos eventos ocurrieron dentro de la zona escolar BOG-SCH-01 (ST_Within) | 3 227.7 ms | 83.8 ms | 88.4 ms | 39× |
| Q4 Vecinos | Los 10 eventos más cercanos a la Plaza de Bolívar (KNN con <->) | 817.8 ms | 0.1 ms | 3.5 ms | 5 602× |

Planes de ejecución:

| Consulta | Con índices | Particionada + índices |
|---|---|---|
| Q1 Heatmap | `Bitmap Heap Scan → BitmapAnd → Bitmap Index Scan (bench_fleet_events_ts) → Bitmap Index Scan (bench_fleet_events_alerts)` | `Append (8 particiones) → Bitmap Heap Scan → Bitmap Index Scan (fleet_events_part_20261119_geom_idx1)` |
| Q2 Ruta | `Bitmap Heap Scan → Bitmap Index Scan (bench_fleet_events_vehicle_ts)` | `Append (2 particiones) → Bitmap Heap Scan → Bitmap Index Scan (fleet_events_part_20261122_vehicle_key_ts_idx)` |
| Q3 Geocerca | `Nested Loop → Index Scan (geofences_code_key) → Bitmap Heap Scan → Bitmap Index Scan (bench_fleet_events_geom)` | `Nested Loop → Index Scan (geofences_code_key) → Append (46 particiones) → Index Scan (fleet_events_part_20261119_geom_idx)` |
| Q4 Vecinos | `Index Scan (bench_fleet_events_geom)` | `Merge Append (46 particiones) → Index Scan (fleet_events_part_20261119_geom_idx)` |

| Índice | Tiempo de creación | Tamaño |
|---|---:|---:|
| GiST en geom | 4.5 s | 200 MB |
| GiST parcial (solo alertas) | 0.8 s | 14 MB |
| BRIN en ts | 0.5 s | 40 kB |
| B-tree (vehicle_key, ts) | 2.0 s | 153 MB |

### 1 015 776 filas

- Fecha: 2026-11-20 21:12 · PostgreSQL 16.9 · PostGIS 3.5.2 · Docker en Windows 11 (portátil)
- Tabla: 157 MB · carga sin índices: 3.7 s · versión particionada: 10 particiones diarias
- Mediana de 5 ejecuciones de `EXPLAIN ANALYZE`, tras 1 de calentamiento

| Consulta | Pregunta de negocio | Sin índices | Con índices | Particionada + índices | Mejor vs. sin índices |
|---|---|---:|---:|---:|---:|
| Q1 Heatmap | Infracciones en un rectángulo del centro de Bogotá (~5 × 5 km) durante 7 días | 57.7 ms | 101.6 ms | 25.2 ms | 2,3× |
| Q2 Ruta | La ruta de un vehículo en un día, ordenada en el tiempo | 41.4 ms | 2.8 ms | 3.4 ms | 15× |
| Q3 Geocerca | Cuántos eventos ocurrieron dentro de la zona escolar BOG-SCH-01 (ST_Within) | 808.3 ms | 8.8 ms | 9.5 ms | 92× |
| Q4 Vecinos | Los 10 eventos más cercanos a la Plaza de Bolívar (KNN con <->) | 156.0 ms | 0.1 ms | 0.8 ms | 1 300× |

Planes de ejecución:

| Consulta | Con índices | Particionada + índices |
|---|---|---|
| Q1 Heatmap | `Bitmap Heap Scan → BitmapAnd → Bitmap Index Scan (bench_fleet_events_ts) → Bitmap Index Scan (bench_fleet_events_alerts)` | `Append (8 particiones) → Bitmap Heap Scan → Bitmap Index Scan (fleet_events_part_20261119_geom_idx1)` |
| Q2 Ruta | `Bitmap Heap Scan → Bitmap Index Scan (bench_fleet_events_vehicle_ts)` | `Append (2 particiones) → Bitmap Heap Scan → Bitmap Index Scan (fleet_events_part_20261122_vehicle_key_ts_idx)` |
| Q3 Geocerca | `Nested Loop → Index Scan (geofences_code_key) → Bitmap Heap Scan → Bitmap Index Scan (bench_fleet_events_geom)` | `Nested Loop → Index Scan (geofences_code_key) → Append (10 particiones) → Index Scan (fleet_events_part_20261119_geom_idx)` |
| Q4 Vecinos | `Index Scan (bench_fleet_events_geom)` | `Merge Append (10 particiones) → Index Scan (fleet_events_part_20261119_geom_idx)` |

| Índice | Tiempo de creación | Tamaño |
|---|---:|---:|
| GiST en geom | 1.0 s | 40 MB |
| GiST parcial (solo alertas) | 0.2 s | 2928 kB |
| BRIN en ts | 0.1 s | 24 kB |
| B-tree (vehicle_key, ts) | 0.4 s | 31 MB |
