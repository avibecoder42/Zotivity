"""Lesen in Etappen: lange Texte kapitelweise, Notizen je Etappe, Antwort aus den Notizen (Stufe 3 des Suchplans)."""

import pytest
from fakes import ScriptedModel
from test_indexing_search import build_library, run_index
from test_rag import install_models

from zotivity.llm import LLMError
from zotivity.rag import AskOptions, ChatService
from zotivity.rag import stages
from zotivity.rag.plan import make_plan
from zotivity.rag.wholetext import WholeText


@pytest.fixture
def indexed(zengine, fake_zotero):
    build_library(fake_zotero)
    run_index(zengine)
    return zengine


def _text(body: str) -> WholeText:
    return WholeText(n=1, lib="users/0", item_key="K", item={}, text=body, tokens=0, att_key="", content_type="",
                     labels=None, rows=[])


def test_segments_and_stage_boundaries_at_chapters():
    page = "Wort " * 400  # rund 500 Tokens je Seite
    body = ("[Abstract]\nKurz.\n\n[S. 1]\n1 Einleitung\n\n" + page + "\n\n[S. 2]\n" + page + "\n\n[S. 3]\n" + page
            + "\n\n[S. 4]\n2 Methode und Stichprobe\n\n" + page + "\n\n[S. 5]\n" + page + "\n\n[S. 6]\n" + page)
    segs = stages.segments(body)
    assert [s.marker for s in segs] == ["", "[S. 1]", "[S. 2]", "[S. 3]", "[S. 4]", "[S. 5]", "[S. 6]"]
    assert segs[1].heading == "1 Einleitung" and segs[4].heading == "2 Methode und Stichprobe" and not segs[2].heading
    parts = stages.split_stages([_text(body)], 2000)
    # Neue Etappe beim Kapitel „2 Methode“ (die erste ist über die Hälfte voll), nicht erst bei 2000 Tokens
    assert [(p.first, p.last) for p in parts] == [("S. 1", "S. 3"), ("S. 4", "S. 6")]
    assert parts[0].pages == "S. 1–3" and parts[1].headings == ["2 Methode und Stichprobe"]
    assert [(p.index, p.total) for p in parts] == [(1, 2), (2, 2)] and parts[0].text.startswith("[Abstract]")
    # Kleine Etappen: Grenze nach Länge
    assert len(stages.split_stages([_text(body)], 1100)) == 3
    assert stages.stage_size(6000) == 4800 and stages.stage_size(100000) == 40000 and stages.stage_size(10) == 2000
    assert stages.has_notes("- Typen [1, S. 4]") and not stages.has_notes("Nichts zur Frage.")


def test_plan_stages(indexed):
    f = indexed.make_filter({})
    assert make_plan(indexed, f, "stages", {}, "anthropic").mode == "whole"  # passt ganz: ganz lesen
    indexed.settings_store.update({"retrieval": {"fulltext_limits": {"anthropic": 50}}})
    plan = make_plan(indexed, f, "stages", {}, "anthropic")
    assert plan.mode == "stages" and plan.stage_tokens == 2000 and plan.stages == 2 and plan.as_dict()["stages_ok"]
    assert make_plan(indexed, f, "auto", {}, "anthropic").mode == "passages"  # nie von selbst: kostet viele Aufrufe
    assert make_plan(indexed, f, "stages", {}, "anthropic", search_only=True).stages_reason == "search_only"


def test_answer_from_stage_notes(indexed, monkeypatch):
    def notes(prompt):
        return "- Chatbots helfen bei Problemen [1, S. 1]." if "Text [1]" in prompt else "Nichts zur Frage."

    helper = ScriptedModel([("Etappe", notes)])
    main = ScriptedModel([("Notizen:", "Jugendliche nutzen Chatbots bei Problemen [1, S. 1].")])
    install_models(monkeypatch, helper, main)
    indexed.settings_store.update({"retrieval": {"fulltext_limits": {"anthropic": 50}}})
    events = list(ChatService(indexed).ask("Wozu nutzen Jugendliche Chatbots?", None, AskOptions(scope="stages")))
    assert len(helper.calls) == 2 and all("Frage: Wozu nutzen" in c for c in helper.calls)
    assert any(e["type"] == "status" and e["text"] == "Etappen gelesen: 2 von 2 …" for e in events)
    assert "Chatbots helfen bei Problemen [1, S. 1]" in main.calls[0] and "Etappe 1 von 2" in main.calls[0]
    assert next(e for e in events if e["type"] == "rewrite")["text"] == "Jugendliche nutzen Chatbots bei Problemen [1]."
    done = events[-1]
    assert done["basis"]["mode"] == "stages" and done["basis"]["stages"] == 2 and done["basis"]["with_notes"] == 1
    assert len(done["stages"]) == 2 and done["stages"][0]["pages"]
    msg = indexed.store.chat_messages(events[0]["chat_id"])[-1]
    assert msg["meta"]["mode"] == "stages" and msg["sources"][0]["page_label"] == "1"


def test_all_stages_fail(indexed, monkeypatch):
    class Failing(ScriptedModel):
        def _stream(self, system, messages, params):
            raise LLMError("Zeitüberschreitung")

    install_models(monkeypatch, Failing([]), ScriptedModel([]))
    indexed.settings_store.update({"retrieval": {"fulltext_limits": {"anthropic": 50}}})
    events = list(ChatService(indexed).ask("Frage", None, AskOptions(scope="stages")))
    assert sum(1 for e in events if e["type"] == "warning") == 2
    assert events[-1]["type"] == "error" and "Zeitüberschreitung" in events[-1]["message"]
