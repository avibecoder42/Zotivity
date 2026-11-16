"""Ganze Texte für „Fragen“: Texte mit Seitenmarken [S. x] für das Sprachmodell, Belege [1, S. 64] → Textstellen.

Das Modell belegt mit Textnummer und gedruckter Seite. Nach der Antwort wird jeder Beleg zu einer nummerierten Quelle
(Text und Seite, mit dem Text dieser Seite) und im Antworttext zu [n] – so funktionieren Belegknöpfe, Kopieren und
Exporte wie bei Textstellen, und „PDF öffnen“ springt zur PDF-Seite.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from zotivity.engine import Engine
from zotivity.extract.chunker import estimate_tokens
from zotivity.index.store import Store
from zotivity.rag.sources import Source, author_year
from zotivity.zotero import opens_in_reader, zotero_open_pdf_url, zotero_select_url


@dataclass
class WholeText:
    n: int
    lib: str
    item_key: str
    item: dict
    text: str  # mit Seitenmarken
    tokens: int
    att_key: str
    content_type: str
    labels: list[str] | None  # gedruckte Seitenzahl je PDF-Seite (None: Index ohne Seitenzahl-Erkennung)
    rows: list[dict]  # Textabschnitte in Lesereihenfolge

    @property
    def citation(self) -> str:
        return author_year(self.item)

    @property
    def title(self) -> str:
        return self.item.get("title") or "(ohne Titel)"

    def as_dict(self) -> dict:
        d = {"n": self.n, "lib": self.lib, "item_key": self.item_key, "citation": self.citation, "title": self.title,
             "tokens": self.tokens, "zotero_select": zotero_select_url(self.lib, self.item_key)}
        if self.att_key and opens_in_reader(self.content_type):
            d["zotero_pdf"] = zotero_open_pdf_url(self.lib, self.att_key, None)
        return d


def load_texts(engine: Engine, keys: list[tuple[str, str]]) -> list[WholeText]:
    """Die ganzen Texte (Text und Abstract) mit Seitenmarken, wie für die Auswertungsblätter."""
    from zotivity.rag.review import ReviewService  # review.py importiert sources.py: erst hier laden

    store = engine.store
    reviews = ReviewService(engine)
    out: list[WholeText] = []
    for lib, key in keys:
        text, _ = reviews.document_text(lib, key, None, 10**12, 0)
        if not text:
            continue
        rows = [r for r in store.document_chunks(lib, key) if r["kind"] == "text"]
        att_key = rows[0]["att_key"] if rows else ""
        att = store.attachment(lib, att_key) if att_key else None
        out.append(WholeText(
            n=len(out) + 1, lib=lib, item_key=key, item=store.item(lib, key) or {}, text=text,
            tokens=estimate_tokens(text), att_key=att_key, content_type=(att or {}).get("content_type") or "",
            labels=store.page_labels(lib, att_key) if att_key else None, rows=rows))
    return out


def format_texts(texts: list[WholeText]) -> str:
    return "\n\n".join(f"=== [{w.n}] {w.citation}: {w.title} ===\n\n{w.text}\n\n=== Ende von [{w.n}] ===" for w in texts)


# ------------------------------------------------------------------ Belege
_GROUP = re.compile(r"\[([^\[\]\n]{1,300})\]")
_PART = re.compile(r"^\s*(\d{1,3})\s*(?:,\s*(.+?))?\s*$")
_PAGE_PREFIX = re.compile(r"^(?:S\.|Seiten?|pp?\.|pages?)\s*", re.IGNORECASE)
_NO_PAGE = re.compile(r"^(?:o\.?\s*S|n\.?\s*p(?:ag)?)\.?$", re.IGNORECASE)


def _refs(inner: str, valid: set[int]) -> list[tuple[int, str]] | None:
    """„1, S. 64; 2, o. S.“ → [(1, '64'), (2, 'o. S.')]; None, wenn es kein Beleg auf die Texte ist."""
    from zotivity.rag.review import NO_PAGE

    refs: list[tuple[int, str]] = []
    for part in inner.split(";"):
        m = _PART.match(part)
        if not m or int(m.group(1)) not in valid:
            return None
        rest = (m.group(2) or "").strip()
        # „[1, 2]“: zwei Texte (ohne „S.“ ist eine einzelne Zahl hinter dem Komma eine Textnummer)
        if rest and re.fullmatch(r"\d{1,3}(?:\s*,\s*\d{1,3})*", rest) and all(int(x) in valid for x in re.findall(r"\d+", rest)):
            refs.append((int(m.group(1)), ""))
            refs.extend((int(x), "") for x in re.findall(r"\d+", rest))
            continue
        page = _PAGE_PREFIX.sub("", rest).strip().rstrip(",")
        if _NO_PAGE.match(page):
            page = NO_PAGE
        elif rest and not _PAGE_PREFIX.match(rest) and not re.match(r"^[\dIVXLCivxlc]", page):
            return None  # z. B. „[1, siehe oben]“ – kein Beleg
        refs.append((int(m.group(1)), page))
    return refs


def _first_page(page: str) -> str:
    first = re.split(r"\s*[–—-]\s*", page)[0]
    return re.sub(r"\s*f{1,2}\.?$", "", first).strip()


def _page_index(w: WholeText, page: str) -> int | None:
    first = _first_page(page)
    if not first:
        return None
    if w.labels is not None:
        return w.labels.index(first) if first in w.labels else None
    for r in w.rows:  # Index ohne Seitenzahl-Erkennung: Seitenangabe der Abschnitte
        if (r.get("page_label") or "").split("–")[0] == first:
            return r.get("page_start")
    return None


def _page_text(w: WholeText, page_index: int | None, pages: int = 1, max_chars: int = 2400) -> str:
    """Text der zitierten PDF-Seite(n) für die Quellenliste."""
    from zotivity.rag.review import _pages_of, _strip_overlap

    if page_index is None:
        return ""
    wanted = set(range(page_index, page_index + max(1, min(pages, 3))))
    parts: list[str] = []
    prev = None
    for r in w.rows:
        start, end = r.get("page_start"), r.get("page_end")
        if start is None or end is None or end < page_index or start > max(wanted):
            prev = None
            continue
        contiguous = prev is not None and r["att_key"] == prev["att_key"] and r["ord"] == prev["ord"] + 1
        text = _strip_overlap(prev["text"], r["text"]) if contiguous else r["text"]
        shift = len(r["text"]) - len(text)
        parts.extend(piece.strip() for piece, p in _pages_of(text, r, shift) if p in wanted)
        prev = r
    out = " ".join(p for p in parts if p)
    return out if len(out) <= max_chars else out[:max_chars].rsplit(" ", 1)[0] + " …"


def _page_span(page: str) -> int:
    nums = re.findall(r"\d+", page)
    if len(nums) >= 2 and nums[1].isdigit() and nums[0].isdigit() and 0 <= int(nums[1]) - int(nums[0]) < 3:
        return int(nums[1]) - int(nums[0]) + 1
    return 2 if re.search(r"\df\.?$", page) else 1


def resolve_citations(answer: str, texts: list[WholeText], store: Store) -> tuple[str, list[Source]]:
    """Belege [1, S. 64] → Quellen (je Text und Seite eine) und [n] im Text."""
    from zotivity.rag.review import NO_PAGE

    by_n = {w.n: w for w in texts}
    valid = set(by_n)
    sources: list[Source] = []
    numbers: dict[tuple[int, str], int] = {}

    def source_for(n: int, page: str) -> int:
        key = (n, page)
        if key not in numbers:
            w = by_n[n]
            idx = _page_index(w, page) if page and page != NO_PAGE else None
            sources.append(Source(
                n=len(sources) + 1, chunk_id=-(len(sources) + 1), lib=w.lib, item_key=w.item_key, att_key=w.att_key,
                title=w.title, citation=w.citation, page_label="" if page == NO_PAGE else page, page_index=idx,
                text=_page_text(w, idx, _page_span(page)) if idx is not None else "", kind="text", score=0.0,
                relevance=None, content_type=w.content_type))
            numbers[key] = len(sources)
        return numbers[key]

    def repl(m: re.Match) -> str:
        refs = _refs(m.group(1), valid)
        if not refs:
            return m.group(0)
        nums = list(dict.fromkeys(source_for(n, page) for n, page in refs))
        return "[" + ", ".join(str(x) for x in nums) + "]"

    return _GROUP.sub(repl, answer), sources


def source_dicts(sources: list[Source]) -> list[dict]:
    return [{**s.as_dict(), "whole": True} for s in sources]
