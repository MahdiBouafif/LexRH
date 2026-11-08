"""Turn one legal article into one or more text chunks ready to embed.

Design choices (each one is an interview question):

* An article is the natural unit of law, so most articles stay in ONE chunk.
* Every chunk starts with a header (section path + article number). Without it,
  "Le préavis est de deux mois." could belong to any of thousands of articles.
* ``nota`` (applicability notes) is treated as the final paragraphs of the article:
  it is searchable and citable together with the text, and it can never blow the
  budget of the other chunks.
* Long articles are split on paragraph boundaries, then on word boundaries, then
  (for a single giant word) by characters. No chunk ever exceeds ``max_tokens``.
* A very long section path is shortened from the top (the deepest levels carry the
  most specific meaning), so the header can never eat the whole budget.
* No tokenizer dependency: token counts are estimated from characters, 3 per token.
  Measured on the real corpus: 16.3M estimated vs 15.2M billed, so the estimate is a
  slight overcount and safe.

CHUNKER_VERSION is stored with every chunk. Bump it when the output of this module
changes, so the pipeline knows which chunks to rebuild.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

CHUNKER_VERSION = "2"
CHARS_PER_TOKEN = 3
DEFAULT_MAX_TOKENS = 1200  # a retrieval-quality budget, far below the 8k model limit
NOTA_LABEL = "Note d'application :"
MIN_BODY_CHARS = 1


def estimate_tokens(text: str) -> int:
    """Cheap, deterministic token estimate (rounded up)."""
    return -(-len(text) // CHARS_PER_TOKEN)


@dataclass(frozen=True)
class Chunk:
    """One piece of text to embed and store."""

    ord: int
    text: str

    @property
    def token_estimate(self) -> int:
        return estimate_tokens(self.text)


def build_header(num: str | None, hierarchy: Sequence[str], max_chars: int | None = None) -> str:
    """Section path then article number, e.g. 'Partie > Livre > ... > Article L1234-1'.

    When ``max_chars`` is given and the header is longer, the TOP levels are dropped
    first and replaced by an ellipsis; the article number is always kept.
    """
    levels = [part.strip() for part in hierarchy if part and part.strip()]
    tail = f"Article {num}" if num else ""

    def join(parts: list[str]) -> str:
        return " > ".join([*parts, tail] if tail else parts)

    header = join(levels)
    if max_chars is None or len(header) <= max_chars:
        return header

    kept = list(levels)
    while kept and len(join(["…", *kept])) > max_chars:
        kept.pop(0)
    header = join(["…", *kept]) if kept else tail
    return header[-max_chars:] if len(header) > max_chars else header


def _hard_cut(word: str, size: int) -> list[str]:
    return [word[i : i + size] for i in range(0, len(word), size)]


def _split_oversized(paragraph: str, budget_chars: int) -> list[str]:
    """A paragraph longer than the budget: cut at word boundaries (long words by characters)."""
    if len(paragraph) <= budget_chars:
        return [paragraph]
    pieces: list[str] = []
    current: list[str] = []
    size = 0

    def flush() -> None:
        nonlocal current, size
        if current:
            pieces.append(" ".join(current))
        current, size = [], 0

    for word in paragraph.split(" "):
        for part in _hard_cut(word, budget_chars) if len(word) > budget_chars else [word]:
            extra = len(part) + (1 if current else 0)
            if current and size + extra > budget_chars:
                flush()
                extra = len(part)
            current.append(part)
            size += extra
    flush()
    return pieces


def _pack(paragraphs: Sequence[str], budget_chars: int) -> list[str]:
    """Greedily group paragraphs into bodies no longer than ``budget_chars``."""
    bodies: list[str] = []
    current: list[str] = []
    size = 0
    for paragraph in paragraphs:
        for piece in _split_oversized(paragraph, budget_chars):
            extra = len(piece) + (1 if current else 0)
            if current and size + extra > budget_chars:
                bodies.append("\n".join(current))
                current, size = [], 0
                extra = len(piece)
            current.append(piece)
            size += extra
    if current:
        bodies.append("\n".join(current))
    return bodies


def chunk_article(
    *,
    num: str | None,
    hierarchy: Sequence[str],
    text: str,
    nota: str = "",
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> list[Chunk]:
    """Split one article into chunks. Returns [] when there is nothing to index.

    Guarantee: every returned chunk has ``token_estimate <= max_tokens``.
    """
    if not text.strip():
        return []

    max_chars = max_tokens * CHARS_PER_TOKEN
    header = build_header(num, hierarchy, max_chars=max_chars // 3)
    budget_chars = max(max_chars - len(header) - 1, MIN_BODY_CHARS)

    paragraphs = [line.strip() for line in text.split("\n") if line.strip()]
    nota_lines = [line.strip() for line in nota.split("\n") if line.strip()]
    if nota_lines:
        nota_lines[0] = f"{NOTA_LABEL} {nota_lines[0]}"
    bodies = _pack([*paragraphs, *nota_lines], budget_chars)

    return [
        Chunk(ord=index, text="\n".join([header, body]) if header else body)
        for index, body in enumerate(bodies)
    ]
