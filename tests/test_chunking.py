from lexrh.rag.chunking import (
    CHARS_PER_TOKEN,
    NOTA_LABEL,
    build_header,
    chunk_article,
    estimate_tokens,
)

HIERARCHY = ["Partie législative", "Livre II", "Article section"]


def test_header_joins_hierarchy_and_article_number() -> None:
    assert build_header("L1234-1", HIERARCHY) == (
        "Partie législative > Livre II > Article section > Article L1234-1"
    )


def test_header_ignores_blank_levels_and_missing_number() -> None:
    assert build_header(None, ["A", " ", "", "B"]) == "A > B"


def test_short_article_is_a_single_chunk_with_header() -> None:
    chunks = chunk_article(num="L1", hierarchy=HIERARCHY, text="Le préavis est de deux mois.")
    assert len(chunks) == 1
    assert chunks[0].ord == 0
    assert chunks[0].text.startswith("Partie législative > Livre II")
    assert chunks[0].text.endswith("Le préavis est de deux mois.")


def test_empty_text_gives_no_chunk() -> None:
    assert chunk_article(num="L1", hierarchy=HIERARCHY, text="  \n ") == []


def test_short_nota_ends_the_last_chunk_and_appears_once() -> None:
    paragraphs = "\n".join(f"Alinéa {i} " + " ".join(["mot"] * 60) for i in range(20))
    chunks = chunk_article(
        num="L1",
        hierarchy=HIERARCHY,
        text=paragraphs,
        nota="Applicable au 1er mai.",
        max_tokens=200,
    )
    assert len(chunks) > 1
    assert sum(NOTA_LABEL in chunk.text for chunk in chunks) == 1
    assert chunks[-1].text.endswith(f"{NOTA_LABEL} Applicable au 1er mai.")


def test_huge_nota_cannot_break_the_budget() -> None:
    nota = "\n".join(f"Disposition {i} " + " ".join(["mot"] * 80) for i in range(40))
    chunks = chunk_article(
        num="L1", hierarchy=HIERARCHY, text="Texte court.", nota=nota, max_tokens=300
    )
    assert len(chunks) > 1
    assert all(chunk.token_estimate <= 300 for chunk in chunks)
    assert all(chunk.text.startswith("Partie législative") for chunk in chunks)
    assert NOTA_LABEL in "\n".join(chunk.text for chunk in chunks)


def test_very_long_hierarchy_is_shortened_from_the_top() -> None:
    levels = [f"Niveau {i} " + "x" * 200 for i in range(10)]
    header = build_header("L1", levels, max_chars=300)
    assert len(header) <= 300
    assert header.startswith("…")
    assert header.endswith("Article L1")
    chunks = chunk_article(num="L1", hierarchy=levels, text="Texte.", max_tokens=300)
    assert all(chunk.token_estimate <= 300 for chunk in chunks)


def test_single_giant_word_is_cut_by_characters() -> None:
    chunks = chunk_article(num="L1", hierarchy=[], text="a" * 5000, max_tokens=100)
    assert len(chunks) > 1
    assert all(chunk.token_estimate <= 100 for chunk in chunks)
    assert "".join(chunk.text.split("\n")[1] for chunk in chunks) == "a" * 5000


def test_long_article_splits_on_paragraphs_within_budget() -> None:
    paragraphs = [f"Alinéa {i} " + " ".join(["mot"] * 60) for i in range(30)]
    chunks = chunk_article(
        num="L1", hierarchy=HIERARCHY, text="\n".join(paragraphs), max_tokens=300
    )
    assert len(chunks) > 1
    assert [chunk.ord for chunk in chunks] == list(range(len(chunks)))
    assert all(chunk.token_estimate <= 300 for chunk in chunks)
    # Every chunk carries the header, so each one is findable on its own.
    assert all(chunk.text.startswith("Partie législative") for chunk in chunks)
    # Nothing is lost or duplicated.
    rebuilt = "\n".join(
        line for chunk in chunks for line in chunk.text.split("\n")[1:] if line.startswith("Alinéa")
    )
    assert rebuilt == "\n".join(paragraphs)


def test_single_giant_paragraph_is_cut_at_word_boundaries() -> None:
    giant = " ".join(f"mot{i}" for i in range(2000))
    chunks = chunk_article(num="L1", hierarchy=[], text=giant, max_tokens=200)
    assert len(chunks) > 1
    assert all(chunk.token_estimate <= 200 for chunk in chunks)
    words = " ".join(line for chunk in chunks for line in chunk.text.split("\n")[1:]).split()
    assert words == giant.split()


def test_chunking_is_deterministic() -> None:
    kwargs = {"num": "L1", "hierarchy": HIERARCHY, "text": "a\nb\nc", "nota": "n"}
    assert chunk_article(**kwargs) == chunk_article(**kwargs)  # type: ignore[arg-type]


def test_estimate_tokens_rounds_up() -> None:
    assert estimate_tokens("a" * CHARS_PER_TOKEN) == 1
    assert estimate_tokens("a" * (CHARS_PER_TOKEN + 1)) == 2
    assert estimate_tokens("") == 0
