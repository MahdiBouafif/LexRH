"""Answer model client: any server that speaks the OpenAI chat-completions protocol.

Ollama, mlx_lm.server, LM Studio, llama.cpp's server, Groq, Cloudflare Workers AI and
Mistral all do, so switching model or provider is a change of ``base_url`` and ``model`` in
the settings, not a code change. Output is constrained with a JSON schema.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from typing import Any, Protocol

import httpx

from lexrh.rag.http import ApiError, post_json_with_retry


class GenerationError(ApiError):
    """The answer model could not be reached or returned something unusable."""


class Generator(Protocol):
    def generate(self, messages: Sequence[dict[str, str]], schema: dict[str, Any]) -> str: ...


class OpenAICompatibleGenerator:
    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        api_key: str = "",
        client: httpx.Client | None = None,
        max_tokens: int = 600,
        max_attempts: int = 3,
        base_delay: float = 2.0,
        extra_body: dict[str, Any] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._model = model
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        # A local 4B model can take a while to read a prompt; keep the timeout generous.
        self._client = client or httpx.Client(timeout=180.0)
        self._max_tokens = max_tokens
        self._max_attempts = max_attempts
        self._base_delay = base_delay
        self._extra_body = extra_body or {}
        self._sleep = sleep
        # Some servers (e.g. Ollama with certain models) refuse JSON-schema output with a
        # 501. After the first refusal we stop asking and describe the JSON in the prompt.
        self._structured = True

    def generate(self, messages: Sequence[dict[str, str]], schema: dict[str, Any]) -> str:
        if self._structured:
            try:
                return self._call(list(messages), schema, structured=True)
            except GenerationError as exc:
                if "501" not in str(exc) and "structured" not in str(exc).lower():
                    raise
                self._structured = False
        shape = json.dumps(schema["properties"], ensure_ascii=False)
        hint = (
            "Réponds uniquement par un objet JSON valide, sans texte autour, "
            f"avec exactement ces champs : {shape}"
        )
        prompted = [dict(m) for m in messages]
        prompted[-1]["content"] += "\n\n" + hint
        return self._call(prompted, schema, structured=False)

    def _call(
        self, messages: list[dict[str, str]], schema: dict[str, Any], *, structured: bool
    ) -> str:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": self._max_tokens,
            **self._extra_body,
        }
        if structured:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "answer", "strict": True, "schema": schema},
            }
        response = post_json_with_retry(
            self._client,
            self._url,
            payload=payload,
            headers=self._headers,
            max_attempts=self._max_attempts,
            base_delay=self._base_delay,
            sleep=self._sleep,
            error_cls=GenerationError,
        )
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise GenerationError(f"Unexpected response from the answer model: {exc}") from exc
        if not isinstance(content, str):
            raise GenerationError("The answer model returned no text.")
        return content
