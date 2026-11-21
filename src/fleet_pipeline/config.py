"""Configuración leída de variables de entorno (o del archivo .env en desarrollo)."""

from functools import lru_cache

from psycopg.conninfo import make_conninfo
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    postgres_user: str
    postgres_password: SecretStr
    postgres_db: str
    postgres_host: str = "localhost"
    postgres_port: int = 5432

    api_key_device: SecretStr
    api_key_admin: SecretStr
    api_key_analyst: SecretStr

    max_batch_size: int = 1000

    @property
    def conninfo(self) -> str:
        return make_conninfo(
            host=self.postgres_host,
            port=self.postgres_port,
            dbname=self.postgres_db,
            user=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
