"""Fragen zu einem genannten Text und zu einer bestimmten Abbildung.

„Beschreibe Abbildung 1 in Ornelas: LLM-Assisted Thematic Analysis …“ – die Suche nach Bedeutung allein findet hier
weder die Bildunterschrift zuverlässig („Fig. 1.“ statt „Abbildung 1“), noch bleibt sie beim genannten Text. Darum:

1. **Genannter Text:** Steht der Titel eines Texts im Suchbereich in der Frage (ganz oder der Haupttitel vor dem
   Doppelpunkt), sucht Zotivity zuerst nur in diesem Text; die übrigen Texte füllen den Rest.
2. **Abbildung nach Nummer:** „Abbildung 1“, „Abb. 2“, „Fig. 3“, „Figure 4a“ → die Abbildungen mit dieser Nummer in der
   Bildunterschrift – im genannten Text (auch aus der vorigen Frage oder über den Namen des Erstautors), sonst im
   ganzen Suchbereich, wenn es höchstens zwei Texte betrifft. Beschrieben: die Beschreibung. Noch nicht beschrieben:
   Bildunterschrift und Text in der Grafik, gekennzeichnet; die Oberfläche bietet „Beschreiben und neu antworten“ an.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from zotivity.extract.chunker import estimate_tokens
from zotivity.figures import LABEL, NOT_A_FIGURE
from zotivity.index.store import Store
from zotivity.rag.sources import Source, author_year

_WORDS = (r"(?:abbildung(?:en)?|abb|figures?|figur(?:en)?|fig|grafik(?:en)?|graphik(?:en)?|schaubild(?:er)?|"
          r"diagramm(?:e)?|charts?|exhibits?)")
_NUM = r"\d{1,3}(?:\.\d{1,2})?[a-z]?"
_ASK = re.compile(rf"\b{_WORDS}\.?\s*({_NUM}(?:\s*(?:,|und|and|&|sowie|oder|or)\s*{_NUM})*)\b", re.IGNORECASE)
_CAPTION = re.compile(rf"^\W*{_WORDS}\.?\s*({_NUM})\b", re.IGNORECASE)
MAX_FIGURES = 4  # je Frage
MAX_AMBIGUOUS_DOCS = 2  # ohne genannten Text: nur, wenn die Nummer in höchstens so vielen Texten vorkommt
NAMED_SHARE = 0.6  # Anteil der Textstellen für den genannten Text


def asked_numbers(text: str) -> list[str]:
    """Abbildungsnummern in einer Frage: „Abbildung 1 und 2“ → ["1", "2"], „Fig. 3a“ → ["3a"]."""
    out: list[str] = []
    for m in _ASK.finditer(text or ""):
        for n in re.findall(_NUM, m.group(1), flags=re.IGNORECASE):
            n = n.lower()
            if n not in out:
                out.append(n)
    return out[:MAX_FIGURES]


def caption_number(caption: str) -> str:
    """Nummer einer Bildunterschrift: „Fig. 1. Overview …“ → "1"; "" ohne Nummer."""
    m = _CAPTION.match(caption or "")
    return m.group(1).lower() if m else ""


def _norm(s: str) -> str:
    return " ".join(re.sub(r"\W+", " ", (s or "").casefold()).split())


def _main_title(title: str) -> str:
    return re.split(r"\s*[:?!]\s+|\s+[–—-]\s+|\.\s+", title or "", maxsplit=1)[0]


def named_docs(store: Store, text: str, keys: list[tuple[str, str]], *, authors: bool = False) -> list[tuple[str, str]]:
    """Texte im Suchbereich, deren Titel in text steht: ganzer Titel (mindestens drei Wörter, 20 Zeichen) oder
    Haupttitel vor dem Doppelpunkt (mindestens fünf Wörter, 35 Zeichen – kürzere wie „LLM-Assisted Thematic
    Analysis“ sind oft einfach das Thema der Frage). Mit authors=True genügt sonst der Nachname des Erstautors (für die
    Abbildungssuche). Mehr als drei Treffer gelten als zufällig: dann keiner."""
    nq = f" {_norm(text)} "
    if len(nq) < 6:
        return []
    by_title: list[tuple[str, str]] = []
    by_author: list[tuple[str, str]] = []
    for key, head in store.item_heads(keys).items():
        full = _norm(head["title"])
        main = _norm(_main_title(head["title"]))
        if (len(full) >= 20 and len(full.split()) >= 3 and f" {full} " in nq) or (
                len(main) >= 35 and len(main.split()) >= 5 and f" {main} " in nq):
            by_title.append(key)
        elif authors:
            last = _norm(head["last_name"])
            if len(last) >= 4 and f" {last} " in nq:
                by_author.append(key)
    for found in (by_title, by_author):
        if found:
            return sorted(found) if len(found) <= 3 else []
    return []


def described(f: dict) -> str:
    """Brauchbare Beschreibung einer Abbildung ("" wenn keine oder „keine inhaltliche Abbildung“)."""
    desc = (f.get("description") or "").strip()
    return "" if not desc or desc.lower().startswith(NOT_A_FIGURE) else desc


@dataclass
class Focus:
    numbers: list[str] = field(default_factory=list)  # gefragte Abbildungsnummern
    named: list[tuple[str, str]] = field(default_factory=list)  # Texte, deren Titel in der Frage steht
    figures: list[dict] = field(default_factory=list)  # passende Abbildungen (mit Beschreibung, falls vorhanden)

    def open_figures(self, store: Store) -> list[dict]:
        """Gefragte Abbildungen ohne Beschreibung – für „Beschreiben und neu antworten“."""
        todo = [f for f in self.figures if f.get("description") is None]
        if not todo:
            return []
        items = store.items({(f["lib"], f["item_key"]) for f in todo})
        return [{"fkey": f["fkey"], "lib": f["lib"], "item_key": f["item_key"], "page": f["page"],
                 "caption": (f.get("caption") or "")[:200],
                 "citation": author_year(items.get((f["lib"], f["item_key"])) or {})} for f in todo]


def focus(store: Store, question: str, history: list[dict], keys: list[tuple[str, str]]) -> Focus:
    """Genannte Texte und gefragte Abbildungen einer Frage (keys: Texte im Suchbereich)."""
    if not keys:
        return Focus()
    out = Focus(numbers=asked_numbers(question), named=named_docs(store, question, keys))
    if not out.numbers:
        return out
    where = out.named or named_docs(store, question, keys, authors=True)
    if not where:  # Folgefrage („Und Abbildung 2?“): Text aus den vorigen Fragen
        for m in reversed([m for m in history if m.get("role") == "user"][-3:]):
            where = named_docs(store, m.get("content") or "", keys, authors=True)
            if where:
                break
    figs = [f for f in store.figures_of_items(where or keys) if caption_number(f.get("caption") or "") in out.numbers]
    if not where and len({(f["lib"], f["item_key"]) for f in figs}) > MAX_AMBIGUOUS_DOCS:
        figs = []  # „Was zeigt Abbildung 1?“ in einem grossen Korpus: unklar, welcher Text gemeint ist
    order = {n: i for i, n in enumerate(out.numbers)}
    figs.sort(key=lambda f: (order.get(caption_number(f.get("caption") or ""), 99), f["lib"], f["item_key"], f["n"]))
    out.figures = figs[:MAX_FIGURES]
    return out


def _caption_passage(store: Store, f: dict) -> dict | None:
    """Textabschnitt der Seite mit der Bildunterschrift (dort steht meist auch, was die Abbildung erklärt)."""
    rows = store.con.execute(
        "SELECT * FROM chunks WHERE lib=? AND att_key=? AND kind='text' AND page_start<=? AND page_end>=? ORDER BY ord",
        (f["lib"], f["att_key"], f["page"], f["page"])).fetchall()
    if not rows:
        return None
    head = " ".join((f.get("caption") or "").split())[:40]
    for r in rows:
        if head and head in " ".join(r["text"].split()):
            return dict(r)
    return dict(rows[0])


def figure_sources(store: Store, figs: list[dict], skip_chunks: set[int]) -> list[Source]:
    """Quellen zu den gefragten Abbildungen: je Abbildung die Beschreibung (oder Bildunterschrift und Text in der
    Grafik) und der Textabschnitt mit der Bildunterschrift, sofern er nicht schon unter den Textstellen ist."""
    if not figs:
        return []
    items = store.items({(f["lib"], f["item_key"]) for f in figs})
    out: list[Source] = []
    seen = set(skip_chunks)
    for i, f in enumerate(figs):
        item = items.get((f["lib"], f["item_key"])) or {}
        labels = store.page_labels(f["lib"], f["att_key"])
        label = labels[f["page"]] if labels and f["page"] < len(labels) else ""
        att = store.attachment(f["lib"], f["att_key"]) or {}
        caption = f.get("caption") or ""
        desc = described(f)
        if desc:
            text, kind = f"{LABEL}" + (f": {caption}" if caption else "") + f"\n{desc}", "figure"
            row = store.con.execute("SELECT id FROM chunks WHERE lib=? AND att_key=? AND kind='figure' AND page_start=?"
                                    " AND text=?", (f["lib"], f["att_key"], f["page"], text)).fetchone()
            chunk_id = row[0] if row else -(10000 + i)
        else:
            parts = [f"Bildunterschrift: {caption or '(keine)'}"]
            if (f.get("text") or "").strip():
                parts.append(f"Text in der Abbildung: {f['text'].strip()[:800]}")
            text, kind, chunk_id = "\n".join(parts), "figure_open", -(10000 + i)
        if chunk_id in seen:
            continue
        seen.add(chunk_id)
        common = {"lib": f["lib"], "item_key": f["item_key"], "att_key": f["att_key"],
                  "title": item.get("title") or "(ohne Titel)", "citation": author_year(item), "score": 0.0,
                  "relevance": None, "content_type": att.get("content_type") or ""}
        out.append(Source(n=0, chunk_id=chunk_id, page_label=label, page_index=f["page"], text=text, kind=kind,
                          **common))
        passage = _caption_passage(store, f)
        if passage and passage["id"] not in seen:
            seen.add(passage["id"])
            out.append(Source(n=0, chunk_id=passage["id"], page_label=passage.get("page_label") or "",
                              page_index=passage.get("page_start"), text=passage["text"], kind="text", **common))
    return out


def combine(first: list[Source], rest: list[Source], budget: int) -> list[Source]:
    """Quellen zusammenführen und neu nummerieren; first immer, rest bis zum Budget (doppelte Abschnitte weg)."""
    used = sum(estimate_tokens(s.text) for s in first)
    ids = {s.chunk_id for s in first}
    out = list(first)
    for s in rest:
        if s.chunk_id in ids:
            continue
        t = estimate_tokens(s.text)
        if out and used + t > budget:
            break
        used += t
        ids.add(s.chunk_id)
        out.append(s)
    ranked = [s for s in out if s.relevance]
    for k, s in enumerate(out, start=1):
        s.n = k
    for k, s in enumerate(ranked, start=1):
        s.relevance["rank"], s.relevance["of"] = k, len(ranked)
    return out
