# Benchmarks

Resultados reales generados por `scripts/benchmark.py` (no editar a mano). Cada sección corresponde a una corrida.

### 5 078 880 filas

- Fecha: 2026-11-20 21:03 · PostgreSQL 16.9 · PostGIS 3.5.2 · Docker en Windows 11 (portátil)
- Tabla: 785 MB · carga sin índices: 22.0 s
- Mediana de 5 ejecuciones de `EXPLAIN ANALYZE`, tras 1 de calentamiento

| Consulta | Pregunta de negocio | Sin índices | Con índices | Mejora | Plan con índices |
|---|---|---:|---:|---:|---|
| Q1 Heatmap | Infracciones en un rectángulo del centro de Bogotá (~5 × 5 km) durante 7 días | 208.2 ms | 110.5 ms | 1,9× | `Bitmap Heap Scan → BitmapAnd → Bitmap Index Scan (bench_ts_brin) → Bitmap Index Scan (bench_alerts_gist)` |
| Q2 Ruta | La ruta de un vehículo en un día, ordenada en el tiempo | 178.2 ms | 3.0 ms | 59× | `Bitmap Heap Scan → Bitmap Index Scan (bench_vehicle_ts)` |
| Q3 Geocerca | Cuántos eventos ocurrieron dentro de la zona escolar BOG-SCH-01 (ST_Within) | 3 271.1 ms | 81.4 ms | 40× | `Nested Loop → Index Scan (geofences_code_key) → Bitmap Heap Scan → Bitmap Index Scan (bench_geom_gist)` |
| Q4 Vecinos | Los 10 eventos más cercanos a la Plaza de Bolívar (KNN con <->) | 860.8 ms | 0.1 ms | 6 329× | `Index Scan (bench_geom_gist)` |

| Índice | Tiempo de creación | Tamaño |
|---|---:|---:|
| GiST en geom | 4.9 s | 200 MB |
| GiST parcial (solo alertas) | 0.8 s | 14 MB |
| BRIN en ts | 0.5 s | 40 kB |
| B-tree (vehicle_key, ts) | 2.3 s | 153 MB |

### 1 015 776 filas

- Fecha: 2026-11-20 21:02 · PostgreSQL 16.9 · PostGIS 3.5.2 · Docker en Windows 11 (portátil)
- Tabla: 157 MB · carga sin índices: 4.1 s
- Mediana de 5 ejecuciones de `EXPLAIN ANALYZE`, tras 1 de calentamiento

| Consulta | Pregunta de negocio | Sin índices | Con índices | Mejora | Plan con índices |
|---|---|---:|---:|---:|---|
| Q1 Heatmap | Infracciones en un rectángulo del centro de Bogotá (~5 × 5 km) durante 7 días | 49.6 ms | 119.8 ms | **más lenta** (0,4×) | `Bitmap Heap Scan → BitmapAnd → Bitmap Index Scan (bench_ts_brin) → Bitmap Index Scan (bench_alerts_gist)` |
| Q2 Ruta | La ruta de un vehículo en un día, ordenada en el tiempo | 44.3 ms | 2.7 ms | 16× | `Bitmap Heap Scan → Bitmap Index Scan (bench_vehicle_ts)` |
| Q3 Geocerca | Cuántos eventos ocurrieron dentro de la zona escolar BOG-SCH-01 (ST_Within) | 1 051.1 ms | 9.1 ms | 115× | `Nested Loop → Index Scan (geofences_code_key) → Bitmap Heap Scan → Bitmap Index Scan (bench_geom_gist)` |
| Q4 Vecinos | Los 10 eventos más cercanos a la Plaza de Bolívar (KNN con <->) | 203.4 ms | 0.1 ms | 1 956× | `Index Scan (bench_geom_gist)` |

| Índice | Tiempo de creación | Tamaño |
|---|---:|---:|
| GiST en geom | 1.6 s | 40 MB |
| GiST parcial (solo alertas) | 0.2 s | 2928 kB |
| BRIN en ts | 0.1 s | 24 kB |
| B-tree (vehicle_key, ts) | 0.5 s | 31 MB |
