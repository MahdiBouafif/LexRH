"""Parser for DILA LEGI article XML (one <ARTICLE> file per article version).

What the real files taught us (see tests/fixtures):
- Dates use 2999-01-01 as a placeholder: as an end date it means "no end"
  (still applicable), as a start date it means "no real start" (the version
  exists only as a pointer, for example the JORF original of a modifying text).
- <ETAT/> can be empty. ETAT labels such as VIGUEUR_DIFF are not reliable for
  "as of" filtering, so retrieval must filter on the dates, not on the label.
- The text sits in BLOC_TEXTUEL/CONTENU as light HTML (<p>, <br/>, <blockquote>).
- The legal hierarchy is a chain of nested <TM>/<TITRE_TM> elements. A section can
  carry several TITRE_TM (renamed over time): pick the one valid at the article's start date.
- NOTA/CONTENU holds applicability notes (for example which dismissals a rule applies to).
"""

import hashlib
import re
from datetime import date
from pathlib import Path
from xml.etree.ElementTree import Element  # noqa: S405  (type only; parsing uses defusedxml)

from defusedxml import ElementTree as SafeET
from pydantic import BaseModel, ConfigDict

PLACEHOLDER_DATE = date(2999, 1, 1)
_BLOCK_TAGS = {"p", "blockquote", "div", "li", "ul", "ol", "table", "tr"}


class ParseError(ValueError):
    """The XML is not a valid LEGI article."""


class Article(BaseModel):
    model_config = ConfigDict(frozen=True)

    legal_id: str  # LEGIARTI...
    num: str | None  # article number as printed, e.g. "L1234-1" or "22"
    etat: str | None  # VIGUEUR, MODIFIE, ABROGE... (informational only)
    valid_from: date | None  # None if the file only carries the 2999 placeholder
    valid_to: date | None  # None = no end date
    article_type: str | None
    text_id: str | None  # LEGITEXT... of the consolidated text (code or decree)
    text_title: str | None
    hierarchy: tuple[str, ...]  # section titles from top level down
    text: str  # plain text, line breaks preserved
    nota: str  # applicability note (NOTA), often empty but legally relevant

    @property
    def is_indexable(self) -> bool:
        """Only versions with a real start date and some text can be retrieved."""
        return self.valid_from is not None and bool(self.text.strip())

    @property
    def content_hash(self) -> str:
        """Changes when anything retrieval depends on changes (idempotent re-ingestion)."""
        parts = [
            self.text,
            self.nota,
            str(self.valid_from),
            str(self.valid_to),
            self.etat or "",
            " > ".join(self.hierarchy),
        ]
        return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def _text_of(root: Element, path: str) -> str | None:
    node = root.find(path)
    if node is None or node.text is None:
        return None
    value = node.text.strip()
    return value or None


def _parse_date(value: str | None, *, placeholder_means_none: bool = True) -> date | None:
    if value is None:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ParseError(f"Invalid date: {value!r}") from exc
    if placeholder_means_none and parsed == PLACEHOLDER_DATE:
        return None
    return parsed


def _html_to_text(node: Element) -> str:
    """Flatten the light HTML of CONTENU to text, keeping line breaks."""
    out: list[str] = []

    def walk(element: Element) -> None:
        if element.text:
            out.append(element.text)
        for child in element:
            tag = child.tag
            if tag == "br":
                out.append("\n")
            elif tag in _BLOCK_TAGS:
                out.append("\n")
                walk(child)
                out.append("\n")
            else:
                walk(child)
            if child.tail:
                out.append(child.tail)

    walk(node)
    raw = "".join(out).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in raw.split("\n")]
    return "\n".join(line for line in lines if line)


def _title_covers(title: Element, as_of: date | None) -> bool:
    """True if this TITRE_TM was the valid wording of the section on that date."""
    if as_of is None:
        return False
    begin = _parse_date(title.get("debut"), placeholder_means_none=False)
    end = _parse_date(title.get("fin"), placeholder_means_none=False)
    return (begin is None or begin <= as_of) and (end is None or as_of < end)


def _pick_title(node: Element, as_of: date | None) -> str | None:
    """A section can list several wordings (renamed over time): take the one valid at as_of."""
    titles = node.findall("TITRE_TM")
    if not titles:
        return None
    chosen = next((t for t in titles if _title_covers(t, as_of)), titles[-1])
    if not chosen.text:
        return None
    return re.sub(r"\s+", " ", chosen.text).strip() or None


def _hierarchy(root: Element, as_of: date | None) -> tuple[str, ...]:
    """Follow the nested <TM> chain under CONTEXTE/TEXTE, using the wording valid at as_of."""
    titles: list[str] = []
    node = root.find("CONTEXTE/TEXTE/TM")
    while node is not None:
        title = _pick_title(node, as_of)
        if title:
            titles.append(title)
        children = node.findall("TM")
        # Several child branches can exist (one per wording): follow the one valid at as_of.
        node = next(
            (c for c in children if any(_title_covers(t, as_of) for t in c.findall("TITRE_TM"))),
            children[0] if children else None,
        )
    return tuple(titles)


def _consolidated_text(root: Element) -> tuple[str | None, str | None]:
    """The TITRE_TXT whose id starts with LEGITEXT is the consolidated code or decree."""
    for title in root.findall("CONTEXTE/TEXTE/TITRE_TXT"):
        text_id = title.get("id_txt")
        if text_id and text_id.startswith("LEGITEXT"):
            label = re.sub(r"\s+", " ", title.text or "").strip() or None
            return text_id, label
    return None, None


def parse_article(xml: bytes | str) -> Article:
    try:
        root = SafeET.fromstring(xml)
    except Exception as exc:
        raise ParseError(f"Not well-formed XML: {exc}") from exc
    if root.tag != "ARTICLE":
        raise ParseError(f"Expected <ARTICLE> root, got <{root.tag}>")

    legal_id = _text_of(root, "META/META_COMMUN/ID")
    if legal_id is None:
        raise ParseError("Missing article ID")

    content = root.find("BLOC_TEXTUEL/CONTENU")
    nota = root.find("NOTA/CONTENU")
    text_id, text_title = _consolidated_text(root)
    valid_from = _parse_date(_text_of(root, "META/META_SPEC/META_ARTICLE/DATE_DEBUT"))
    return Article(
        legal_id=legal_id,
        num=_text_of(root, "META/META_SPEC/META_ARTICLE/NUM"),
        etat=_text_of(root, "META/META_SPEC/META_ARTICLE/ETAT"),
        valid_from=valid_from,
        valid_to=_parse_date(_text_of(root, "META/META_SPEC/META_ARTICLE/DATE_FIN")),
        article_type=_text_of(root, "META/META_SPEC/META_ARTICLE/TYPE"),
        text_id=text_id,
        text_title=text_title,
        hierarchy=_hierarchy(root, valid_from),
        text=_html_to_text(content) if content is not None else "",
        nota=_html_to_text(nota) if nota is not None else "",
    )


def parse_article_file(path: Path) -> Article:
    return parse_article(path.read_bytes())
