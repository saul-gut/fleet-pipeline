"""Autenticación por API key y roles (D-12)."""

import pytest
from fastapi import HTTPException

from fleet_pipeline.config import Settings
from fleet_pipeline.security.auth import Role, require_roles, resolve_role

SETTINGS = Settings(
    postgres_user="u",
    postgres_password="p",
    postgres_db="d",
    api_key_device="key-device",
    api_key_admin="key-admin",
    api_key_analyst="key-analyst",
)


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("key-device", Role.DEVICE),
        ("key-admin", Role.ADMIN),
        ("key-analyst", Role.ANALYST),
        ("key-otra", None),
        ("", None),
    ],
)
def test_resolve_role(key: str, expected: Role | None) -> None:
    assert resolve_role(key, SETTINGS) == expected


def test_allowed_role_passes() -> None:
    dependency = require_roles(Role.DEVICE)
    assert dependency(SETTINGS, "key-device") == Role.DEVICE


def test_missing_key_is_401() -> None:
    with pytest.raises(HTTPException) as exc:
        require_roles(Role.DEVICE)(SETTINGS, None)
    assert exc.value.status_code == 401


def test_wrong_key_is_401() -> None:
    with pytest.raises(HTTPException) as exc:
        require_roles(Role.DEVICE)(SETTINGS, "key-falsa")
    assert exc.value.status_code == 401


def test_valid_key_with_wrong_role_is_403() -> None:
    """Un analista no puede enviar telemetría."""
    with pytest.raises(HTTPException) as exc:
        require_roles(Role.DEVICE)(SETTINGS, "key-analyst")
    assert exc.value.status_code == 403
