"""Autenticación por API key con tres roles (D-12).

La key llega en el header X-API-Key. La comparación usa secrets.compare_digest, que tarda lo
mismo acierte o falle, para no filtrar información por tiempos de respuesta.
"""

from collections.abc import Callable
from enum import StrEnum
from secrets import compare_digest
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from fleet_pipeline.config import Settings, get_settings


class Role(StrEnum):
    DEVICE = "device"  # dispositivos: solo envían telemetría
    ADMIN = "admin"  # ve el ID real del vehículo
    ANALYST = "analyst"  # ve el ID enmascarado


def resolve_role(api_key: str, settings: Settings) -> Role | None:
    """Devuelve el rol asociado a la key, o None si no corresponde a ninguno."""
    keys = {
        Role.DEVICE: settings.api_key_device,
        Role.ADMIN: settings.api_key_admin,
        Role.ANALYST: settings.api_key_analyst,
    }
    for role, secret in keys.items():
        if compare_digest(api_key.encode(), secret.get_secret_value().encode()):
            return role
    return None


def require_roles(*allowed: Role) -> Callable[..., Role]:
    """Dependencia de FastAPI: exige una key válida de alguno de los roles permitidos."""

    def dependency(
        settings: Annotated[Settings, Depends(get_settings)],
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> Role:
        role = resolve_role(x_api_key, settings) if x_api_key else None
        if role is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "API key ausente o inválida")
        if role not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "El rol no tiene permiso")
        return role

    return dependency
