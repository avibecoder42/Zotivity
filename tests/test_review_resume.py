"""Geschriebener (bezahlter) Text des Forschungsstands geht nicht still verloren: Schreiben macht nach einem Fehler oder
Abbruch beim ersten fehlenden Abschnitt weiter, und das Speichern der Gliederung behält den Text bei seinem Abschnitt
(Code-Prüfung 2026-11-11, C15 und C18)."""

import pytest
from fastapi.testclient import TestClient

from zotivity.jobs import Job
from zotivity.rag.review import carry_sections, missing_sections
from test_truncation import H, Scripted, service

TEXT = {"Einleitung": "## Einleitung\n\nChatbots werden genutzt [@A, S. 2].",
        "Befunde": "## Befunde\n\nDie Befunde zeigen Entlastung [@A, S. 4].",
        "Schluss": "## Schluss\n\nEs fehlen Längsschnittstudien [@A]."}


def outline(*headings, words=100):
    return {"title": "T", "sections": [{"heading": h, "focus": "", "keys": [], "words": words} for h in headings]}


def make_review(zengine, sections=()):
    state = {"outline": outline("Einleitung", "Befunde", "Schluss"), "target_words": 300, "step": "outlined"}
    if sections:
        state["sections"] = [{"heading": h, "text": TEXT[h]} for h in sections]
    return zengine.store.create_review("T", "Wie nutzen Jugendliche Chatbots?", state)


def written(prompt: str) -> str:
    return next(h for h in TEXT if f"Abschnitt „{h}“" in prompt)


def test_write_continues_after_an_error(zengine):
    broken = {"Befunde"}

    def answer(prompt, params):
        heading = written(prompt)
        if heading in broken:
            raise RuntimeError("Anbieter nicht erreichbar")
        return TEXT[heading], "STOP"

    model = Scripted(answer)
    svc = service(zengine, model)
    rid = make_review(zengine)
    with pytest.raises(RuntimeError):
        svc.write(Job("review", "t"), rid)
    state = svc.get(rid)["state"]
    assert [s["heading"] for s in state["sections"]] == ["Einleitung"] and missing_sections(state) == [1, 2]

    # Zweiter Lauf: die Einleitung bleibt, geschrieben werden nur die fehlenden Abschnitte
    broken.clear()
    model.calls.clear()
    job = Job("review", "t")
    out = svc.write(job, rid)
    assert [written(c[0]) for c in model.calls] == ["Befunde", "Schluss"] and out["written"] == 2
    assert TEXT["Einleitung"] in model.calls[0][0]  # der vorhandene Abschnitt steht als „bisher geschrieben“ im Prompt
    assert (job.done, job.total) == (3, 3)
    state = svc.get(rid)["state"]
    assert [s["text"] for s in state["sections"]] == list(TEXT.values()) and state["step"] == "written"
    assert state["check"]["missing_sections"] == []

    # Alles geschrieben: ohne ausdrückliches Neuschreiben wird nichts ersetzt
    model.calls.clear()
    with pytest.raises(ValueError, match="schon geschrieben"):
        svc.write(Job("review", "t"), rid)
    assert not model.calls
    svc.write(Job("review", "t"), rid, restart=True)
    assert [written(c[0]) for c in model.calls] == ["Einleitung", "Befunde", "Schluss"]


def test_carry_sections_keeps_text_with_its_section():
    old = [{"heading": h, "text": t} for h, t in TEXT.items()]
    # Nur Wortzahl oder Fokus geändert (ohne Angabe der bisherigen Stelle): gleiche Überschrift, alles bleibt
    kept, lost = carry_sections(old, outline("Einleitung", "Befunde", "Schluss", words=250)["sections"])
    assert kept == old and lost == []
    # Verschoben, umbenannt, einer entfernt, ein neuer in der Mitte
    kept, lost = carry_sections(old, outline("Schluss", "Neu", "Einführung")["sections"], [2, None, 0])
    assert [s["heading"] for s in kept] == ["Schluss", "Neu", "Einführung"]
    assert kept[0]["text"] == TEXT["Schluss"] and kept[1]["text"] == ""
    assert kept[2]["text"] == "## Einführung\n\nChatbots werden genutzt [@A, S. 2]."  # Überschrift im Text nachgeführt
    assert lost == ["Befunde"]
    # Neuer Abschnitt am Ende braucht keinen Platzhalter; unbrauchbare Angaben zählen als „neu“
    kept, lost = carry_sections(old[:1], outline("Einleitung", "Neu")["sections"], [0, None])
    assert kept == old[:1] and lost == []
    kept, lost = carry_sections(old, outline("A", "B")["sections"], [7, "x"])
    assert kept == [] and lost == list(TEXT)


def test_saving_the_outline_keeps_written_text(zengine):
    model = Scripted(lambda p, params: ("## Methoden\n\nMeist Befragungen [@A].", "STOP"))
    svc = service(zengine, model)
    rid = make_review(zengine, ["Einleitung", "Befunde", "Schluss"])
    svc.check(rid)

    # Wortzahl geändert und gespeichert: der Text bleibt (früher war er danach weg)
    res = svc.set_outline(rid, outline("Einleitung", "Befunde", "Schluss", words=400), [0, 1, 2])
    state = svc.get(rid)["state"]
    assert res == {"kept": 3, "lost": []} and [s["text"] for s in state["sections"]] == list(TEXT.values())
    assert state["outline"]["sections"][0]["words"] == 400 and state["check"]["planned_words"] == 1200

    # Neuer Abschnitt in der Mitte, die übrigen verschoben: nur der neue fehlt, und nur er wird geschrieben
    svc.set_outline(rid, outline("Einleitung", "Methoden", "Schluss", "Befunde"), [0, None, 2, 1])
    state = svc.get(rid)["state"]
    assert [s["text"] for s in state["sections"]] == [TEXT["Einleitung"], "", TEXT["Schluss"], TEXT["Befunde"]]
    assert missing_sections(state) == [1]
    assert state["check"]["missing_sections"] == [{"index": 1, "heading": "Methoden"}]
    assert state["check"]["incomplete_sections"] == []  # die Lücke gilt nicht als „endet mitten im Satz“
    svc.write(Job("review", "t"), rid)
    assert len(model.calls) == 1 and "Abschnitt „Methoden“" in model.calls[0][0]
    state = svc.get(rid)["state"]
    assert [s["heading"] for s in state["sections"]] == ["Einleitung", "Methoden", "Schluss", "Befunde"]
    assert state["sections"][1]["text"].startswith("## Methoden") and state["sections"][3]["text"] == TEXT["Befunde"]
    assert missing_sections(state) == []


def test_outline_and_write_api(zengine, monkeypatch):
    rid = make_review(zengine, ["Einleitung", "Befunde", "Schluss"])
    monkeypatch.setattr("zotivity.server.app.ollama_available", lambda url: False)
    from zotivity.server.app import create_app

    with TestClient(create_app(zengine)) as c:
        # Alles geschrieben: „Schreiben“ ohne restart ersetzt nichts
        r = c.post(f"/api/reviews/{rid}/write", headers=H, json={})
        assert r.status_code == 400 and "schon geschrieben" in r.text
        # Abschnitt entfernt: sein Text fällt weg, der Rest bleibt an seinem Abschnitt
        r = c.put(f"/api/reviews/{rid}/outline", headers=H,
                  json={"outline": outline("Schluss", "Einleitung"), "origin": [2, 0]})
        assert r.status_code == 200
        assert [s["text"] for s in r.json()["state"]["sections"]] == [TEXT["Schluss"], TEXT["Einleitung"]]
        assert c.put(f"/api/reviews/{rid}/outline", headers=H, json={"outline": {"sections": "x"}}).status_code == 400
        assert c.put("/api/reviews/9999/outline", headers=H, json={"outline": outline("A")}).status_code == 404
