"""One retrying POST for every Mistral call (embeddings, reranking, later answering)."""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from typing import Any

import httpx

logger = logging.getLogger(__name__)

RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


class ApiError(RuntimeError):
    """A Mistral call failed for good (after retries, or a non-retryable error)."""


def _delay(attempt: int, response: httpx.Response | None, base: float, cap: float) -> float:
    """Honour Retry-After when the server sends it, else exponential backoff + jitter."""
    if response is not None:
        retry_after = response.headers.get("retry-after")
        if retry_after:
            try:
                return min(float(retry_after), cap)
            except ValueError:
                pass
    backoff = min(base * float(2**attempt), cap)
    jitter: float = random.uniform(0.5, 1.0)  # noqa: S311 (jitter, not security)
    return backoff * jitter


def post_json_with_retry(
    client: httpx.Client,
    url: str,
    *,
    payload: dict[str, Any],
    headers: dict[str, str],
    max_attempts: int = 6,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    sleep: Callable[[float], None] = time.sleep,
    error_cls: type[ApiError] = ApiError,
) -> httpx.Response:
    """POST ``payload`` as JSON. Retry rate limits, server errors and network errors."""
    for attempt in range(max_attempts):
        response: httpx.Response | None = None
        try:
            response = client.post(url, json=payload, headers=headers)
        except httpx.TransportError as exc:  # timeouts, connection resets
            logger.warning("call failed (%s), attempt %d", exc, attempt + 1)
        else:
            if response.status_code == 200:
                return response
            if (
                response.status_code == 429
                and response.headers.get("x-ratelimit-limit-req-minute") == "0"
            ):
                raise error_cls(
                    f"No quota for this model on {url} (limit of 0 requests per minute). "
                    "Use a model your plan allows, or check your plan in the Mistral console."
                )
            if response.status_code not in RETRYABLE_STATUS:
                raise error_cls(
                    f"Mistral returned {response.status_code} for {url}: {response.text[:200]}"
                )
            logger.warning(
                "Mistral returned %d for %s, attempt %d", response.status_code, url, attempt + 1
            )

        if attempt < max_attempts - 1:
            sleep(_delay(attempt, response, base_delay, max_delay))

    raise error_cls(f"Call failed after {max_attempts} attempts")
