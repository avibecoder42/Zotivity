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
    cards = [s for s in sources if s["kind"] == "card"]
    passages = sources[len(cards):]
    # Karten zuerst, dann die passendsten Textstellen aus den Volltexten, durchgehend nummeriert, ohne Abstracts
    assert len(cards) == 5 and sources[:5] == cards and passages and all(s["kind"] == "text" for s in passages)
    assert [s["n"] for s in sources] == list(range(1, len(sources) + 1))
    assert all(s["page_index"] is not None for s in passages)
    prompt = main.calls[0]
    assert "Karten der Texte im Suchbereich (5 von 5)" in prompt and "Passende Textstellen aus den Volltexten" in prompt
    assert prompt.index("Karten der Texte") < prompt.index("Passende Textstellen") and "Überblicksfrage" in prompt
    assert f"[{len(cards) + 1}] " in prompt.split("Passende Textstellen", 1)[1]
    basis = events[-1]["basis"]
    assert basis == {"mode": "cards", "cards": 5, "passages": len(passages),
                     "passage_docs": len({s["item_key"] for s in passages}), "scope_docs": 5, "qtype": "overview"}

    # Frageart von Hand: Detail → wieder Textstellen, obwohl das Hilfsmodell „overview“ meldet
    main.rules.insert(0, ("Textstellen aus der Bibliothek", "Antwort [1]."))
    events = list(ChatService(library).ask("Welche Methoden?", None, AskOptions(scope="auto", qtype="detail")))
    assert events[-1]["basis"]["mode"] == "passages" and events[-1]["basis"]["qtype"] == "detail"


def test_overview_survives_a_failing_search(library, monkeypatch):
    """Scheitert die Suche nach Textstellen, stützt sich die Antwort auf die Karten (mit Hinweis)."""
    helper = ScriptedModel([("Suchanfragen", json.dumps({"type": "overview", "queries": ["Methoden"]}))])
    main = ScriptedModel([("Karten der Texte", "Überblick [1].")])
    install_models(monkeypatch, helper, main)
    library.settings_store.update({"retrieval": {"fulltext_limits": {"anthropic": 50}}})
    service = ChatService(library)

    def broken(*a, **k):
        raise RuntimeError("Index gesperrt")
    monkeypatch.setattr(service.searcher, "search", broken)
    events = list(service.ask("Welche Methoden verwenden die Studien?", None, AskOptions(scope="auto")))
    assert any(e["type"] == "warning" and "Karten" in e["text"] for e in events)
    assert events[-1]["basis"]["passages"] == 0 and events[-1]["basis"]["cards"] == 5
    assert "Passende Textstellen" not in main.calls[0]


def test_query_prompt_treats_comparisons_of_concepts_as_detail():
    from zotivity.rag import prompts

    overview = prompts.QUERY_PROMPT.split('- "overview":', 1)[1].split("\n", 1)[0]
    detail = prompts.QUERY_PROMPT.split('- "detail":', 1)[1].split("\n", 1)[0]
    assert "Positionen" not in overview and "Vergleich der Texte" not in overview and "Bestand" in overview
    assert all(w in detail for w in ("Begriffen", "Definitionen", "Positionen", "im Zweifel"))
