"""Literaturverzeichnis: Jeder zitierte Text hat einen Eintrag – auch wenn Zotero für ein Paket nicht alle liefert – und
mehr als 50 Einträge stehen durchgehend sortiert (Code-Prüfung 2026-11-11, C16 und C19)."""

import httpx
import pytest
from test_indexing_search import build_library, run_index

from zotivity.exporting import Citations, _bib_sort_key
from zotivity.rag import ReviewService

APA = "http://www.zotero.org/styles/apa"


@pytest.fixture
def indexed(zengine, fake_zotero):
    build_library(fake_zotero)
    run_index(zengine)
    return zengine


def many(fake_zotero, n=60) -> list[tuple[str, str]]:
    """n Einträge; die Reihenfolge der Schlüssel läuft den Namen entgegen (zwei Pakete, die sich überschneiden)."""
    keys = []
    for i in range(n):
        fake_zotero.add_item(f"MANY{i:04d}", f"Titel {i}", f"Autor{n - i:02d}", "2020")
        keys.append(("users/0", f"MANY{i:04d}"))
    return keys


def fail_requests(monkeypatch, zot, when):
    """Anfragen an Zotero scheitern lassen, wenn when(params) zutrifft: als Fehlerantwort (500) oder als Ausnahme."""
    real = zot._request

    def request(method, url, **kw):
        outcome = when(kw.get("params") or {})
        if outcome == "error":
            return httpx.Response(500, text="kaputt")
        if outcome == "raise":
            raise RuntimeError("Verbindung abgebrochen")
        return real(method, url, **kw)

    monkeypatch.setattr(zot, "_request", request)


def test_export_bibliography_is_sorted_across_packages(indexed, fake_zotero):
    keys = many(fake_zotero)
    fake_zotero.add_item("UMLAUT01", "Titel", "Özdemir", "2019")
    fake_zotero.add_item("ZETT0001", "Titel", "Zimmermann", "2019")
    cit = Citations(indexed, keys + [("users/0", "UMLAUT01"), ("users/0", "ZETT0001")], APA, "de")
    bib = cit.bibliography_text()
    assert len(bib) == 62 and cit.from_zotero
    assert bib == sorted(bib, key=_bib_sort_key)  # ein Verzeichnis, nicht zwei alphabetische Blöcke hintereinander
    assert bib[0].startswith("Autor01") and bib[-2].startswith("Özdemir") and bib[-1].startswith("Zimmermann")
    # Ein einzelnes Paket bleibt, wie Zotero es liefert
    one = Citations(indexed, [("users/0", "ITEMA001"), ("users/0", "ITEMB001")], APA, "de")
    assert [b.split(",")[0] for b in one.bibliography_text()] == ["Müller", "Devlieghere"]


def test_export_bibliography_has_an_entry_for_every_item(indexed, fake_zotero, monkeypatch):
    keys = [("users/0", "ITEMA001"), ("users/0", "ITEMB001")]
    del fake_zotero.items["ITEMB001"]  # in Zotero gelöscht, im Index noch bekannt
    bib = Citations(indexed, keys, APA, "de").bibliography_text()
    assert len(bib) == 2 and bib[0].startswith("Devlieghere 2018") and bib[1].startswith("Müller, A. (2024)")

    # Das zweite Paket scheitert mittendrin: Die Einträge des ersten bleiben, der Rest kommt aus den gespeicherten Angaben
    keys = many(fake_zotero)
    fail_requests(monkeypatch, indexed.zotero(),
                  lambda p: "raise" if p.get("format") == "bib" and "MANY0055" in p.get("itemKey", "") else None)
    cit = Citations(indexed, keys, APA, "de")
    bib = cit.bibliography_text()
    assert len(bib) == 60 and not cit.from_zotero
    assert sum(1 for b in bib if b.startswith("Autor")) == 50  # aus Zotero; die übrigen zehn sind nicht indexiert


def test_review_bibliography_has_an_entry_for_every_cited_text(indexed, fake_zotero, monkeypatch):
    svc = ReviewService(indexed)
    keys = ["ITEMA001", "ITEMB001"]
    docs = {k: {"lib": "users/0", "item_key": k} for k in keys}
    items = svc._items(("users/0", k) for k in keys)
    assert svc._bibliography(keys, docs, items).splitlines() == [
        "- Devlieghere, A. (2018). Information systems in social work.", "- Müller, A. (2024). Chatbots als Gesprächspartner."]

    # Ein zitierter Text ist in Zotero gelöscht: Sein Eintrag kommt aus den gespeicherten Angaben (früher fehlte er still)
    del fake_zotero.items["ITEMB001"]
    assert svc._bibliography(keys, docs, items).splitlines() == [
        "- Devlieghere 2018: Information systems in social work.", "- Müller, A. (2024). Chatbots als Gesprächspartner."]

    # Ein Paket scheitert als Ganzes: je Eintrag einzeln, keiner fehlt
    keys = [k for _, k in many(fake_zotero)]
    docs = {k: {"lib": "users/0", "item_key": k} for k in keys}
    fail_requests(monkeypatch, indexed.zotero(),
                  lambda p: "error" if p.get("format") == "bib" and p.get("itemKey", "").count(",") == 9 else None)
    lines = svc._bibliography(keys, docs, {}).splitlines()
    assert len(lines) == 60 and all(line.startswith("- Autor") for line in lines)
    assert lines == sorted(lines, key=str.casefold)

    # Zotero nicht erreichbar: alle Einträge aus den gespeicherten Angaben, ohne es für jeden neu zu versuchen
    tries = []
    fail_requests(monkeypatch, indexed.zotero(), lambda p: tries.append(1) or "raise")
    lines = svc._bibliography(["ITEMA001", "ITEMB001"], docs | {"ITEMA001": {"lib": "users/0"}, "ITEMB001": {"lib": "users/0"}}, items).splitlines()
    assert lines == ["- Devlieghere 2018: Information systems in social work.", "- Müller 2024: Chatbots als Gesprächspartner."]
    assert len(tries) == 1
