import shutil
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"

# The twelve articles the ingestion tests were written against. Add your own fixtures to
# tests/fixtures freely: tests that count files use this fixed set, so they keep passing.
KNOWN_ARTICLES = [
    "LEGIARTI000006644100",  # Code du travail D122-11, TRANSFERE
    "LEGIARTI000006644101",  # D122-11, MODIFIE
    "LEGIARTI000006644103",  # D122-12, TRANSFERE
    "LEGIARTI000006644104",  # D122-12, TRANSFERE
    "LEGIARTI000006644105",  # D122-12, MODIFIE
    "LEGIARTI000006901112",  # L1234-1, VIGUEUR
    "LEGIARTI000033020826",  # L3141-3, VIGUEUR
    "LEGIARTI000035644154",  # L1234-9, VIGUEUR
    "LEGIARTI000050668716",  # DGSE decree (another text), VIGUEUR_DIFF
    "LEGIARTI000050668720",  # DGSE decree, VIGUEUR_DIFF
    "LEGIARTI000050668722",  # DGSE decree, VIGUEUR_DIFF
    "LEGIARTI000050668736",  # DGSE decree, placeholder start date
]


@pytest.fixture
def known_articles(tmp_path: Path) -> Path:
    """A temporary folder holding exactly the twelve known articles."""
    folder = tmp_path / "known_articles"
    folder.mkdir()
    for legal_id in KNOWN_ARTICLES:
        shutil.copy(FIXTURES / f"{legal_id}.xml", folder / f"{legal_id}.xml")
    return folder
