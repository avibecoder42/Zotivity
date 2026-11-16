"""Frageart und Überblicksfragen mit einer Karte je Text (Stufe 3 des Suchplans)."""

import json

import pytest
from fakes import ScriptedModel
from test_indexing_search import SOCIAL, TEEN, build_library, run_index
from test_rag import install_models

from zotivity.rag import AskOptions, ChatService
from zotivity.rag.cards import build_cards, card_text
from zotivity.rag.chat import generate_queries
from zotivity.rag.plan import scope_docs


@pytest.fixture
def library(zengine, fake_zotero):
    build_library(fake_zotero)
    for i in range(3):  # fünf Texte: Überblicksfragen bekommen Karten (ab vier Texten)
        fake_zotero.add_item(f"ITEMX00{i}", f"Weitere Studie {i}", f"Autor{i}", str(2019 + i),
                             abstract=f"Abstract der Studie {i} über Jugendliche und Chatbots.")
        fake_zotero.add_pdf(f"ATTX000{i}", f"ITEMX00{i}", [(TEEN if i % 2 else SOCIAL) * 4])
    run_index(zengine)
    return zengine


def test_generate_queries_returns_question_type():
    model = ScriptedModel([("Suchanfragen", json.dumps({"type": "Overview", "queries": ["a", "b", "c"]}))])
    assert generate_queries(model, "Welche Methoden nutzen die Studien?", [], 2, "low") == (["a", "b"], "overview")
    model = ScriptedModel([("Suchanfragen", json.dumps({"type": "anders", "queries": ["a"]}))])
    assert generate_queries(model, "Frage", [], 2, "low")[1] == ""


def test_cards_one_per_text_with_sheet(library):
    library.store.put_sheet("users/0", "ITEMA001", {"Methode": "Befragung", "Stichprobe": "1200 Jugendliche"}, "m")
    docs = scope_docs(library, library.make_filter({}))
    cards = build_cards(library, docs, 30000)
    assert len(cards) == 5 and [c.n for c in cards] == [1, 2, 3, 4, 5]
    assert cards[0].citation.endswith("2024")  # neueste zuerst
    a = next(c for c in cards if c.item_key == "ITEMA001")
    assert "Abstract:" in a.text and "Auswertungsblatt: Methode: Befragung | Stichprobe: 1200 Jugendliche" in a.text
    assert a.kind == "card" and a.page_index is None
    assert len(build_cards(library, docs, 10)) == 1  # Budget: mindestens eine Karte
    assert card_text({"item_type": "book"}, "", None) == "book"


def test_overview_question_uses_cards(library, monkeypatch):
    helper = ScriptedModel([("Suchanfragen", json.dumps({"type": "overview", "queries": ["Methoden Studien"]}))])
    main = ScriptedModel([("Karten der Texte", "Zwei Studien befragen Jugendliche [1][3].")])
    install_models(monkeypatch, helper, main)
    library.settings_store.update({"retrieval": {"fulltext_limits": {"anthropic": 50}}})  # keine ganzen Texte
    events = list(ChatService(library).ask("Welche Methoden verwenden die Studien?", None,
                                           AskOptions(scope="auto")))
    qt = next(e for e in events if e["type"] == "qtype")
    assert qt["qtype"] == "overview" and qt["detected"] == "overview" and qt["chosen"] == "auto"
    sources = next(e for e in events if e["type"] == "sources")["sources"]
    assert len(sources) == 5 and all(s["kind"] == "card" for s in sources)
    assert "Karten der Texte im Suchbereich (5 von 5)" in main.calls[0] and "Überblicks- oder Vergleichsfrage" in main.calls[0]
    assert events[-1]["basis"] == {"mode": "cards", "cards": 5, "scope_docs": 5, "qtype": "overview"}

    # Frageart von Hand: Detail → wieder Textstellen, obwohl das Hilfsmodell „overview“ meldet
    main.rules.insert(0, ("Textstellen aus der Bibliothek", "Antwort [1]."))
    events = list(ChatService(library).ask("Welche Methoden?", None, AskOptions(scope="auto", qtype="detail")))
    assert events[-1]["basis"]["mode"] == "passages" and events[-1]["basis"]["qtype"] == "detail"
