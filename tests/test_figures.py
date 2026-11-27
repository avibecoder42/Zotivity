"""Abbildungen in PDFs: beim Einlesen finden, beschreiben lassen (mit Bild oder ohne), als Abschnitte durchsuchbar,
Beschreibungen bleiben beim erneuten Einlesen derselben Datei."""

from __future__ import annotations

import pytest
from fakes import ScriptedModel
from fastapi.testclient import TestClient
from test_indexing_search import run_index
from test_rag import install_models

from zotivity import figures
from zotivity.extract import pdfmake
from zotivity.jobs import Job
from zotivity.llm import LLMError
from zotivity.retrieval import Searcher
from zotivity.server.app import create_app

PARA = ("Die Fachpersonen der Kinder- und Jugendhilfe dokumentieren ihre Arbeit in einem elektronischen System. "
        "Die Befragung zeigt Unterschiede zwischen den Arbeitsfeldern. ")
DESCRIPTION = "Balkendiagramm: Anteil der Fachpersonen mit täglicher Dokumentation je Arbeitsfeld, Heimerziehung am höchsten."


class ImageModel(ScriptedModel):
    """Merkt sich, ob ein Bild mitkam; lehnt Bilder auf Wunsch ab (wie ein Modell ohne Bildverständnis)."""

    def __init__(self, reply=DESCRIPTION, refuse_images=False):
        super().__init__([("Beschreibe die Abbildung", reply)])
        self.images: list[int] = []
        self.refuse_images = refuse_images

    def _stream(self, system, messages, params):
        n = sum(len(m.images) for m in messages)
        if n and self.refuse_images:
            raise LLMError("Bad request: image input not supported")
        self.images.append(n)
        yield from super()._stream(system, messages, params)


def figure_pdf(path):
    pages = []
    for k in range(3):
        page = pdfmake.text_page(PARA * 4, box=(60, 60, 540, 300))
        if k == 1:
            page.charts.append((100, 330, 460, 560))
            page.text(100, 580, "Abbildung 2: Tägliche Dokumentation nach Arbeitsfeld", 9)
        pages.append(page)
    path.write_bytes(pdfmake.build(pages))
    return path


@pytest.fixture
def library(zengine, fake_zotero, tmp_path):
    zengine.settings_store.update({"figures": {"enabled": True}})
    fake_zotero.add_item("ITEMF001", "Dokumentation in der Jugendhilfe", "Muster", "2022", collections=[])
    fake_zotero.add_file("ATTF0001", "ITEMF001", figure_pdf(tmp_path / "studie.pdf"), "application/pdf")
    run_index(zengine)
    return zengine


def chunk_kinds(engine):
    return [(r["kind"], r["page_start"]) for r in engine.store.document_chunks("users/0", "ITEMF001")]


def test_figures_found_while_reading(library):
    figs = library.store.figures("users/0", "ATTF0001")
    assert [(f["page"], f["kind"], f["caption"][:11]) for f in figs] == [(1, "drawing", "Abbildung 2")]
    assert not figs[0]["description"] and ("figure", 1) not in chunk_kinds(library)
    est = figures.estimate(library)
    assert est["figures"] == 1 and est["open"] == 1 and est["described"] == 0 and est["input_tokens"] > 0


def test_describe_with_image_and_search(library, monkeypatch):
    model = ImageModel()
    install_models(monkeypatch, model, model)
    res = figures.describe(library, Job("figures", "t"))
    assert res["described"] == 1 and res["with_image"] == 1 and res["chunks"] == 1 and model.images == [1]
    prompt = model.calls[0]
    assert "Abbildung 2: Tägliche Dokumentation" in prompt and "Das Bild der Abbildung ist beigefügt" in prompt
    fig_chunk = next(r for r in library.store.document_chunks("users/0", "ITEMF001") if r["kind"] == "figure")
    assert fig_chunk["page_start"] == 1 and fig_chunk["text"].startswith(figures.LABEL + ": Abbildung 2")
    assert DESCRIPTION in fig_chunk["text"]
    assert library.store.count_document_chunks_to_embed(library.space(), "users/0", "ATTF0001", "ITEMF001") == 0
    hits = Searcher(library).search("Balkendiagramm tägliche Dokumentation Heimerziehung", k=5)
    assert any(h.chunk["kind"] == "figure" for h in hits)
    assert figures.estimate(library)["open"] == 0

    # Erneutes Einlesen derselben Datei: Beschreibung bleibt, kein neuer Auftrag, Textabschnitte unverändert
    text_ids = [r["id"] for r in library.store.document_chunks("users/0", "ITEMF001") if r["kind"] == "text"]
    library.store.mark_pending("users/0", ["ATTF0001"])
    run_index(library)
    assert [r["id"] for r in library.store.document_chunks("users/0", "ITEMF001") if r["kind"] == "text"] == text_ids
    assert ("figure", 1) in chunk_kinds(library) and len(model.calls) == 1


def test_model_without_images_falls_back_to_text(library, monkeypatch):
    model = ImageModel(refuse_images=True)
    install_models(monkeypatch, model, model)
    res = figures.describe(library, Job("figures", "t"))
    assert res["described"] == 1 and res["with_image"] == 0 and model.images == [0]
    assert "Es liegt kein Bild vor" in model.calls[0]
    assert library.store.get_meta(figures._no_images_key(model)) == "1"


def test_decorative_images_do_not_become_chunks(library, monkeypatch):
    model = ImageModel(reply="Keine inhaltliche Abbildung.")
    install_models(monkeypatch, model, model)
    figures.describe(library, Job("figures", "t"))
    assert ("figure", 1) not in chunk_kinds(library) and figures.estimate(library)["open"] == 0


def test_scan_finds_figures_in_already_read_pdfs(zengine, fake_zotero, tmp_path):
    fake_zotero.add_item("ITEMF001", "Dokumentation in der Jugendhilfe", "Muster", "2022", collections=[])
    fake_zotero.add_file("ATTF0001", "ITEMF001", figure_pdf(tmp_path / "studie.pdf"), "application/pdf")
    run_index(zengine)  # Abbildungen aus
    assert zengine.store.figures("users/0", "ATTF0001") == []
    text_ids = [r["id"] for r in zengine.store.document_chunks("users/0", "ITEMF001") if r["kind"] == "text"]
    zengine.settings_store.update({"figures": {"enabled": True}})
    res = figures.scan(zengine, Job("index", "t"))
    assert res == {"pdfs": 1, "figures": 1}
    assert [r["id"] for r in zengine.store.document_chunks("users/0", "ITEMF001") if r["kind"] == "text"] == text_ids


def test_figures_api(library):
    client = TestClient(create_app(library))
    r = client.get("/api/figures").json()
    assert r["figures"] == 1 and r["open"] == 1 and r["with_image"] is True
    assert client.put("/api/settings", json={"figures": {"model": "main", "send_image": False}},
                      headers={"X-Zotivity": "1"}).status_code == 200
    assert client.get("/api/figures").json()["with_image"] is False


def test_source_header_marks_machine_descriptions():
    from zotivity.rag.prompts import ANSWER_SYSTEM
    from zotivity.rag.sources import Source

    s = Source(n=3, chunk_id=1, lib="users/0", item_key="K", att_key="A", title="T", citation="Muster 2022",
               page_label="12", page_index=11, text="x", kind="figure", score=0.0, relevance=None)
    assert "(Abbildung, maschinell beschrieben)" in s.header()
    assert "maschinell beschrieben" in ANSWER_SYSTEM
