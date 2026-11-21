# Fuentes de las geocercas

Los archivos originales se descargan con `scripts/download_sources.py` en `data/raw/`, que no se sube al repositorio. `geofences.geojson` es un derivado generado con `scripts/prepare_geofences.py`: reproyectado a EPSG:4326, simplificado a 5 m y con las operaciones métricas hechas en EPSG:9377.

**Licencia del derivado:** las zonas escolares de Medellín derivan de datos CC BY-SA 4.0, así que se publican con la misma licencia. Las demás geocercas citan su fuente en el campo `source`.

## Resumen de geocercas

| Tipo | Código | Límite | Origen del límite | Fuente |
|---|---|---|---|---|
| Perímetro urbano | `BOG-URB`, `MED-URB` | 50 km/h | Ley 2251 de 2022 | Perímetros oficiales |
| Zona escolar | `*-SCH-01..05` | 30 km/h | Ley 2251 de 2022 | DBSCAN sobre colegios + buffer de 100 m |
| Hotspot de siniestralidad | `*-HR-01..05` | 40 km/h | Política de la flota | DBSCAN sobre siniestros + buffer de 50 m |
| Autopista | `COR-HWY` | 90 km/h | Res. MinTransporte 25995 de 2023 | Ruta OSRM, buffer de 30 m |
| Tramo crítico de Guarne | `COR-GUARNE` | 60 km/h | Política de la flota | ~17 km centrados en Guarne |
| Vía deteriorada El Santuario–Caño Alegre | `COR-SANTUARIO` | 70 km/h | Política de la flota | 135 km por la ruta desde El Santuario hacia Bogotá; termina en (5.8992, -74.6265), cerca de Puerto Triunfo. Caño Alegre no aparece en Nominatim, así que el extremo se asume por distancia |

## Siniestros viales

| Ciudad | Fuente | Filtro | Puntos | Parámetros DBSCAN |
|---|---|---|---|---|
| Bogotá | SIGAT, Secretaría Distrital de Movilidad (`Accidentalidad/AccidentalidadAnalisis`, capa MUERTO) | Años 2023–2026 | 2 341 | radio 250 m, mínimo 8 |
| Medellín | Alcaldía de Medellín (`transporte/VM_Accidentes`) | Heridos, 2025 | 30 153, de los que se excluyen 913 (2 coordenadas con más de 150 registros: ubicaciones por defecto de la geocodificación, verificadas en mapa) | radio 75 m, mínimo 100 |

Solo se descargan la geometría y el año (minimización de datos). Los hotspots no son comparables entre ciudades: Bogotá publica muertos y Medellín heridos. **Licencia de los siniestros de Bogotá: pendiente de verificar.** Solo se publican los polígonos derivados, nunca los registros.

## Colegios

| Ciudad | Fuente | Licencia | Puntos |
|---|---|---|---|
| Bogotá | Datos Abiertos Bogotá, "Colegios Bogotá D.C." v12.24 (recurso `b2e46196-f801-4324-8be3-9e7d54266876`) | CC BY 4.0 | 2 237 |
| Medellín | GeoMedellín, sedes educativas (`educacion/VC_Sedes_Instituciones`, capa 0) | CC BY-SA 4.0 | 779 |

## Perímetro urbano de Bogotá D.C.

| Campo | Valor |
|---|---|
| Entidad | Datos Abiertos Bogotá |
| URL | https://datosabiertos.bogota.gov.co/dataset/perimetro-urbano-bogota-d-c/resource/b71c98ad-535d-4d33-8e8e-e36f1479acd3 |
| Id del recurso | `b71c98ad-535d-4d33-8e8e-e36f1479acd3` |
| Licencia | Creative Commons Attribution 4.0 (CC BY 4.0) |
| Formato original | Shapefile |
| Última actualización de los datos | 2026-08-11 |
| Fecha de descarga | 2026-11-20 |
| Sistema de coordenadas original | MAGNA-SIRGAS Ciudad de Bogotá (`PCS_CarMAGBOG`, Transverse Mercator con elipsoide GRS80 modificado, a = 6 380 687 m) |

## Perímetro urbano de Medellín (POT, Acuerdo 48 de 2014)

| Campo | Valor |
|---|---|
| Entidad | Alcaldía de Medellín, Subsecretaría de Catastro |
| Servicio | https://www.medellin.gov.co/servidormapas/rest/services/vivienda_ciudad_terri/VA_ConsultaOperadorCatastral_geo/MapServer/25 (capa "Perimetro Urbano POT") |
| Licencia | Creative Commons, según el esquema de licenciamiento abierto de GeoMedellín (Circular Conjunta 202660000054 de 2026, Ley 1712 de 2014) |
| Formato de descarga | GeoJSON (consulta `query`, `outSR=4326`) |
| Fecha de adopción | 2014-12-17 |
| Fecha de descarga | 2026-11-20 |
| Sistema de coordenadas del servicio | EPSG:9377 (MAGNA-SIRGAS Origen Nacional), descargado en EPSG:4326 |
| Contenido | 4 polígonos de suelo urbano: el área urbana principal y las cabeceras de Altavista, San Cristóbal y San Antonio de Prado |
| Uso | Se unen los 4 en una sola geocerca (MultiPolygon), porque el POT clasifica las cabeceras como suelo urbano |
