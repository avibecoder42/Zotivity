"""Dubletten: dasselbe Werk als mehrere Zotero-Einträge – Erkennung, Regel, nur die gewählte Kopie wird gelesen und
gefunden (auch in Korpora, die nur eine andere Kopie enthalten), Entscheidungen von Hand und über die API."""

from fastapi.testclient import TestClient
from test_indexing_search import TEEN, build_library, run_index

from zotivity.index import duplicates
from zotivity.index.duplicates import find_groups, normalize_doi, title_key
from zotivity.retrieval import Searcher

H = {"X-Zotivity": "1"}


def row(lib, key, title, author="Müller", year=2024, doi="", extra="", item_type="journalArticle"):
    return {"lib": lib, "key": key, "item_type": item_type, "title": title, "year": year, "doi": doi, "extra": extra,
            "creators": [{"lastName": author, "firstName": "A."}]}


def test_detection_rules():
    assert normalize_doi("https://doi.org/10.3389/FPSYG.2026.123.") == "10.3389/fpsyg.2026.123"
    assert title_key("Editorial", [{"lastName": "X"}], 2020) == ""  # zu kurz: nur über die DOI
    assert title_key("Die adaptive ökologische Falle", [{"lastName": "Gong"}], 2026) == \
        "die adaptive okologische falle|gong|2026"
    groups = find_groups([
        row("users/0", "A", "The adaptive ecological trap", doi="10.1234/abc"),
        row("groups/1", "B", "The Adaptive Ecological Trap!", doi=""),  # ohne DOI: über Titel/Autor/Jahr dazu
        row("groups/1", "C", "Ganz anderer Titel hier", extra="tex.x: y\nDOI: 10.1234/ABC"),  # DOI im Feld Extra
        row("users/0", "D", "Editorial", author="X", year=2020),
        row("groups/1", "E", "Editorial", author="X", year=2020),  # kurzer Titel ohne DOI: nicht zusammengefasst
        row("users/0", "F", "Preprint und Artikel gleich", doi="10.1234/pre"),
        row("groups/1", "G", "Preprint und Artikel gleich", doi="10.1234/art"),  # verschiedene DOIs: nicht zusammen
        row("users/0", "H", "Soziale Arbeit digital heute"),
        row("groups/1", "I", "Soziale Arbeit digital heute"),
        row("groups/1", "J", "Soziale Arbeit digital heute", year=2023),  # anderes Jahr
        row("users/0", "K", "Anhang ohne Eintrag hier", item_type="attachment"),
        row("groups/1", "L", "Anhang ohne Eintrag hier", item_type="attachment"),
    ])
    assert groups == {
        "doi:10.1234/abc": [("groups/1", "B"), ("groups/1", "C"), ("users/0", "A")],
        "t:soziale arbeit digital heute|muller|2024": [("groups/1", "I"), ("users/0", "H")],
    }


def test_rule_prefers_text_then_pages_then_library():
    q = {("users/0", "A"): (1, 0, 1), ("groups/1", "B"): (1, 0, 1)}
    assert duplicates.rule_choice(list(q), q, "users/0") == ("users/0", "A")
    assert duplicates.rule_choice(list(q), q, "groups/1") == ("groups/1", "B")
    q[("users/0", "A")] = (0, 0, 1)  # gelesen, aber ohne Text
    assert duplicates.rule_choice(list(q), q, "users/0") == ("groups/1", "B")
    q = {("users/0", "A"): (2, 0, 1), ("groups/1", "B"): (2, 1, 1)}  # beide gelesen, nur B mit Seitenzahlen
    assert duplicates.rule_choice(list(q), q, "users/0") == ("groups/1", "B")


def test_a_file_without_text_beats_no_file():
    # Fall „Were You Joking?“: PDF in der Gruppe ohne lesbaren Text, in Meine Bibliothek gar keine Datei
    q = {("users/0", "A"): (0, 0, 0), ("groups/1", "B"): (0, 0, 1)}
    assert duplicates.rule_choice(list(q), q, "users/0") == ("groups/1", "B")
    # Sind beide ohne Datei, gilt wie bisher die bevorzugte Bibliothek
    q = {("users/0", "A"): (0, 0, 0), ("groups/1", "B"): (0, 0, 0)}
    assert duplicates.rule_choice(list(q), q, "users/0") == ("users/0", "A")


def test_copy_from_the_corpus_is_preferred_among_equals():
    a, b = ("users/0", "A"), ("groups/1", "B")
    q = {a: (1, 0, 1), b: (1, 0, 1)}
    assert duplicates.rule_choice(list(q), q, "users/0", own={b}) == b
    # auch wenn die andere Kopie schon gelesen ist und Seitenzahlen hat
    q = {a: (2, 1, 1), b: (1, 0, 1)}
    assert duplicates.rule_choice(list(q), q, "users/0") == a
    assert duplicates.rule_choice(list(q), q, "users/0", own={b}) == b
    # aber nicht, wenn sie dafür schlechter ist: ohne Text oder ohne Datei
    q = {a: (1, 0, 1), b: (0, 0, 1)}
    assert duplicates.rule_choice(list(q), q, "users/0", own={b}) == a
    q = {a: (0, 0, 1), b: (0, 0, 0)}
    assert duplicates.rule_choice(list(q), q, "users/0", own={b}) == a


def test_quality_sees_the_file_even_without_text(zengine, fake_zotero):
    build_library(fake_zotero)
    # Kopie ohne Datei mit dem kleineren Schlüssel: Nach der alten Regel gewänne sie den Gleichstand
    fake_zotero.add_item("ITEM0000", "Chatbots als Gesprächspartner", "Müller", "2024", collections=["COLB0002"])
    run_index(zengine)
    zengine.store.set_attachment_status("users/0", "ATTA0001", "no_text", error="kein Text")
    work = next(iter(zengine.store.duplicate_groups()))
    g = duplicates.resolve(zengine.store, "users/0")[work]
    assert g["rule"] == ("users/0", "ITEMA001") and g["quality"][("users/0", "ITEM0000")] == (0, 0, 0)
    (d,) = duplicates.describe(zengine.store, "users/0", [work])
    facts = {c["key"]: (c["file"], c["chosen"]) for c in d["copies"]}
    assert facts == {"ITEM0000": (False, False), "ITEMA001": (True, True)}


def test_run_for_a_corpus_keeps_its_own_copy(zengine, fake_zotero):
    build_library(fake_zotero)
    add_copy(fake_zotero)
    from zotivity.index.indexer import Indexer

    run_index(zengine, sync=True, collections=["users/0:COLB0002"])
    work = next(iter(zengine.store.duplicate_groups()))
    c = zengine.store.duplicate_choices()[work]
    assert (c["mode"], c["item_key"]) == ("user", "ITEMD001")
    assert ("users/0", "ITEMD001") in Indexer(zengine).scope(collections=["users/0:COLB0002"])


def add_copy(fz):
    """Zweite Kopie von „Chatbots als Gesprächspartner“ in der Sammlung „Soziale Arbeit“ (eigene PDF)."""
    fz.add_item("ITEMD001", "Chatbots als Gesprächspartner", "Müller", "2024", collections=["COLB0002"])
    fz.add_pdf("ATTD0001", "ITEMD001", [TEEN * 4, "Zweite Seite der Kopie. " * 30])


def test_only_the_chosen_copy_is_read_and_found(zengine, fake_zotero):
    build_library(fake_zotero)
    add_copy(fake_zotero)
    result, job = run_index(zengine)
    work = next(iter(zengine.store.duplicate_groups()))
    assert zengine.store.duplicate_groups()[work] == [("users/0", "ITEMA001"), ("users/0", "ITEMD001")]
    # Lauf ohne Rückfrage: Regel übernommen (Gleichstand → kleinerer Schlüssel), die andere Kopie nicht gelesen
    assert job.counters["duplicates_auto"] == 1 and zengine.store.duplicate_choices()[work]["mode"] == "rule"
    assert result["extracted"] == 2
    assert zengine.store.attachment("users/0", "ATTD0001")["status"] == "pending"
    st = zengine.store.scope_stats(zengine.resolve_copies(zengine.store.allowed_items(
        zengine.make_filter({"corpus_id": zengine.store.corpora()[0]["id"]}))), zengine.space())
    assert st["documents"] == 2 and st["pending"] == 0  # die nicht gewählte Kopie zählt nicht als offen

    # Korpus nur mit der anderen Kopie: das Werk gehört dazu – über die gewählte Kopie
    cid = zengine.store.create_corpus("Soziale Arbeit", ["users/0:COLB0002"])
    from zotivity.index.indexer import Indexer

    assert Indexer(zengine).scope(corpus_id=cid) == {("users/0", "ITEMB001"), ("users/0", "ITEMA001")}
    assert Indexer(zengine).scope(corpus_id=cid, resolve=False) == {("users/0", "ITEMB001"), ("users/0", "ITEMD001")}
    hits = Searcher(zengine).search("Chatbots Jugendliche persönliche Probleme", zengine.make_filter({"corpus_id": cid}))
    assert hits and {h.chunk["item_key"] for h in hits} <= {"ITEMA001", "ITEMB001"}
    assert hits[0].chunk["item_key"] == "ITEMA001"

    # Von Hand die andere Kopie wählen: der nächste Lauf liest sie, Treffer kommen nur noch von ihr
    zengine.store.set_duplicate_choice(work, "user", "users/0", "ITEMD001")
    result, job = run_index(zengine)
    assert result["extracted"] == 1 and job.counters["duplicates_auto"] == 0
    assert zengine.store.attachment("users/0", "ATTD0001")["status"] == "indexed"
    hits = Searcher(zengine).search("Chatbots Jugendliche persönliche Probleme", k=10)
    keys = {h.chunk["item_key"] for h in hits}
    assert "ITEMD001" in keys and "ITEMA001" not in keys

    # Keine Dublette: beide Kopien werden verwendet
    zengine.store.set_duplicate_choice(work, "separate")
    assert zengine.copy_resolution() == {}
    keys = {h.chunk["item_key"] for h in Searcher(zengine).search("Chatbots Jugendliche persönliche Probleme", k=10)}
    assert {"ITEMA001", "ITEMD001"} <= keys


def test_duplicates_api(zengine, fake_zotero, monkeypatch):
    build_library(fake_zotero)
    add_copy(fake_zotero)
    monkeypatch.setattr("zotivity.server.app.ollama_available", lambda url: False)
    from zotivity.server.app import create_app

    with TestClient(create_app(zengine)) as c:
        # Vor dem ersten Lauf: Bibliothek noch nie abgeglichen → sync=1 gleicht vorher ab
        r = c.get("/api/duplicates?undecided=1&sync=1").json()
        assert r["ask"] is True and r["preferred"] == "users/0" and r["libraries"][0]["lib"] == "users/0"
        (g,) = r["groups"]
        assert g["mode"] is None and g["title"] == "Chatbots als Gesprächspartner" and len(g["copies"]) == 2
        a, d = g["copies"]
        assert a["key"] == "ITEMA001" and a["rule"] and a["chosen"] and a["text"] == 1
        assert a["collections"] == ["KI und Jugend › Unterordner"] and d["collections"] == ["Soziale Arbeit"]
        assert a["suggest"] and not d["suggest"] and not a["in_corpus"] and a["file"] and d["file"]  # ohne Korpus: die Regel
        # Für einen Korpus: dessen Kopie wird vorgeschlagen, die Regel und die Wahl bleiben, bis jemand entscheidet
        cid = c.post("/api/corpora", json={"name": "Soziale Arbeit", "sources": ["users/0:COLB0002"]}, headers=H).json()["id"]
        (own,) = c.get(f"/api/duplicates?undecided=1&corpus_id={cid}").json()["groups"]
        x, y = own["copies"]
        assert (x["suggest"], y["suggest"]) == (False, True) and (x["in_corpus"], y["in_corpus"]) == (False, True)
        assert x["rule"] and x["chosen"] and not y["rule"]
        assert c.post("/api/duplicates/choices", json={"choices": [
            {"work": g["work"], "mode": "user", "lib": "users/0", "key": "ITEMX"}]}, headers=H).status_code == 400
        assert c.post("/api/duplicates/choices", json={"choices": [
            {"work": g["work"], "mode": "user", "lib": "users/0", "key": "ITEMD001"}]}, headers=H).json()["saved"] == 1
        assert c.get("/api/duplicates?undecided=1").json()["groups"] == []
        (g,) = c.get("/api/duplicates").json()["groups"]
        assert g["mode"] == "user" and [x["chosen"] for x in g["copies"]] == [False, True]
        # Entscheidung zurücknehmen: gilt wieder als neu
        c.post("/api/duplicates/choices", json={"choices": [{"work": g["work"], "mode": None}]}, headers=H)
        assert len(c.get("/api/duplicates?undecided=1").json()["groups"]) == 1
