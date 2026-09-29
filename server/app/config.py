from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="LLV_", extra="ignore")

    env: str = "dev"
    log_level: str = "INFO"

    database_url: str = "postgresql+asyncpg://llvoice:llvoice@localhost:5432/llvoice"
    redis_url: str = "redis://localhost:6379/0"

    jwt_secret: SecretStr
    # Khóa AES-256 (base64, 32 byte) dùng để mã hóa API key MiniMax lưu trong DB
    encryption_key: SecretStr
    access_token_ttl_minutes: int = 24 * 60
    refresh_token_ttl_days: int = 30

    minimax_base_url: str = "https://api.minimax.io"

    # Khi chạy thật, đổi thành đường dẫn khó đoán
    admin_path: str = "/admin"

    @property
    def is_dev(self) -> bool:
        return self.env == "dev"


@lru_cache
def get_settings() -> Settings:
    return Settings()
