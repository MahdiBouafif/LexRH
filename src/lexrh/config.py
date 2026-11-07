from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

# Vector size of the embedding model. Verify against the current Mistral
# embeddings documentation before the first ingestion; changing it later
# needs a new migration and a re-embedding run.
EMBEDDING_DIM = 1024


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://lexrh:lexrh@localhost:5432/lexrh"
    log_level: str = "INFO"
    mistral_api_key: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
