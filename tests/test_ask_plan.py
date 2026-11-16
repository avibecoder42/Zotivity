"""Suchplan für „Fragen“: ganze Texte oder Textstellen, Belege mit Seite, Grundlage der Antwort."""

import pytest
from fakes import ScriptedModel
from test_indexing_search import build_library, run_index
from test_rag import install_models

from zotivity.rag import AskOptions, ChatService
from zotivity.rag import wholetext
from zotivity.rag.plan import fulltext_limit, make_plan, scope_docs


@pytest.fixture
def indexed(zengine, fake_zotero):
    build_library(fake_zotero)
    run_index(zengine)
    return zengine


def test_scope_docs_count_texts_with_length(indexed):
    docs = scope_docs(indexed, indexed.make_filter({}))
    assert {d.item_key for d in docs} == {"ITEMA001", "ITEMB001"}
    assert all(d.tokens > 50 for d in docs)
    only_a = scope_docs(indexed, indexed.make_filter({"item_keys": ["users/0:ITEMA001"]}))
    assert [d.item_key for d in only_a] == ["ITEMA001"]
    lengths = indexed.store.text_lengths({("users/0", "ITEMA001")})
    assert list(lengths) == [("users/0", "ITEMA001")] and lengths[("users/0", "ITEMA001")][1] >= 1


def test_plan_whole_when_texts_fit_the_limit(indexed):
    f = indexed.make_filter({})
    plan = make_plan(indexed, f, "auto", {}, "anthropic")
    assert plan.mode == "whole" and plan.whole_ok and plan.n_docs == 2 and plan.limit == 100000
    assert make_plan(indexed, f, "passages", {}, "anthropic").mode == "passages"
    assert make_plan(indexed, f, "auto", {}, "anthropic", search_only=True).reason == "search_only"
    # Grenze je Anbieter (Experten): zu klein → Textstellen, mit Grund
    indexed.settings_store.update({"retrieval": {"fulltext_limits": {"ollama": 50}}})
    plan = make_plan(indexed, f, "whole", {}, "ollama")
    assert plan.mode == "passages" and plan.reason == "too_large" and plan.limit == 50
    assert fulltext_limit(indexed.settings, "anthropic") == 100000  # die anderen Vorgaben bleiben
    indexed.settings_store.update({"retrieval": {"fulltext_limits": {"ollama": 0}}})
    assert make_plan(indexed, f, "auto", {}, "ollama").reason == "off"
    # Sitzungswerte der Suchoptionen gehen den Experten-Werten vor
    plan = make_plan(indexed, f, "passages", {"max_chunks_per_doc": 20, "unbekannt": 1}, "anthropic")
    assert plan.cfg.max_chunks_per_doc == 20 and plan.session == {"max_chunks_per_doc": 20}
    d = plan.as_dict()
    assert d["values"]["max_chunks_per_doc"] == 20 and d["session"] == {"max_chunks_per_doc": 20}


def test_whole_text_answer_cites_pages(indexed, monkeypatch):
    def helper_unused(*a, **k):
        raise AssertionError("Bei ganzen Texten keine Suchanfragen")

    main = ScriptedModel([("Texte:", "Jugendliche nutzen Chatbots [1, S. 1]. Sozialarbeit [2, S. 2; 1, o. S.]. "
                                     "Beides [1, 2]. Kein Beleg [1, siehe oben] und [7].")])
    install_models(monkeypatch, None, main)
    monkeypatch.setattr("zotivity.rag.chat.helper_model", helper_unused)
    events = list(ChatService(indexed).ask("Was steht in den Texten?", None, AskOptions(scope="auto")))
    types = [e["type"] for e in events]
    assert "plan" in types and "rewrite" in types and types[-1] == "done"
    assert next(e for e in events if e["type"] == "plan")["plan"]["mode"] == "whole"
    # Die ganzen Texte mit Seitenmarken stehen im System-Prompt
    prompt = main.calls[0]
    assert "=== [1]" in prompt and "=== [2]" in prompt and "[S. 1]" in prompt and "[S. 2]" in prompt
    text = next(e for e in events if e["type"] == "rewrite")["text"]
    sources = [e for e in events if e["type"] == "sources"][-1]["sources"]
    assert text.startswith("Jugendliche nutzen Chatbots [1]. Sozialarbeit [2, 3]. Beides [4, 5].")
    assert "[1, siehe oben]" in text and "[7]" in text  # keine Belege auf die Texte: unverändert
    by_n = {s["n"]: s for s in sources}
    first = by_n[1]
    assert first["page_label"] == "1" and first["page_index"] == 0 and first["whole"] is True
    assert "page=1" in first["zotero_pdf"] and first["text"]
    assert by_n[3]["page_label"] == "" and by_n[3]["page_index"] is None  # o. S.
    done = events[-1]
    assert done["basis"]["mode"] == "whole" and len(done["basis"]["texts"]) == 2 and done["basis"]["tokens"] > 0
    chat_id = events[0]["chat_id"]
    msg = indexed.store.chat_messages(chat_id)[-1]
    assert msg["content"] == text and msg["meta"]["raw"].startswith("Jugendliche nutzen Chatbots [1, S. 1]")

    # Folgefrage: gleicher System-Prompt (Prompt-Caching), Verlauf in der Fassung des Modells
    list(ChatService(indexed).ask("Und weiter?", chat_id, AskOptions(scope="auto")))
    first_system, follow = main.calls[0].split("\nWas steht")[0], main.calls[-1]
    assert follow.startswith(first_system) and "[1, S. 1]" in follow.split("Texte:")[-1]


def test_whole_requested_but_too_large_uses_passages(indexed, monkeypatch):
    main = ScriptedModel([("Textstellen aus der Bibliothek", "Antwort [1].")])
    install_models(monkeypatch, ScriptedModel([]), main)
    indexed.settings_store.update({"retrieval": {"fulltext_limits": {"anthropic": 10}, "multi_query": False}})
    events = list(ChatService(indexed).ask("Chatbots Jugendliche", None, AskOptions(scope="whole")))
    assert any(e["type"] == "warning" and "Ganze Texte" in e["text"] for e in events)
    done = events[-1]
    assert done["type"] == "done" and done["basis"]["mode"] == "passages"
    assert done["basis"]["scope_docs"] == 2 and done["basis"]["passages"] >= 1


def test_resolve_citations_page_ranges(indexed):
    texts = wholetext.load_texts(indexed, [("users/0", "ITEMA001")])
    assert len(texts) == 1 and texts[0].labels
    out, sources = wholetext.resolve_citations("A [1, S. 1–2] B [1, p. 2] C [1, S. 1–2] D [1]", texts, indexed.store)
    assert out == "A [1] B [2] C [1] D [3]"
    assert sources[0].page_label == "1–2" and sources[0].page_index == 0
    assert sources[1].page_index == 1 and sources[2].page_label == "" and sources[2].page_index is None
