# Fuentes de las geocercas

Los archivos originales se guardan en `data/raw/` y no se suben al repositorio. Los GeoJSON de esta carpeta son derivados: están reproyectados a EPSG:4326 y simplificados con `scripts/prepare_geofences.py`.

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
