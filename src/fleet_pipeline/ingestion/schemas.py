"""Contrato del paquete de telemetría que envían los dispositivos IoT/AVL."""

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

VEHICLE_ID_PATTERN = r"^VHC-[0-9]{4}-[A-Z]{3}$"


class TelemetryPacket(BaseModel):
    """Un reporte de un vehículo.

    Aquí se valida el formato y los rangos físicos (nivel 1 de la D-09). Las reglas que
    dependen del contexto (fecha actual, territorio, lectura anterior) viven en quality.py.
    """

    # extra="ignore": un firmware nuevo que agregue campos no rompe la ingesta.
    # allow_inf_nan=False: NaN e infinito no son mediciones válidas.
    model_config = ConfigDict(frozen=True, extra="ignore", allow_inf_nan=False)

    vehicle_id: str = Field(pattern=VEHICLE_ID_PATTERN, examples=["VHC-8472-ABC"])
    # AwareDatetime exige zona horaria: una hora sin zona es ambigua y se rechaza.
    ts: AwareDatetime = Field(examples=["2026-11-20T18:30:00-05:00"])
    lat: float = Field(ge=-90, le=90, examples=[4.59808])
    lon: float = Field(ge=-180, le=180, examples=[-74.07609])
    speed_kmh: float = Field(ge=0, le=250, examples=[42.5])
    fuel_pct: float = Field(ge=0, le=100, examples=[81.3])
