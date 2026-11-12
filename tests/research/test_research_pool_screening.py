"""Pool mit Duplikaterkennung, Vorfilter, Zotero-Abgleich, Screening mit Rückgängig, Protokoll."""

from __future__ import annotations

import pytest

from zotivity.research.errors import ResearchError
from zotivity.research.records import dedup_keys, normalize


def make_project(service, **kw):
    return service.create_project({"title": "Jugendliche und Onlineaggressionen", "criteria_out": "Cyberbullying\nkeine Jugendlichen",
                                   "folder_map": [{"slot": 1, "label": "Teasing", "path": "Teasing/neu"},
                                                  {"slot": 2, "label": "Rollen", "path": "Rollen/neu"}], **kw})


def test_dedup_across_sources(service, sim):
    p = make_project(service)
    pid = p["id"]
    qp = service.add_query(pid, "T2_Teasing", "pubmed", "teasing[tiab]")
    qo = service.add_query(pid, "T2_Teasing", "openalex", "teasing")
    r1 = service.run_query(qp["id"])
    assert (r1["fetched"], r1["new"], r1["duplicates"]) == (2, 2, 0)
    r2 = service.run_query(qo["id"])
    assert (r2["fetched"], r2["new"], r2["duplicates"]) == (2, 1, 1)  # W1 = PMID 111 (DOI in anderer Schreibweise)

    recs = {r["title"]: r for r in service.store.records(pid)}
    assert len(recs) == 3
    merged = recs["Teasing online among adolescents"]
    assert merged["pmid"] == "111" and merged["openalex_id"] == "W1"
    assert {f["source"] for f in merged["found"]} == {"pubmed", "openalex"}
    s = service.summary(pid)
    assert s["sources"] == {"pubmed": 2, "openalex": 2} and s["pool"] == 3


def test_title_match_respects_years_and_ids(service):
    pid = make_project(service)["id"]
    run = service.store.add_run(pid, kind="import", source="ris")
    title = "Digital drama among adolescents in group chats"
    stats = service.store.add_records(pid, run, [
        {"title": title, "year": 2015},
        {"title": title, "year": 2016, "abstract": "filled"},   # gleiche Arbeit, Abstract wird ergänzt
        {"title": title, "year": 2024},                         # andere Arbeit (Jahr weit weg)
        {"title": title + ".", "doi": "10.9/one", "year": 2015},  # gleiche Arbeit, DOI wird ergänzt
        {"title": "Editorial"},                                   # zu kurz für den Titelabgleich, aber eigener Datensatz
    ])
    assert stats["new"] == 3
    recs = service.store.records(pid)
    first = next(r for r in recs if r["year"] == 2015)
    assert first["abstract"] == "filled" and first["doi"] == "10.9/one"


def test_keys():
    k = dedup_keys(normalize({"doi": "https://doi.org/10.1/ABC.", "pmid": "1", "openalex_id": "https://openalex.org/w5",
                              "title": "A sufficiently long title"}))
    assert k == {"doi:10.1/abc", "pmid:1", "oa:W5", "title:asufficientlylongtitle"}


def test_changed_target_library_asks_for_new_match(service, zotero):
    pid = make_project(service)["id"]
    run = service.store.add_run(pid, kind="import", source="ris")
    service.store.add_records(pid, run, [{"title": "Teasing and banter in WhatsApp groups", "abstract": "y" * 80, "doi": "10.5/known"},
                                         {"title": "Exclusion from group chats among adolescents", "abstract": "z" * 80, "doi": "10.5/given"}])
    known, given = sorted(service.store.records(pid), key=lambda r: r["title"], reverse=True)
    zotero.items = {"K1": {"key": "K1", "version": 1, "data": {"title": "Something else", "DOI": "10.5/KNOWN"}}}
    service.zotero_match(pid)
    service.store.add_link(pid, given["id"], "users/0", "K9", "Rollen/neu", "new")
    s = service.summary(pid)
    assert (s["in_zotero"], s["in_zotero_other"]) == (2, 0)

    service.update_project(pid, {"target_library": "groups/4755508", "target_name": "Gruppe"})
    s = service.summary(pid)
    assert (s["in_zotero"], s["in_zotero_other"]) == (0, 1)  # was dorthin übergeben wurde, zählt nicht als alter Abgleich

    zotero.items = {}
    service.zotero_match(pid)
    s = service.summary(pid)
    assert (s["in_zotero"], s["in_zotero_other"]) == (0, 0)
    recs = {r["id"]: r for r in service.store.records(pid)}
    assert recs[known["id"]]["zotero_key"] == "" and recs[given["id"]]["zotero_key"] == "K9"


def test_summary_tells_what_is_open(service, sim):
    pid = make_project(service)["id"]
    s = service.summary(pid)
    assert (s["queries"], s["queries_open"], s["handover_open"], s["fulltext_done"]) == (0, 0, 0, 0)
    q = service.add_query(pid, "T", "pubmed", "teasing[tiab]")
    service.add_query(pid, "T", "psycinfo", "teasing")  # läuft über den RIS-Weg, zählt nicht als ungesucht
    s = service.summary(pid)
    assert (s["queries"], s["queries_open"]) == (2, 1)
    service.run_query(q["id"])
    assert service.summary(pid)["queries_open"] == 0
    service.update_query(q["id"], {"string": "banter[tiab]"})  # neue Fassung: wieder ungesucht
    assert service.summary(pid)["queries_open"] == 1
    a, b = [r["id"] for r in service.store.records(pid)][:2]
    service.decide(pid, a, "include", folder=1)
    service.decide(pid, b, "include", folder=2)
    s = service.summary(pid)
    assert (s["included"], s["handover_open"]) == (2, 2)
    service.store.add_link(pid, a, "users/0", "K1", "Teasing/neu", "new")
    assert service.summary(pid)["handover_open"] == 1
    service.store.set_fulltext(pid, [a], "include")
    assert service.summary(pid)["fulltext_done"] == 1


def test_prefilters_and_zotero_match(service, zotero):
    pid = make_project(service)["id"]
    run = service.store.add_run(pid, kind="import", source="ris")
    service.store.add_records(pid, run, [
        {"title": "Cyberbullying victimization in middle school", "abstract": "x" * 80, "lang": "en"},
        {"title": "Teasing and banter in WhatsApp groups", "abstract": "y" * 80, "lang": "en", "doi": "10.5/known"},
        {"title": "Ausgrenzung in Klassenchats von Jugendlichen", "abstract": "", "lang": "de"},
        {"title": "Conflits en ligne chez les adolescents", "abstract": "z" * 80, "lang": "fr"},
    ])
    zotero.items = {"K1": {"key": "K1", "version": 1, "data": {"title": "Something else", "DOI": "10.5/KNOWN"}}}
    m = service.zotero_match(pid)
    assert m == {"library": "users/0", "items": 1, "matches": 1}

    p = service.get_project(pid)
    pre = p["prefilters"]
    assert [f["kind"] for f in pre] == ["in_zotero", "no_abstract", "language"]
    pre.append({"kind": "title", "pattern": "bully* OR mobbing", "active": True})
    for f in pre:
        if f["kind"] == "no_abstract":
            f["active"] = True
    service.update_project(pid, {"prefilters": pre})
    pool = service.pool(pid)
    assert pool["total"] == 4 and pool["hidden"] == 3
    assert [it["title"] for it in pool["items"]] == ["Conflits en ligne chez les adolescents"]
    counts = {f["kind"]: f["count"] for f in pool["prefilters"]}
    assert counts == {"in_zotero": 1, "no_abstract": 1, "language": 1, "title": 1}  # „language“ ist aus: würde 1 ausblenden
    assert len(service.pool(pid, show_hidden=True)["items"]) == 4
    assert service.summary(pid)["visible"] == 1


def test_zotero_match_by_title_respects_years_and_ids(service, zotero):
    """Ein gleicher Titel genügt nicht: Preprint und Artikel, Neuauflage oder „Letter to the editor“ sind andere Arbeiten
    (Code-Prüfung 2026-11-11, E13) – sonst blendet der Vorfilter sie aus, und die Übergabe legt den fremden Eintrag ab."""
    pid = make_project(service)["id"]
    run = service.store.add_run(pid, kind="import", source="ris")
    title = "Digital drama among adolescents in group chats"
    service.store.add_records(pid, run, [
        {"title": title, "year": 2024, "doi": "10.5/article"},                         # in Zotero liegt nur der Preprint
        {"title": "Teasing and banter in WhatsApp groups", "year": 2019},              # in Zotero: Auflage von 2008
        {"title": "Online conflict in classroom chats today", "year": 2021},           # gleiches Jahr, keine Kennungen
        {"title": "Something completely different here", "doi": "10.5/same", "year": 2015},  # gleiche DOI, anderer Titel
    ])
    zotero.items = {
        "PRE": {"key": "PRE", "data": {"title": title, "DOI": "10.5/preprint", "date": "2023-11-02"}},
        "OLD": {"key": "OLD", "data": {"title": "Teasing and banter in WhatsApp groups", "date": "2008"}},
        "NEW": {"key": "NEW", "data": {"title": "Online conflict in classroom chats today", "date": "March 2021"}},
        "DOI": {"key": "DOI", "data": {"title": "Ein anderer Titel in Zotero", "extra": "DOI: 10.5/SAME", "date": "1999"}},
    }
    assert service.zotero_match(pid)["matches"] == 2
    keys = {r["title"]: r["zotero_key"] for r in service.store.records(pid)}
    assert keys == {title: "", "Teasing and banter in WhatsApp groups": "",
                    "Online conflict in classroom chats today": "NEW", "Something completely different here": "DOI"}
    # Zwei Einträge mit gleichem Titel in Zotero: Es zählt der, der nicht widerspricht
    zotero.items["ART"] = {"key": "ART", "data": {"title": title, "DOI": "10.5/other", "date": "2024"}}
    zotero.items["FIT"] = {"key": "FIT", "data": {"title": title, "date": "2024"}}
    service.zotero_match(pid)
    assert {r["title"]: r["zotero_key"] for r in service.store.records(pid)}[title] == "FIT"


def test_handed_over_records_stay_visible(service):
    """Die Übergabe setzt den Zotero-Schlüssel – das darf gesichtete Treffer nicht nachträglich ausblenden."""
    pid = make_project(service)["id"]
    run = service.store.add_run(pid, kind="import", source="ris")
    service.store.add_records(pid, run, [{"title": f"Record number {i} about online conflict", "abstract": "a" * 60} for i in range(4)])
    ids = [it["id"] for it in service.queue(pid)["items"]]
    service.decide(pid, ids[0], "include", 2)
    service.decide(pid, ids[1], "exclude", reason="Cyberbullying")
    service.store.add_link(pid, ids[0], "users/0", "K0", "COLL", "created")
    s = service.summary(pid)
    assert (s["visible"], s["screened"], s["included"], s["hidden"]) == (4, 2, 1, 0)
    assert [it["id"] for it in service.queue(pid, "include")["items"]] == [ids[0]]
    assert service.pool(pid)["hidden"] == 0

    # Noch nicht gesichtet und schon in Zotero: Das blendet der Vorfilter weiter aus
    service.store.add_link(pid, ids[2], "users/0", "K2", "COLL", "added")
    s = service.summary(pid)
    assert (s["visible"], s["screened"], s["hidden"]) == (3, 2, 1)

    # Abgeschaltet nennt er nur, was er tatsächlich ausblenden würde, nicht die gesichteten Treffer
    p = service.get_project(pid)
    off = [{**f, "active": False} if f["kind"] == "in_zotero" else f for f in p["prefilters"]]
    service.update_project(pid, {"prefilters": off})
    assert next(f for f in service.pool(pid)["prefilters"] if f["kind"] == "in_zotero")["count"] == 1
    service.update_project(pid, {"prefilters": p["prefilters"]})

    # Ein über „Ausgeblendete prüfen“ bestätigter Ausschluss bleibt ausgeblendet
    service.review(pid, [ids[2]], "exclude")
    assert service.store.current_decision(pid, ids[2])["status"] == "exclude"
    assert service.summary(pid)["hidden"] == 1

    # Rückgängig: wieder offen und in Zotero, also wieder ausgeblendet
    service.store.add_link(pid, ids[3], "users/0", "K3", "COLL", "added")
    service.decide(pid, ids[3], "include", 2)
    assert ids[3] not in [it["id"] for it in service.pool(pid, status="hidden")["items"]]
    service.undo(pid)
    assert ids[3] in [it["id"] for it in service.pool(pid, status="hidden")["items"]]


def test_screening_decide_and_undo(service):
    pid = make_project(service)["id"]
    run = service.store.add_run(pid, kind="import", source="ris")
    service.store.add_records(pid, run, [{"title": f"Record number {i} about online conflict", "abstract": "a" * 60} for i in range(3)])
    q = service.queue(pid)
    ids = [it["id"] for it in q["items"]]
    assert len(ids) == 3 and q["criteria_out"] == ["Cyberbullying", "keine Jugendlichen"]

    service.decide(pid, ids[0], "include", 2)
    service.decide(pid, ids[1], "exclude", reason="Cyberbullying")
    service.decide(pid, ids[1], "maybe")
    with pytest.raises(ResearchError):
        service.decide(pid, ids[2], "include")  # ohne Ordner
    s = service.summary(pid)
    assert (s["screened"], s["included"], s["maybe"], s["excluded"]) == (2, 1, 1, 0)

    undone = service.undo(pid)
    assert undone["record_id"] == ids[1] and undone["status"] == "maybe"
    assert service.queue(pid, "exclude")["items"][0]["decision"]["reason"] == "Cyberbullying"  # vorheriger Entscheid gilt wieder
    assert [it["id"] for it in service.queue(pid, "open")["items"]] == [ids[2]]
    assert len(service.store.decision_history(pid)) == 2


def test_query_versions(service):
    pid = make_project(service)["id"]
    q1 = service.add_query(pid, "K1", "pubmed", "a[tiab]")
    q2 = service.update_query(q1["id"], {"string": "a[tiab] OR b[tiab]"})
    assert q2["version"] == 2 and q2["id"] != q1["id"]
    q3 = service.update_query(q2["id"], {"limits": {"year_from": 2006}})
    assert q3["id"] == q2["id"] and q3["limits"] == {"year_from": 2006}
    assert [q["version"] for q in service.store.queries(pid)] == [2]
    assert [q["version"] for q in service.store.query_versions(q2["id"])] == [2, 1]


def test_import_query_files(service):
    pid = make_project(service)["id"]
    files = [{"filename": "T1_Ostracism.txt", "content": "ostracism[tiab]\n AND teen*[tiab]"}]
    out = service.import_query_files(pid, files)
    assert out[0]["name"] == "T1_Ostracism" and out[0]["string"] == "ostracism[tiab] AND teen*[tiab]"
    again = service.import_query_files(pid, files)
    assert again[0]["id"] == out[0]["id"]  # unverändert → keine neue Version


def test_ris_import_and_protocol(service, sim):
    pid = make_project(service, question="Wie gehen Jugendliche mit Onlineaggressionen um?")["id"]
    q = service.add_query(pid, "T2_Teasing", "pubmed", "teasing[tiab]", {"year_from": 2006})
    service.count_query(q["id"])
    service.run_query(q["id"])
    from test_research_sources import OVID

    r = service.import_ris(pid, "psycinfo.ris", OVID.encode("cp1252"), "psycinfo", "T1_Ostracism", "exp Ostracism/", 120)
    assert r["new"] == 2 and r["hits"] == 120
    with pytest.raises(ResearchError):
        service.import_ris(pid, "leer.ris", b"nothing here", "psycinfo")
    rid = service.store.records(pid)[0]["id"]
    service.decide(pid, rid, "include", 1)
    other = service.store.records(pid)[1]["id"]
    service.decide(pid, other, "exclude", reason="Cyberbullying")

    md, name, mime = service.protocol(pid, "md")
    assert name.startswith("Suchprotokoll_Jugendliche") and mime == "text/markdown"
    assert "## Suchen" in md and "| PubMed | `T2_Teasing` (v1) | 2006–… |" in md
    assert "translated(teasing[tiab])" in md and "APA PsycInfo (Ovid)" in md
    assert "eingeschlossen: 1" in md and "| Cyberbullying | 1 |" in md
    assert "### Teasing (1)" in md

    csv_runs, _, _ = service.protocol(pid, "runs")
    assert csv_runs.startswith("﻿Datum;Quelle;Name") and "psycinfo.ris" in csv_runs
    csv_recs, _, _ = service.protocol(pid, "records")
    assert "eingeschlossen;Teasing" in csv_recs


def test_pool_follows_latest_search_and_reset(service, sim):
    """Der Pool zeigt je Suchstring nur die letzte Suche; gelöschte Suchstrings verschwinden, Entschiedenes bleibt."""
    import pytest

    from zotivity.research.errors import ResearchError

    pid = make_project(service)["id"]
    q = service.add_query(pid, "T2_Teasing", "pubmed", "teasing[tiab]")
    service.run_query(q["id"])
    first = service.pool(pid)["items"]
    assert len(first) == 2
    service.decide(pid, first[0]["id"], "include", 1)
    service.run_query(q["id"], max_records=1)  # enger: nur noch 1 geholt
    items = service.pool(pid)["items"]
    latest = {it["id"] for it in items if not it["stale"]}
    assert len(latest) == 1 and {it["id"] for it in items} == latest | {first[0]["id"]}  # der andere fällt weg
    assert service.summary(pid)["pool"] == len(items)

    service.delete_query(q["id"])
    left = service.pool(pid)["items"]
    assert [it["id"] for it in left] == [first[0]["id"]] and left[0]["stale"]  # nur der schon entschiedene
    assert service.pool(pid)["stale"] == 1 and service.queue(pid, "open")["items"] == []

    with pytest.raises(ResearchError):
        service.reset_pool(pid)
    r = service.reset_pool(pid, confirm=True)
    assert r == {"deleted": 2, "decisions": 1} and service.pool(pid)["total"] == 0
    assert service.store.q("SELECT COUNT(*) FROM run WHERE project_id = ?", (pid,))[0][0] == 2  # Protokoll der Läufe bleibt


def test_more_folders_and_move(service, sim):
    """Mehr als sechs Ordner; vor dem Löschen werden aufgenommene Treffer verschoben (rückgängig als Ganzes)."""
    import pytest

    from zotivity.research.errors import ResearchError

    pid = make_project(service)["id"]
    service.update_project(pid, {"folder_map": [{"slot": 1, "label": "Teasing", "path": "T"}, {"slot": 12, "label": "Neu", "path": "N"},
                                                {"slot": 21, "label": "zu viel", "path": "X"}]})
    assert [f["slot"] for f in service.get_project(pid)["folder_map"]] == [1, 12]  # höchstens 20
    q = service.add_query(pid, "T2_Teasing", "pubmed", "teasing[tiab]")
    service.run_query(q["id"])
    a, b = [it["id"] for it in service.pool(pid)["items"]]
    service.decide(pid, a, "include", 12)
    service.decide(pid, b, "include", 12)
    assert service.summary(pid)["by_folder"] == {"12": 2}
    with pytest.raises(ResearchError):
        service.decide(pid, a, "include", 21)
    r = service.move_folder(pid, 12, 1)
    assert r["moved"] == 2 and service.summary(pid)["by_folder"] == {"1": 2}
    assert service.store.current_decision(pid, a)["method"] == "card"  # Entscheidweise bleibt
    assert service.undo(pid)["count"] == 2 and service.summary(pid)["by_folder"] == {"12": 2}
    with pytest.raises(ResearchError):
        service.move_folder(pid, 1, 1)
