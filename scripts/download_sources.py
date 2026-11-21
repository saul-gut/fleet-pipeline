"""Descarga las fuentes oficiales para construir las geocercas (D-05, D-06) en data/raw/.

data/raw/ no se versiona. Este script permite reconstruir todo desde las fuentes originales.
Uso: uv run python scripts/download_sources.py

Minimización de datos: de los siniestros solo se piden la geometría y el año. Los servicios
también exponen edad y género de las víctimas, que no se necesitan y no se descargan.
"""

from __future__ import annotations

import io
import json
import logging
import zipfile
from dataclasses import dataclass
from pathlib import Path

import httpx

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("download_sources")

RAW = Path(__file__).resolve().parent.parent / "data" / "raw"
PAGE_SIZE = 2000

MEDELLIN = "https://www.medellin.gov.co/servidormapas/rest/services"
BOGOTA_SIMUR = "https://sig.simur.gov.co/arcgis/rest/services"
BOGOTA_CKAN = "https://datosabiertos.bogota.gov.co/api/3/action/resource_show"


@dataclass(frozen=True)
class ArcGisLayer:
    """Capa de un servicio ArcGIS REST que se descarga paginada como GeoJSON en EPSG:4326."""

    output: str
    url: str
    where: str
    out_fields: str


ARCGIS_LAYERS = [
    ArcGisLayer(
        "perimetrourbano_medellin/perimetro_urbano_pot.geojson",
        f"{MEDELLIN}/vivienda_ciudad_terri/VA_ConsultaOperadorCatastral_geo/MapServer/25",
        "1=1",
        "nombre,clase_suelo",
    ),
    ArcGisLayer(
        "siniestros_bogota/muertos.geojson",
        f"{BOGOTA_SIMUR}/Accidentalidad/AccidentalidadAnalisis/FeatureServer/0",
        "ANO_OCURRENCIA_ACC >= 2023",
        "ANO_OCURRENCIA_ACC",
    ),
    ArcGisLayer(
        "siniestros_medellin/heridos.geojson",
        f"{MEDELLIN}/transporte/VM_Accidentes/MapServer/0",
        "gravedad_siniestro = 'HERIDOS' AND anio >= 2025",
        "anio",
    ),
    ArcGisLayer(
        "colegios_medellin/sedes_educativas.geojson",
        f"{MEDELLIN}/educacion/VC_Sedes_Instituciones/MapServer/0",
        "1=1",
        "nombre_est,sector",
    ),
]

# Recursos de Datos Abiertos Bogotá (CKAN): id del recurso -> carpeta de destino.
CKAN_SHAPEFILES = {
    "b71c98ad-535d-4d33-8e8e-e36f1479acd3": "perimetrourbano_bogota",
    "b2e46196-f801-4324-8be3-9e7d54266876": "colegios_bogota",
}


def download_arcgis(client: httpx.Client, layer: ArcGisLayer) -> int:
    """Descarga todas las páginas de la capa y las une en un solo FeatureCollection."""
    features: list[dict] = []
    offset = 0
    while True:
        response = client.get(
            f"{layer.url}/query",
            params={
                "where": layer.where,
                "outFields": layer.out_fields,
                "outSR": 4326,
                "f": "geojson",
                "orderByFields": "objectid",
                "resultOffset": offset,
                "resultRecordCount": PAGE_SIZE,
            },
        )
        response.raise_for_status()
        page = response.json().get("features", [])
        features.extend(page)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    path = RAW / layer.output
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8"
    )
    return len(features)


def download_ckan_shapefile(client: httpx.Client, resource_id: str, folder: str) -> int:
    """Descarga el zip del recurso CKAN y lo descomprime. Devuelve el número de archivos."""
    resource = client.get(BOGOTA_CKAN, params={"id": resource_id})
    resource.raise_for_status()
    url = resource.json()["result"]["url"]
    content = client.get(url, follow_redirects=True)
    content.raise_for_status()

    target = RAW / folder
    target.mkdir(parents=True, exist_ok=True)
    if url.lower().endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(content.content)) as archive:
            archive.extractall(target)
            return len(archive.namelist())
    (target / Path(url).name).write_bytes(content.content)
    return 1


def main() -> None:
    with httpx.Client(timeout=120, headers={"User-Agent": "fleet-pipeline-data-prep"}) as client:
        for layer in ARCGIS_LAYERS:
            count = download_arcgis(client, layer)
            log.info("%s: %d elementos", layer.output, count)
        for resource_id, folder in CKAN_SHAPEFILES.items():
            files = download_ckan_shapefile(client, resource_id, folder)
            log.info("%s: %d archivos", folder, files)


if __name__ == "__main__":
    main()
