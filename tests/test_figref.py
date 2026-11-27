"""Fragen zu einem genannten Text und zu einer bestimmten Abbildung („Beschreibe Abbildung 1 in …“), Beschreiben auf
Knopfdruck und automatisch nach dem Indexieren, Abbildungen je Korpus."""

from __future__ import annotations

import json
import time

import pytest
from fakes import ScriptedModel
from fastapi.testclient import TestClient
from test_indexing_search import run_index
from test_rag import install_models

from zotivity import figures
from zotivity.extract import pdfmake
from zotivity.jobs import Job
from zotivity.rag import figref, wholetext
from zotivity.rag.chat import AskOptions, ChatService
from zotivity.server.app import create_app

TITLE_A = "LLM-Assisted Thematic Analysis: Opportunities and Limits of Coding"
TITLE_B = "Dokumentation in der Kinder- und Jugendhilfe"
PARA_A = ("Large language models support thematic analysis in qualitative research. The workflow has five phases, "
          "from familiarisation to reporting, as illustrated in Figure 1. ")
PARA_B = ("Die Fachpersonen der Kinder- und Jugendhilfe dokumentieren ihre Arbeit in einem elektronischen System. "
          "Die Befragung zeigt Unterschiede zwischen den Arbeitsfeldern und Phasen der Arbeit. ")
HELPER = '{"queries": []}'  # Hilfsmodell: keine zusätzlichen Suchanfragen
DESCRIPTION = "Flussdiagramm mit fünf Phasen der LLM-gestützten thematischen Analyse, von der Vertrautmachung bis zum Bericht."


def make_pdf(path, para: str, caption: str):
    pages = []
    for k in range(3):
        page = pdfmake.text_page(para * 4, box=(60, 60, 540, 300))
        if k == 1:
            page.charts.append((100, 330, 460, 560))
            page.text(100, 580, caption, 9)
        pages.append(page)
    path.write_bytes(pdfmake.build(pages))
    return path


@pytest.fixture
def lib2(zengine, fake_zotero, tmp_path):
    zengine.settings_store.update({"figures": {"enabled": True}})
    fake_zotero.add_item("ITEMA001", TITLE_A, "Ornelas", "2025", collections=[])
    fake_zotero.add_file("ATTA0001", "ITEMA001", make_pdf(tmp_path / "a.pdf", PARA_A, "Fig. 1. Overview of LLM-assisted TA"),
                         "application/pdf")
    fake_zotero.add_item("ITEMB001", TITLE_B, "Muster", "2022", collections=[])
    fake_zotero.add_file("ATTB0001", "ITEMB001", make_pdf(tmp_path / "b.pdf", PARA_B, "Abbildung 1: Dokumentation je Feld"),
                         "application/pdf")
    run_index(zengine)
    return zengine


def ask(engine, question, chat_id=None, **opts):
    events = list(ChatService(engine).ask(question, chat_id, AskOptions(**opts)))
    sources = next(e["sources"] for e in events if e["type"] == "sources")
    done = next(e for e in events if e["type"] == "done")
    return events, sources, done


def wait(engine, job_id):
    for _ in range(400):
        job = engine.jobs.get(job_id)
        if job.status not in ("queued", "running"):
            return job
        time.sleep(0.02)
    raise AssertionError("Auftrag läuft zu lange")


# ------------------------------------------------------------------ Erkennen
def test_asked_numbers_and_caption_numbers():
    assert figref.asked_numbers("Beschreibe Abbildung 1 in Ornelas") == ["1"]
    assert figref.asked_numbers("Was zeigen Abbildungen 2 und 3?") == ["2", "3"]
    assert figref.asked_numbers("Fig. 4a und Abb. 5, Figure 2.1") == ["4a", "5", "2.1"]
    assert figref.asked_numbers("Wie wird die Abbildung der Realität diskutiert? Abbau 3") == []
    assert figref.caption_number("Fig. 1. Overview of LLM-Assisted TA") == "1"
    assert figref.caption_number("Abbildung 12: Anteil je Feld") == "12"
    assert figref.caption_number("Figure 3b – Results") == "3b"
    assert figref.caption_number("Overview of the workflow") == ""


def test_named_docs_by_title_main_title_and_author(lib2):
    keys = [("users/0", "ITEMA001"), ("users/0", "ITEMB001")]
    s = lib2.store
    assert figref.named_docs(s, f"Beschreibe Abbildung 1 in Ornelas: {TITLE_A}", keys) == [("users/0", "ITEMA001")]
    assert figref.named_docs(s, "Was steht in «LLM-Assisted Thematic Analysis» zu Phasen?", keys) == []  # Thema
    assert figref.named_docs(s, "Welche Aspekte nennt Dokumentation in der Kinder- und Jugendhilfe?", keys) == [
        ("users/0", "ITEMB001")]  # ganzer Titel
    assert figref.named_docs(s, "Was zeigt Abbildung 1 bei Ornelas?", keys) == []
    assert figref.named_docs(s, "Was zeigt Abbildung 1 bei Ornelas?", keys, authors=True) == [("users/0", "ITEMA001")]
    assert figref.named_docs(s, "Wie nutzen Jugendliche Chatbots?", keys, authors=True) == []


# ------------------------------------------------------------------ Fragen
def test_question_about_undescribed_figure(lib2, monkeypatch):
    main = ScriptedModel([("Textstellen aus der Bibliothek", "Abbildung 1 heisst laut Bildunterschrift Overview [1].")])
    install_models(monkeypatch, ScriptedModel([], default=HELPER), main)
    events, sources, done = ask(lib2, f"Beschreibe Abbildung 1 in Ornelas: {TITLE_A}")
    assert sources[0]["kind"] == "figure_open" and sources[0]["item_key"] == "ITEMA001"
    assert "Bildunterschrift: Fig. 1. Overview of LLM-assisted TA" in sources[0]["text"]
    assert sources[0]["page_index"] == 1
    assert sources[1]["kind"] == "text" and "Fig. 1. Overview" in sources[1]["text"]  # Textstelle mit der Bildunterschrift
    assert all(s["item_key"] == "ITEMA001" for s in sources[:3])
    prompt = main.calls[-1]
    assert "(Abbildung, noch nicht beschrieben – nur Bildunterschrift und Text in der Grafik)" in prompt
    assert "noch nicht beschrieben" in prompt.split("Textstellen aus der Bibliothek")[0]  # Regel im System-Prompt
    fkey = lib2.store.figures("users/0", "ATTA0001")[0]["fkey"]
    assert [f["fkey"] for f in done["figures_open"]] == [fkey]
    assert done["figures_open"][0]["citation"].startswith("Ornelas") and done["basis"]["named"] == ["Ornelas 2025"]
    msg = lib2.store.chat_messages(done["chat_id"])[-1]
    assert msg["meta"]["figures_open"][0]["fkey"] == fkey  # bleibt beim erneuten Öffnen des Chats

    # Knopf „Beschreiben und neu antworten“: nur diese Abbildung beschreiben
    model = ScriptedModel([("Beschreibe die Abbildung", DESCRIPTION)], default=HELPER)
    install_models(monkeypatch, model, main)
    res = figures.describe(lib2, Job("figures", "t"), fkeys={fkey})
    assert res["described"] == 1 and len(model.calls) == 1
    assert figures.estimate(lib2)["open"] == 1  # die Abbildung im anderen Text bleibt offen
    events, sources, done = ask(lib2, f"Beschreibe Abbildung 1 in Ornelas: {TITLE_A}", done["chat_id"])
    assert sources[0]["kind"] == "figure" and DESCRIPTION in sources[0]["text"]
    assert sources[0]["chunk_id"] > 0 and "figures_open" not in done


def test_follow_up_question_uses_text_from_previous_question(lib2, monkeypatch):
    install_models(monkeypatch, ScriptedModel([], default=HELPER), ScriptedModel([], default="Antwort [1]."))
    _, _, done = ask(lib2, f"Worum geht es in {TITLE_A}?")
    _, sources, done2 = ask(lib2, "Und was zeigt Abbildung 1?", done["chat_id"])
    assert sources[0]["kind"] == "figure_open" and sources[0]["item_key"] == "ITEMA001"


def test_figure_without_named_text_only_when_unambiguous(lib2, monkeypatch):
    install_models(monkeypatch, ScriptedModel([], default=HELPER), ScriptedModel([], default="Antwort [1]."))
    _, sources, _ = ask(lib2, "Was zeigt Abbildung 1?")  # zwei Texte mit Abbildung 1: beide
    assert {s["item_key"] for s in sources if s["kind"] == "figure_open"} == {"ITEMA001", "ITEMB001"}
    focus = figref.focus(lib2.store, "Was zeigt Abbildung 1?", [], [("users/0", "ITEMA001"), ("users/0", "ITEMB001")])
    assert len(focus.figures) == 2
    monkeypatch.setattr(figref, "MAX_AMBIGUOUS_DOCS", 1)
    focus = figref.focus(lib2.store, "Was zeigt Abbildung 1?", [], [("users/0", "ITEMA001"), ("users/0", "ITEMB001")])
    assert focus.figures == []
    _, sources, _ = ask(lib2, "Was zeigt Abbildung 7?")
    assert not any(s["kind"].startswith("figure") for s in sources)


def test_named_text_comes_first(lib2, monkeypatch):
    install_models(monkeypatch, ScriptedModel([], default=HELPER), ScriptedModel([], default="Antwort [1]."))
    _, sources, done = ask(lib2, f"Welche Phasen beschreibt {TITLE_A} und wie dokumentieren Fachpersonen?")
    keys = [s["item_key"] for s in sources]
    assert "ITEMB001" in keys  # andere Texte bleiben dabei
    assert keys.index("ITEMB001") > max(i for i, k in enumerate(keys) if k == "ITEMA001")  # zuerst der genannte
    assert done["basis"]["named"] == ["Ornelas 2025"]
    assert [s["n"] for s in sources] == list(range(1, len(sources) + 1))
    ranked = [s["relevance"]["rank"] for s in sources if s["relevance"]]
    assert ranked == list(range(1, len(ranked) + 1))


def test_whole_texts_contain_figure_descriptions(lib2, monkeypatch):
    install_models(monkeypatch, ScriptedModel([("Beschreibe die Abbildung", DESCRIPTION)]), ScriptedModel([]))
    figures.describe(lib2, Job("figures", "t"))
    texts = wholetext.load_texts(lib2, [("users/0", "ITEMA001")])
    assert "Abbildungen (maschinell beschrieben, nicht Text der Quelle):" in texts[0].text
    assert f"Fig. 1. Overview of LLM-assisted TA: {DESCRIPTION}" in texts[0].text
    assert "[o. S.] Fig. 1." in texts[0].text  # Seitenmarke (die Test-PDF hat keine gedruckten Seitenzahlen)
    from zotivity.rag.prompts import WHOLE_SYSTEM

    assert "Abbildungen (maschinell beschrieben)" in WHOLE_SYSTEM


# ------------------------------------------------------------------ Automatisch, je Korpus, Schnittstelle
def test_auto_describe_after_indexing(lib2, monkeypatch):
    model = ScriptedModel([("Beschreibe die Abbildung", DESCRIPTION)])
    install_models(monkeypatch, model, model)
    assert figures.start_auto(lib2) is False  # aus als Standard
    lib2.settings_store.update({"figures": {"auto_describe": True, "auto_limit": 1}})
    notes = []
    assert figures.start_auto(lib2, notes.append) is True
    assert notes == ["Beschreibe 1 von 2 offenen Abbildungen (eigener Auftrag)"]
    job = wait(lib2, lib2.jobs.running("figures").id if lib2.jobs.running("figures") else max(lib2.jobs._jobs))
    assert job.status == "done" and job.title == figures.AUTO_TITLE and job.result["described"] == 1
    assert figures.estimate(lib2)["open"] == 1

    # Ein Indexierlauf startet den Auftrag selbst
    lib2.store.mark_pending("users/0", ["ATTB0001"])
    run_index(lib2)
    job = wait(lib2, max(lib2.jobs._jobs))
    assert job.title == figures.AUTO_TITLE and figures.estimate(lib2)["open"] == 0


def test_auto_describe_pauses_after_failure(lib2, monkeypatch):
    from zotivity.llm import LLMError

    class Broken(ScriptedModel):
        def _stream(self, system, messages, params):
            raise LLMError("Server nicht erreichbar")
            yield  # noqa

    install_models(monkeypatch, Broken([]), Broken([]))
    lib2.settings_store.update({"figures": {"auto_describe": True}})
    assert figures.start_auto(lib2) is True
    job = wait(lib2, max(lib2.jobs._jobs))
    assert job.result["described"] == 0 and job.result["errors"] == 2
    assert figures.start_auto(lib2) is False  # Pause: nicht bei jedem Lauf dieselben Abbildungen erneut schicken
    lib2.store.set_meta("figures_auto_pause_until", "0")
    assert figures.start_auto(lib2) is True
    wait(lib2, max(lib2.jobs._jobs))


def test_auto_describe_needs_a_key(lib2, monkeypatch):
    from zotivity.keystore import get_store

    lib2.settings_store.update({"figures": {"auto_describe": True}})
    get_store().delete("anthropic")
    notes = []
    assert figures.start_auto(lib2, notes.append) is False
    assert notes and notes[0].startswith("Abbildungen nicht automatisch beschrieben:")


def test_api_per_corpus_and_selected_figures(zengine, fake_zotero, tmp_path, monkeypatch):
    zengine.settings_store.update({"figures": {"enabled": True}})
    fake_zotero.add_collection("COLA0001", "LLM")
    fake_zotero.add_collection("COLB0001", "Jugendhilfe")
    fake_zotero.add_item("ITEMA001", TITLE_A, "Ornelas", "2025", collections=["COLA0001"])
    fake_zotero.add_file("ATTA0001", "ITEMA001", make_pdf(tmp_path / "a.pdf", PARA_A, "Fig. 1. Overview"),
                         "application/pdf")
    fake_zotero.add_item("ITEMB001", TITLE_B, "Muster", "2022", collections=["COLB0001"])
    fake_zotero.add_file("ATTB0001", "ITEMB001", make_pdf(tmp_path / "b.pdf", PARA_B, "Abbildung 1: Je Feld"),
                         "application/pdf")
    run_index(zengine)
    cid = zengine.store.create_corpus("LLM", ["users/0:COLA0001"])
    model = ScriptedModel([("Beschreibe die Abbildung", DESCRIPTION)])
    install_models(monkeypatch, model, model)
    client = TestClient(create_app(zengine))
    h = {"X-Zotivity": "1"}
    assert client.get("/api/figures").json()["open"] == 2
    r = client.get(f"/api/figures?corpus_id={cid}").json()
    assert r["open"] == 1 and r["texts"] == 1

    job = wait(zengine, client.post("/api/figures/describe", json={"corpus_id": cid}, headers=h).json()["id"])
    assert job.result["described"] == 1 and "Overview" in model.calls[0]
    assert client.get(f"/api/figures?corpus_id={cid}").json()["open"] == 0
    fkey = zengine.store.figures("users/0", "ATTB0001")[0]["fkey"]
    job = wait(zengine, client.post("/api/figures/describe", json={"fkeys": [fkey]}, headers=h).json()["id"])
    assert job.result["described"] == 1 and len(model.calls) == 2
    r = client.get("/api/figures").json()
    assert r["open"] == 0 and r["described"] == 2 and r["auto_paused_until"] == 0
    assert json.dumps(r)
