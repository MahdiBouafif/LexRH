import httpx
import pytest

from lexrh.config import EMBEDDING_DIM
from lexrh.rag.embed_chunks import MAX_TEXTS_PER_CALL, _call_groups
from lexrh.rag.embeddings import EmbeddingError, HashEmbedder, MistralEmbedder


def _vector() -> list[float]:
    return [0.1] * EMBEDDING_DIM


def _ok(count: int, tokens: int = 7) -> httpx.Response:
    data = [{"index": i, "embedding": _vector()} for i in reversed(range(count))]
    return httpx.Response(200, json={"data": data, "usage": {"total_tokens": tokens}})


def _embedder(handler, sleeps: list[float] | None = None) -> MistralEmbedder:  # type: ignore[no-untyped-def]
    client = httpx.Client(transport=httpx.MockTransport(handler))
    recorded = sleeps if sleeps is not None else []
    return MistralEmbedder("key", client=client, sleep=recorded.append)


def test_empty_key_is_rejected() -> None:
    with pytest.raises(EmbeddingError, match="MISTRAL_API_KEY"):
        MistralEmbedder("")


def test_success_returns_vectors_in_input_order_and_tokens() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["authorization"]
        return _ok(3, tokens=42)

    result = _embedder(handler).embed(["a", "b", "c"])
    assert seen["auth"] == "Bearer key"
    assert len(result.vectors) == 3
    assert result.tokens == 42


def test_empty_input_makes_no_call() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no call expected")

    assert _embedder(handler).embed([]).vectors == []


def test_retries_on_429_then_succeeds_and_honours_retry_after() -> None:
    attempts = {"n": 0}
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] < 3:
            return httpx.Response(429, headers={"retry-after": "2"})
        return _ok(1)

    result = _embedder(handler, sleeps).embed(["x"])
    assert len(result.vectors) == 1
    assert attempts["n"] == 3
    assert sleeps == [2.0, 2.0]


def test_retries_on_server_error_with_backoff() -> None:
    attempts = {"n": 0}
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(503) if attempts["n"] == 1 else _ok(1)

    _embedder(handler, sleeps).embed(["x"])
    assert len(sleeps) == 1
    assert 0.5 <= sleeps[0] <= 1.0


def test_retries_on_network_error() -> None:
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise httpx.ConnectError("boom")
        return _ok(1)

    assert len(_embedder(handler).embed(["x"]).vectors) == 1


def test_gives_up_after_max_attempts() -> None:
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(500)

    with pytest.raises(EmbeddingError, match="after 6 attempts"):
        _embedder(handler).embed(["x"])
    assert attempts["n"] == 6


def test_client_error_is_not_retried() -> None:
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(401, text="unauthorized")

    with pytest.raises(EmbeddingError, match="401"):
        _embedder(handler).embed(["x"])
    assert attempts["n"] == 1


def test_wrong_dimension_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"data": [{"index": 0, "embedding": [0.1, 0.2]}], "usage": {}}
        )

    with pytest.raises(EmbeddingError, match="dimensions"):
        _embedder(handler).embed(["x"])


def test_wrong_count_is_rejected() -> None:
    with pytest.raises(EmbeddingError, match="got 1"):
        _embedder(lambda request: _ok(1)).embed(["a", "b"])


def test_hash_embedder_is_deterministic_and_records_batches() -> None:
    fake = HashEmbedder()
    first = fake.embed(["a", "b"])
    second = fake.embed(["a"])
    assert first.vectors[0] == second.vectors[0]
    assert first.vectors[0] != first.vectors[1]
    assert len(first.vectors[0]) == EMBEDDING_DIM
    assert fake.calls == [2, 1]


def test_call_groups_respect_count_and_token_limits() -> None:
    many = ["x"] * (MAX_TEXTS_PER_CALL * 2 + 1)
    groups = _call_groups(many)
    assert [len(g) for g in groups] == [MAX_TEXTS_PER_CALL, MAX_TEXTS_PER_CALL, 1]

    big = ["y" * 30_000, "y" * 30_000]  # about 10k estimated tokens each
    assert [len(g) for g in _call_groups(big)] == [1, 1]
    assert sorted(i for g in groups for i in g) == list(range(len(many)))
