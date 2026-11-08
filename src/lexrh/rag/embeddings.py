"""Embedding client for the Mistral API, with retries, plus a fake for tests.

We call the HTTP endpoint directly with httpx instead of using a vendor SDK:
the API is one endpoint, the dependency stays small, and tests can replace the
network with ``httpx.MockTransport``.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import httpx

from lexrh.config import EMBEDDING_DIM
from lexrh.rag.http import ApiError, post_json_with_retry

MISTRAL_EMBEDDINGS_URL = "https://api.mistral.ai/v1/embeddings"
MISTRAL_MODEL = "mistral-embed"


class EmbeddingError(ApiError):
    """The embedding call failed for good."""


@dataclass(frozen=True)
class EmbeddingResult:
    vectors: list[list[float]]
    tokens: int  # tokens billed by the API (0 for the fake)


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> EmbeddingResult: ...


class MistralEmbedder:
    """Calls Mistral's embeddings endpoint. Retries on rate limits and server errors."""

    def __init__(
        self,
        api_key: str,
        *,
        client: httpx.Client | None = None,
        max_attempts: int = 6,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key:
            raise EmbeddingError("MISTRAL_API_KEY is empty. Set it in .env.")
        self._client = client or httpx.Client(timeout=60.0)
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._max_attempts = max_attempts
        self._base_delay = base_delay
        self._max_delay = max_delay
        self._sleep = sleep

    def embed(self, texts: list[str]) -> EmbeddingResult:
        if not texts:
            return EmbeddingResult(vectors=[], tokens=0)
        response = post_json_with_retry(
            self._client,
            MISTRAL_EMBEDDINGS_URL,
            payload={"model": MISTRAL_MODEL, "input": texts},
            headers=self._headers,
            max_attempts=self._max_attempts,
            base_delay=self._base_delay,
            max_delay=self._max_delay,
            sleep=self._sleep,
            error_cls=EmbeddingError,
        )
        return self._parse(response, expected=len(texts))

    @staticmethod
    def _parse(response: httpx.Response, expected: int) -> EmbeddingResult:
        body = response.json()
        data = sorted(body["data"], key=lambda item: item["index"])
        vectors = [item["embedding"] for item in data]
        if len(vectors) != expected:
            raise EmbeddingError(f"Asked for {expected} embeddings, got {len(vectors)}")
        for vector in vectors:
            if len(vector) != EMBEDDING_DIM:
                raise EmbeddingError(
                    f"Expected {EMBEDDING_DIM} dimensions, got {len(vector)}. "
                    "Update EMBEDDING_DIM and add a migration."
                )
        tokens = int(body.get("usage", {}).get("total_tokens", 0))
        return EmbeddingResult(vectors=vectors, tokens=tokens)


class HashEmbedder:
    """Deterministic fake: same text gives the same vector. For tests only, no network."""

    def __init__(self, dim: int = EMBEDDING_DIM) -> None:
        self._dim = dim
        self.calls: list[int] = []  # batch sizes, so tests can check batching

    def embed(self, texts: list[str]) -> EmbeddingResult:
        self.calls.append(len(texts))
        vectors: list[list[float]] = []
        for text in texts:
            digest = hashlib.sha256(text.encode()).digest()
            vectors.append([digest[i % len(digest)] / 255.0 for i in range(self._dim)])
        return EmbeddingResult(vectors=vectors, tokens=0)
