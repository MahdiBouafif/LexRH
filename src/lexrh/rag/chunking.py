"""Turn one legal article into one or more text chunks ready to embed.

Design choices (each one is an interview question):

* An article is the natural unit of law, so most articles stay in ONE chunk.
* Every chunk starts with a header (section path + article number). Without it,
  "Le préavis est de deux mois." could belong to any of thousands of articles.
* ``nota`` (applicability notes) is appended to the LAST chunk: it changes how the
  article applies, so it must be searchable and citable together with the text.
* Long articles are split on paragraph boundaries, never in the middle of a
  sentence, and never above the token budget.
* No tokenizer dependency: token counts are estimated from characters. The
  estimate is deliberately pessimistic (3 characters per token, French averages
  closer to 4), so we never exceed the model's context window by mistake.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

CHARS_PER_TOKEN = 3  # pessimistic on purpose (see module docstring)
DEFAULT_MAX_TOKENS = 1200  # a retrieval-quality budget, far below the 8k model limit
NOTA_LABEL = "Note d'application :"


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


def build_header(num: str | None, hierarchy: Sequence[str]) -> str:
    """Section path then article number, e.g. 'Partie > Livre > ... > Article L1234-1'."""
    parts = [part.strip() for part in hierarchy if part and part.strip()]
    if num:
        parts.append(f"Article {num}")
    return " > ".join(parts)


def _split_oversized(paragraph: str, budget_chars: int) -> list[str]:
    """Last resort for a single paragraph longer than the budget: cut at word boundaries."""
    pieces: list[str] = []
    current: list[str] = []
    size = 0
    for word in paragraph.split(" "):
        extra = len(word) + (1 if current else 0)
        if current and size + extra > budget_chars:
            pieces.append(" ".join(current))
            current, size = [], 0
            extra = len(word)
        current.append(word)
        size += extra
    if current:
        pieces.append(" ".join(current))
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
    """Split one article into chunks. Returns [] when there is nothing to index."""
    body = text.strip()
    if not body:
        return []

    header = build_header(num, hierarchy)
    nota_block = f"{NOTA_LABEL} {nota.strip()}" if nota.strip() else ""

    # Fixed overhead every chunk pays: header plus a newline. The nota is added to the last
    # chunk only, so reserve room for it there as well (simple and safe: reserve everywhere).
    overhead = len(header) + 1 + (len(nota_block) + 1 if nota_block else 0)
    budget_chars = max(max_tokens * CHARS_PER_TOKEN - overhead, 200)

    paragraphs = [line for line in body.split("\n") if line.strip()]
    bodies = _pack(paragraphs, budget_chars)

    chunks: list[Chunk] = []
    for index, piece in enumerate(bodies):
        lines = [header] if header else []
        lines.append(piece)
        if nota_block and index == len(bodies) - 1:
            lines.append(nota_block)
        chunks.append(Chunk(ord=index, text="\n".join(lines)))
    return chunks
