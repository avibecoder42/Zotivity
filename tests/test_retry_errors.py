"""Texte mit Fehler: ein normaler Lauf liest sie nicht neu, sagt das aber („Index ist aktuell – Texte mit Fehler
oder ohne Text: n“); „Erneut versuchen“ je Korpus; nach einem Update werden sie einmal neu gelesen."""

from __future__ import annotations

from fastapi.testclient import TestClient
from test_indexing_search import run_index

from zotivity import __version__
from zotivity.extract import pdfmake
from zotivity.server.app import create_app

PARA = "Die Fachpersonen dokumentieren ihre Arbeit in einem elektronischen System. " * 20


def library(zengine, fake_zotero, tmp_path):
    fake_zotero.add_collection("COLA0001", "Korpus")
    fake_zotero.add_item("ITEMOK01", "Gute Studie", "Muster", "2022", collections=["COLA0001"])
    good = tmp_path / "gut.pdf"
    good.write_bytes(pdfmake.build([pdfmake.text_page(PARA)]))
    fake_zotero.add_file("ATTOK001", "ITEMOK01", good, "application/pdf")
    fake_zotero.add_item("ITEMBAD1", "Kaputte Studie", "Probst", "2020", collections=["COLA0001"])
    bad = tmp_path / "kaputt.pdf"
    bad.write_bytes(b"%PDF-1.7\nkein gueltiges PDF\n%%EOF\n")
    fake_zotero.add_file("ATTBAD01", "ITEMBAD1", bad, "application/pdf")
    return zengine.store.create_corpus("Korpus", ["users/0:COLA0001"])


def status(engine, key):
    return engine.store.attachment("users/0", key)["status"]


def test_run_reports_texts_with_errors(zengine, fake_zotero, tmp_path):
    cid = library(zengine, fake_zotero, tmp_path)
    result, job = run_index(zengine, corpus_id=cid)
    assert status(zengine, "ATTOK001") == "indexed" and status(zengine, "ATTBAD01") == "error"
    assert job.counters["open_errors"] == 1 and job.counters["corpus_id"] == cid

    # Nächster Lauf: nichts Neues, der kaputte Text wird nicht neu gelesen – die Meldung sagt das
    result, job = run_index(zengine, corpus_id=cid)
    assert job.counters["docs_total"] == 0
    assert job.message == "Index ist aktuell – Texte mit Fehler oder ohne Text: 1"
    assert job.counters["open_errors"] == 1 and job.counters["open_no_text"] == 0
    assert zengine.store.problem_counts() == {"error": 1, "no_text": 0}
    assert zengine.store.problem_counts({("users/0", "ITEMOK01")}) == {"error": 0, "no_text": 0}

    # „Erneut versuchen“ für diesen Korpus liest ihn nochmals
    result, job = run_index(zengine, corpus_id=cid, retry_errors=True)
    assert job.counters["docs_total"] == 1 and status(zengine, "ATTBAD01") == "error"


def test_texts_with_errors_are_read_again_once_after_an_update(zengine, fake_zotero, tmp_path):
    cid = library(zengine, fake_zotero, tmp_path)
    run_index(zengine, corpus_id=cid)
    assert zengine.store.get_meta("errors_retried_version") == __version__
    result, job = run_index(zengine, corpus_id=cid)
    assert job.counters["docs_total"] == 0  # gleiche Version: kein neuer Versuch

    zengine.store.set_meta("errors_retried_version", "1.2.9")  # wie nach einem Update
    result, job = run_index(zengine, corpus_id=cid)
    assert job.counters["docs_total"] == 1
    assert any(f"Neue Version {__version__}: Texte mit Fehler werden erneut gelesen (1)" in line for line in job.log)
    assert status(zengine, "ATTBAD01") == "error"  # scheitert wieder: bleibt bis zum nächsten Update
    result, job = run_index(zengine, corpus_id=cid)
    assert job.counters["docs_total"] == 0


def test_corpus_endpoint_retries_errors(zengine, fake_zotero, tmp_path):
    cid = library(zengine, fake_zotero, tmp_path)
    run_index(zengine, corpus_id=cid)
    client = TestClient(create_app(zengine))
    corpus = next(c for c in client.get("/api/corpora").json()["corpora"] if c["id"] == cid)
    assert corpus["stats"]["error"] == 1
    r = client.post(f"/api/corpora/{cid}/index", json={"retry_errors": True}, headers={"X-Zotivity": "1"}).json()
    from test_server import wait_job

    state = wait_job(client, r["id"])
    assert state["status"] == "done" and state["counters"]["docs_total"] == 1
    assert state["counters"]["open_errors"] == 1
