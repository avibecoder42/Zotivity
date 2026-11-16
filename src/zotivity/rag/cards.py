"""Überblicks- und Vergleichsfragen: von jedem Text im Suchbereich eine kurze Karte statt einzelner Textstellen.

Eine Karte enthält Autor/Jahr, Titel, Art und Ort der Veröffentlichung, das Abstract und – wo es eines gibt – das
Auswertungsblatt aus einem Forschungsstand. Rund 300 Tokens je Text: 100 Texte sind etwa 30 000 Tokens, und jeder
Text kommt vor.
"""

from __future__ import annotations

from zotivity.engine import Engine
from zotivity.extract.chunker import estimate_tokens
from zotivity.rag.plan import ScopeDoc
from zotivity.rag.sources import Source, author_year

QUESTION_TYPES = ("detail", "overview", "evidence")
CARD_CHARS = 1400  # etwa 350 Tokens je Karte
MAX_CARDS = 400


def _clip(text: str, n: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + " …"


def _sheet_text(sheet: dict | None) -> str:
    if not sheet:
        return ""
    data = sheet.get("sheet") if isinstance(sheet.get("sheet"), dict) else sheet
    parts = []
    for k, v in (data or {}).items():
        if isinstance(v, (list, tuple)):
            v = "; ".join(str(x) for x in v)
        if v not in (None, "", [], {}):
            parts.append(f"{k}: {v}")
    return " | ".join(parts)


def card_text(item: dict, abstract: str, sheet: dict | None) -> str:
    meta = [item.get("item_type") or "", item.get("publication") or ""]
    lines = [", ".join(m for m in meta if m)]
    if abstract:
        lines.append("Abstract: " + _clip(abstract, 900))
    sheet_txt = _sheet_text(sheet)
    if sheet_txt:
        lines.append("Auswertungsblatt: " + _clip(sheet_txt, 900))
    return _clip("\n".join(x for x in lines if x), CARD_CHARS)


def build_cards(engine: Engine, docs: list[ScopeDoc], token_budget: int) -> list[Source]:
    """Je Text eine Karte als Quelle (kind „card“), bis das Budget erreicht ist."""
    store = engine.store
    items = store.items({(d.lib, d.item_key) for d in docs[:MAX_CARDS]})
    # Neueste zuerst (reicht das Budget nicht, fehlen die ältesten)
    keyed = sorted(docs[:MAX_CARDS], key=lambda d: (-int((items.get((d.lib, d.item_key)) or {}).get("year") or 0),
                                                    d.item_key))
    cards: list[Source] = []
    used = 0
    for d in keyed:
        item = items.get((d.lib, d.item_key)) or {}
        abstract = next((r["text"] for r in store.document_chunks(d.lib, d.item_key) if r["kind"] == "abstract"), "")
        if not abstract:
            abstract = item.get("abstract") or ""
        text = card_text(item, abstract, store.get_sheet(d.lib, d.item_key))
        tokens = estimate_tokens(text) + 30
        if cards and used + tokens > token_budget:
            break
        used += tokens
        cards.append(Source(
            n=len(cards) + 1, chunk_id=-(len(cards) + 1), lib=d.lib, item_key=d.item_key, att_key="",
            title=item.get("title") or "(ohne Titel)", citation=author_year(item), page_label="", page_index=None,
            text=text, kind="card", score=0.0, relevance=None))
    return cards
