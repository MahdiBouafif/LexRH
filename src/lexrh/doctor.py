"""Quick setup check: `make doctor`. Never prints secrets, only whether they are set."""

from __future__ import annotations

import argparse
import importlib.util
import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date

from sqlalchemy import text

from lexrh.config import Settings, get_settings

PLACEHOLDERS = {"", "xxx", "change-me", "changeme", "your-key"}


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    warning: bool = False  # a failed warning does not fail the run


def check_settings(settings: Settings) -> Check:
    problems: list[str] = []
    if settings.mistral_api_key.strip().lower() in PLACEHOLDERS:
        problems.append("MISTRAL_API_KEY is empty or a placeholder (needed for embeddings)")
    if not settings.generator_model:
        problems.append("GENERATOR_MODEL is empty")
    local = "localhost" in settings.generator_base_url or "127.0.0.1" in settings.generator_base_url
    if not local and settings.generator_api_key.strip().lower() in PLACEHOLDERS:
        problems.append("GENERATOR_API_KEY is empty (needed for a hosted answer model)")
    summary = (
        f"answer model {settings.generator_model or '-'} at {settings.generator_base_url}; "
        f"reranker={settings.rerank_backend}; top_k={settings.answer_top_k}"
    )
    return Check("settings", not problems, "; ".join(problems) or summary)


def check_database() -> Check:
    from lexrh.db.session import get_engine

    try:
        with get_engine().connect() as conn:
            documents = conn.execute(text("SELECT count(*) FROM documents")).scalar_one()
            in_force = conn.execute(
                text(
                    "SELECT count(*) FROM documents WHERE status <> 'ANNULE' "
                    "AND valid_from <= :d AND (valid_to IS NULL OR valid_to > :d)"
                ),
                {"d": date.today()},
            ).scalar_one()
            chunks = conn.execute(text("SELECT count(*) FROM chunks")).scalar_one()
            missing = conn.execute(
                text("SELECT count(*) FROM chunks WHERE embedding IS NULL")
            ).scalar_one()
    except Exception as exc:  # report any connection/schema problem as a failed check
        return Check(
            "database", False, f"{type(exc).__name__}: {str(exc)[:150]} (is `make up` running?)"
        )
    detail = f"{documents} article versions, {in_force} in force today, {chunks} chunks"
    if documents == 0 or chunks == 0:
        return Check("database", False, detail + " - empty: run `make ingest` and `make embed`")
    if missing:
        return Check("database", False, detail + f", {missing} without embedding: run `make embed`")
    return Check("database", True, detail + ", all embedded")


def check_embedding(settings: Settings) -> Check:
    from lexrh.config import EMBEDDING_DIM
    from lexrh.rag.embeddings import MistralEmbedder
    from lexrh.rag.http import ApiError

    try:
        result = MistralEmbedder(settings.mistral_api_key, max_attempts=2).embed(["test"])
    except ApiError as exc:
        return Check("mistral embeddings", False, str(exc)[:200])
    ok = len(result.vectors) == 1 and len(result.vectors[0]) == EMBEDDING_DIM
    return Check("mistral embeddings", ok, f"returned {len(result.vectors[0])} dimensions")


def check_reranker(settings: Settings) -> Check:
    if settings.rerank_backend != "local":
        return Check("reranker", True, f"backend={settings.rerank_backend} (nothing to install)")
    if importlib.util.find_spec("sentence_transformers") is None:
        return Check(
            "reranker", False, "sentence-transformers missing: run `uv sync --extra local-rerank`"
        )
    return Check(
        "reranker", True, f"sentence-transformers installed; model {settings.local_rerank_model}"
    )


def check_generator(settings: Settings) -> Check:
    from lexrh.rag.answer import answer_schema
    from lexrh.rag.generate import OpenAICompatibleGenerator
    from lexrh.rag.http import ApiError

    generator = OpenAICompatibleGenerator(
        settings.generator_base_url,
        settings.generator_model,
        api_key=settings.generator_api_key,
        max_tokens=max(settings.generator_max_tokens, 200),
        max_attempts=2,
        extra_body=(
            {"reasoning_effort": settings.generator_reasoning_effort}
            if settings.generator_reasoning_effort
            else None
        ),
    )
    messages = [
        {"role": "system", "content": "Réponds uniquement par un objet JSON."},
        {
            "role": "user",
            "content": (
                "Article [L1] : « Le ciel est bleu. » "
                "Question : de quelle couleur est le ciel ? Cite L1 et mets refusal à false."
            ),
        },
    ]
    started = time.perf_counter()
    try:
        raw = generator.generate(messages, answer_schema(["L1"]))
    except ApiError as exc:
        return Check("answer model", False, str(exc)[:220])
    seconds = time.perf_counter() - started
    try:
        data = json.loads(raw)
        ok = isinstance(data, dict) and {"answer", "citations", "refusal"} <= set(data)
    except ValueError:
        ok = False
    return Check(
        "answer model",
        ok,
        f"replied with valid JSON in {seconds:.1f}s" if ok else f"bad JSON: {raw[:120]}",
    )


def check_ask() -> Check:
    from lexrh.rag.answer import build_assistant
    from lexrh.rag.http import ApiError

    started = time.perf_counter()
    try:
        result = build_assistant().ask(
            "Quel préavis pour un licenciement après 3 ans ?", date(2020, 1, 1)
        )
    except ApiError as exc:
        return Check("end-to-end /ask", False, str(exc)[:220])
    seconds = time.perf_counter() - started
    cited = [source.num for source in result.sources]
    if result.refused:
        return Check("end-to-end /ask", False, f"refused ({result.reason})", warning=True)
    detail = (
        f"{seconds:.1f}s (includes loading the reranker); cited {', '.join(cited)}; "
        f"shown {', '.join(result.retrieved)}"
    )
    if "L1234-1" not in cited:
        return Check("end-to-end /ask", False, detail + " - expected L1234-1", warning=True)
    return Check("end-to-end /ask", True, detail)


def format_checks(checks: Sequence[Check]) -> str:
    lines = []
    for check in checks:
        mark = "PASS" if check.ok else ("WARN" if check.warning else "FAIL")
        lines.append(f"[{mark}] {check.name}: {check.detail}")
    failed = sum(1 for c in checks if not c.ok and not c.warning)
    warned = sum(1 for c in checks if not c.ok and c.warning)
    lines.append("")
    lines.append(
        "Everything looks right."
        if not failed and not warned
        else f"{failed} failed, {warned} warning(s). Fix the FAIL lines first."
    )
    return "\n".join(lines)


def run(skip_ask: bool = False, runner: Callable[[], Settings] = get_settings) -> list[Check]:
    settings = runner()
    checks = [check_settings(settings)]
    if not checks[0].ok:
        return checks  # nothing else can work without the basics
    checks.append(check_database())
    checks.append(check_reranker(settings))
    checks.append(check_embedding(settings))
    checks.append(check_generator(settings))
    if not skip_ask and all(c.ok or c.warning for c in checks):
        checks.append(check_ask())
    return checks


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check that LexRH is set up correctly.")
    parser.add_argument("--skip-ask", action="store_true", help="skip the end-to-end question")
    args = parser.parse_args(argv)
    checks = run(skip_ask=args.skip_ask)
    print(format_checks(checks))
    return 1 if any(not c.ok and not c.warning for c in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())
