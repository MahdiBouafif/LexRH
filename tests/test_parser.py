from datetime import date
from pathlib import Path

import pytest

from lexrh.rag.parser import Article, ParseError, parse_article, parse_article_file

FIXTURES = Path(__file__).parent / "fixtures"


def load(legal_id: str) -> Article:
    return parse_article_file(FIXTURES / f"{legal_id}.xml")


def test_simple_article_fields() -> None:
    article = load("LEGIARTI000050668722")
    assert article.legal_id == "LEGIARTI000050668722"
    assert article.num == "22"
    assert article.etat == "VIGUEUR_DIFF"
    assert article.valid_from == date(2024, 12, 1)
    assert article.valid_to is None  # 2999-01-01 means "no end"
    assert article.article_type == "AUTONOME"
    assert article.is_indexable


def test_text_is_plain_and_keeps_line_breaks() -> None:
    article = load("LEGIARTI000050668722")
    assert "<" not in article.text
    lines = article.text.split("\n")
    assert lines[0].startswith("Un arrêté du ministre de la défense")
    assert lines[1].startswith("Le nombre d'emplois ouverts")


def test_numbered_list_items_end_up_on_separate_lines() -> None:
    article = load("LEGIARTI000050668720")
    lines = article.text.split("\n")
    assert any(line.startswith("1° Parmi les élèves") for line in lines)
    assert any(line.startswith("2° Au titre de la promotion interne") for line in lines)


def test_hierarchy_follows_nested_sections() -> None:
    article = load("LEGIARTI000050668722")
    assert article.hierarchy == (
        "Titre II : DISPOSITIONS PORTANT STATUT DU CORPS DES ADMINISTRATEURS "
        "DE LA DIRECTION GÉNÉRALE DE LA SÉCURITÉ EXTÉRIEURE",
        "Chapitre Ier : Recrutement",
    )


def test_consolidated_text_is_the_legitext_not_the_jorf_one() -> None:
    article = load("LEGIARTI000050668722")
    assert article.text_id == "LEGITEXT000050668674"
    assert article.text_title is not None
    assert article.text_title.startswith("Décret n° 2024-1068")


def test_placeholder_start_date_means_not_indexable() -> None:
    article = load("LEGIARTI000050668736")
    assert article.valid_from is None
    assert article.valid_to is None
    assert article.etat is None  # <ETAT/> is empty
    assert article.article_type == "ENTIEREMENT_MODIF"
    assert not article.is_indexable


def test_nested_blockquotes_are_flattened() -> None:
    article = load("LEGIARTI000050668736")
    assert "Décret n° 2010-1693 du 30 décembre 2010" in article.text
    assert "<" not in article.text


def test_content_hash_is_stable_and_sensitive_to_changes() -> None:
    a = load("LEGIARTI000050668722")
    b = load("LEGIARTI000050668722")
    assert a.content_hash == b.content_hash
    changed = a.model_copy(update={"text": a.text + " modifié"})
    assert changed.content_hash != a.content_hash


def test_wrong_root_raises() -> None:
    with pytest.raises(ParseError):
        parse_article("<TEXTE_VERSION><META/></TEXTE_VERSION>")


def test_malformed_xml_raises() -> None:
    with pytest.raises(ParseError):
        parse_article("<ARTICLE><META>")


def test_missing_id_raises() -> None:
    with pytest.raises(ParseError):
        parse_article("<ARTICLE><META><META_COMMUN/></META></ARTICLE>")


def test_invalid_date_raises() -> None:
    xml = (
        "<ARTICLE><META><META_COMMUN><ID>X</ID></META_COMMUN><META_SPEC><META_ARTICLE>"
        "<DATE_DEBUT>not-a-date</DATE_DEBUT></META_ARTICLE></META_SPEC></META></ARTICLE>"
    )
    with pytest.raises(ParseError):
        parse_article(xml)


# --- Real Code du travail articles (old regulatory part, several versions of D122-12) ---

CODE_TRAVAIL = "LEGITEXT000006072050"


def test_code_du_travail_article_identity_and_hierarchy() -> None:
    article = load("LEGIARTI000006644105")
    assert article.num == "D122-12"
    assert article.text_id == CODE_TRAVAIL
    assert article.text_title == "Code du travail"
    assert article.hierarchy == (
        "Partie réglementaire ancienne - Décrets simples",
        "Livre Ier : Conventions relatives au travail",
        "Titre II : CONTRAT DE TRAVAIL",
        "Chapitre II : REGLES PROPRES AU CONTRAT DE TRAVAIL",
        "Section 2 : Résiliation du contrat de travail à durée indéterminée.",
    )


@pytest.mark.parametrize(
    ("legal_id", "etat", "start", "end"),
    [
        ("LEGIARTI000006644103", "TRANSFERE", date(1981, 10, 30), date(1989, 11, 28)),
        ("LEGIARTI000006644104", "TRANSFERE", date(1989, 11, 28), date(1991, 8, 2)),
        ("LEGIARTI000006644105", "MODIFIE", date(1991, 8, 2), date(2005, 1, 1)),
    ],
)
def test_historical_versions_keep_real_validity_ranges(
    legal_id: str, etat: str, start: date, end: date
) -> None:
    article = load(legal_id)
    assert article.etat == etat
    assert article.valid_from == start
    assert article.valid_to == end  # real end dates are kept, only 2999 becomes None
    assert article.is_indexable


def test_versions_of_the_same_article_are_distinct_documents() -> None:
    ids = {load(i).legal_id for i in ("LEGIARTI000006644103", "LEGIARTI000006644104")}
    hashes = {load(i).content_hash for i in ("LEGIARTI000006644103", "LEGIARTI000006644104")}
    assert len(ids) == 2
    assert len(hashes) == 2


def test_old_text_is_cleaned_of_padding_and_blank_lines() -> None:
    article = load("LEGIARTI000006644103")
    lines = article.text.split("\n")
    assert lines[0].startswith("Si, dans son opposition, l'employeur prétend")
    assert all(line == line.strip() and line for line in lines)  # no padding, no blank lines
    assert "100 F à 10.000 F" in article.text


# --- Current articles: L1234-1 (notice), L3141-3 (paid leave), L1234-9 (severance) ---


def test_current_article_l1234_1() -> None:
    article = load("LEGIARTI000006901112")
    assert article.num == "L1234-1"
    assert article.etat == "VIGUEUR"
    assert article.valid_from == date(2008, 5, 1)
    assert article.valid_to is None
    assert len(article.hierarchy) == 7
    assert article.hierarchy[0] == "Partie législative"  # trailing spaces stripped
    assert (
        article.hierarchy[-1] == "Sous-section 1 : Préavis et indemnité compensatrice de préavis."
    )
    lines = article.text.split("\n")
    assert len(lines) == 5
    assert lines[0].startswith("Lorsque le licenciement n'est pas motivé par une faute grave")
    assert lines[1].startswith("1° S'il justifie")
    assert lines[3].startswith("3° S'il justifie")
    assert article.nota == ""  # the NOTA block only holds empty <br/> tags


def test_renamed_section_uses_the_wording_valid_at_the_article_start_date() -> None:
    article = load("LEGIARTI000033020826")
    # The chapter lists two "Section 2" titles: the 2008-2016 one ends with a dot.
    assert article.hierarchy[-2:] == (
        "Section 2 : Durée du congé",
        "Sous-section 1 : Ordre public.",
    )
    assert article.hierarchy[1].endswith("épargne salariale")  # no trailing padding


def test_section_wording_follows_the_date_when_the_article_is_older() -> None:
    raw = (FIXTURES / "LEGIARTI000033020826.xml").read_text(encoding="utf-8")
    older = raw.replace(
        "<DATE_DEBUT>2016-08-10</DATE_DEBUT>", "<DATE_DEBUT>2010-01-01</DATE_DEBUT>"
    )
    article = parse_article(older)
    assert "Section 2 : Durée du congé." in article.hierarchy


def test_paragraph_text_has_no_leading_padding() -> None:
    article = load("LEGIARTI000033020826")
    assert article.text.split("\n") == [
        "Le salarié a droit à un congé de deux jours et demi ouvrables par mois de travail "
        "effectif chez le même employeur.",
        "La durée totale du congé exigible ne peut excéder trente jours ouvrables.",
    ]


def test_nota_is_kept_and_changes_the_hash() -> None:
    article = load("LEGIARTI000035644154")
    assert article.num == "L1234-9"
    assert article.valid_from == date(2017, 9, 24)
    assert "8 mois d'ancienneté" in article.text
    assert article.nota.startswith("Conformément à l'article 40-I de l'ordonnance")
    changed = article.model_copy(update={"nota": ""})
    assert changed.content_hash != article.content_hash
