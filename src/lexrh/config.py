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
    # Limits are per model and per plan. ministral-8b-latest is allowed on the free tier at
    # 2 requests/minute; mistral-small/medium/large are not. Change both lines with your plan.
    rerank_backend: str = "local"  # "local" (cross-encoder, no quota) or "mistral"
    rerank_model: str = "ministral-8b-latest"
    local_rerank_model: str = "BAAI/bge-reranker-v2-m3"
    chat_min_interval: float = 31.0  # seconds between chat calls (2 per minute -> 30 s)

    # Answer model: any OpenAI-compatible server (Ollama, mlx_lm.server, LM Studio, Groq, ...).
    # Set GENERATOR_MODEL to a model name from your provider (e.g. the Groq console list).
    generator_base_url: str = "https://api.groq.com/openai/v1"
    generator_model: str = ""
    generator_api_key: str = ""
    generator_max_tokens: int = 600
    # Only for reasoning models that accept it (e.g. "none"); empty = do not send the parameter.
    generator_reasoning_effort: str = ""
    answer_top_k: int = 5
    # Model used to rewrite the question into legal wording before searching (same server and
    # key as the answer model). Empty = off. A different model than the answer model has its
    # own rate limit, e.g. ministral-8b-latest next to ministral-14b-latest.
    rewrite_model: str = ""
    # Refuse when the best article's rerank score (0..10) is below this. 0 = disabled until
    # it is tuned on questions the Code du travail does not answer.
    min_relevance: float = 0.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
