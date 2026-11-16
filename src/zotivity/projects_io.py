"""Projekte als Datei (.zoty, früher .zllm) exportieren und importieren – für Sicherung, Weitergabe oder einen anderen Rechner.

Die Datei ist ein ZIP mit project.json: Projekt, Korpus-Definitionen (Sammlungsschlüssel), Chats,
Forschungsstände samt PDF-Seiten ihrer Belege, die Auswertungsblätter der einbezogenen Texte und der Suchverlauf.
Nicht enthalten: der Index (wird am Ziel neu erstellt) und API-Schlüssel. Die PDF-Seiten der Belege lassen die Belege
am Ziel die richtige Seite öffnen, bevor die Texte dort indexiert sind (nur bei derselben Datei, s. ReviewService.page_map).
"""

from __future__ import annotations

import io
import json
import logging
import time
import zipfile

from zotivity import __version__
from zotivity.engine import Engine
from zotivity.zotero.tree import describe_sources, fetch_tree

log = logging.getLogger(__name__)

FORMAT = "zotivity-project"
LEGACY_FORMATS = ("zotllm-project", "zoterollm-project")  # .zllm-Dateien aus Versionen bis 1.0.1 (ZotLLM, ZoteroLLM)
FORMAT_VERSION = 1
MAX_BYTES = 200 * 1024 * 1024


def export_project(engine: Engine, project_id: int) -> tuple[str, bytes]:
    store = engine.store
    project = store.project(project_id)
    if not project:
        raise KeyError(project_id)
    corpora = [store.corpus(cid) for cid in project["corpus_ids"]]
    chats = []
    for c in store.chats(limit=100000, project_id=project_id):
        chats.append({"title": c["title"], "created_at": c["created_at"], "updated_at": c["updated_at"],
                      "messages": [{k: m[k] for k in ("role", "content", "sources", "meta", "created_at")}
                                   for m in store.chat_messages(c["id"])]})
    from zotivity.rag.review import ReviewService

    reviewer = ReviewService(engine)
    reviews, sheet_keys = [], set()
    for r in store.reviews(project_id=project_id):
        try:  # PDF-Seiten der Belege; fehlen sie, öffnen die Belege am Ziel erst nach dem Indexieren die richtige Seite
            pages = reviewer.page_map(r["id"])
        except Exception as e:  # noqa: BLE001
            log.warning("PDF-Seiten der Belege nicht exportiert: %s", e)
            pages = {}
        full = store.get_review(r["id"])  # nach page_map: das Umstellen alter Seitenangaben ist darin enthalten
        docs = store.review_docs(r["id"])
        sheet_keys.update((d["lib"], d["item_key"]) for d in docs if d["included"])
        state = {k: v for k, v in full["state"].items() if k != "_pdf_pages"}
        reviews.append({"title": full["title"], "question": full["question"], "state": state, "pages": pages,
                        "created_at": full["created_at"], "updated_at": full["updated_at"],
                        "docs": [{k: d[k] for k in ("lib", "item_key", "score", "hits", "decision", "reason",
                                                    "included")} for d in docs]})
    sheets = []
    for lib, key in sorted(sheet_keys):
        s = store.get_sheet(lib, key)
        if s:
            sheets.append({"lib": lib, "item_key": key, "sheet": s["sheet"], "model": s["model"],
                           "created_at": s["created_at"]})
    data = {
        "format": FORMAT, "version": FORMAT_VERSION, "app_version": __version__, "exported_at": time.time(),
        "project": {"name": project["name"], "description": project.get("description") or "",
                    "color": project.get("color") or "", "embedding": project.get("embedding")},
        "corpora": [{"id": c["id"], "name": c["name"], "description": c.get("description") or "",
                     "sources": c["sources"], "sources_info": describe_sources(engine, c["sources"])}
                    for c in corpora if c],
        "chats": chats, "reviews": reviews, "sheets": sheets,
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("project.json", json.dumps(data, ensure_ascii=False, indent=1))
    safe = "".join(ch if ch.isalnum() or ch in " -_" else "_" for ch in project["name"]).strip() or "projekt"
    return f"{safe}.zoty", buf.getvalue()


def _unique(name: str, taken: set[str]) -> str:
    out, n = name, 2
    while out.casefold() in taken:
        out, n = f"{name} ({n})", n + 1
    taken.add(out.casefold())
    return out


def _clean_pages(raw) -> dict[str, dict]:
    """PDF-Seiten der Belege aus der Datei: nur die erwartete Form übernehmen (Schlüssel → Anhang, MD5, Seiten)."""
    out: dict[str, dict] = {}
    if not isinstance(raw, dict):
        return out
    for key, entry in raw.items():
        if not isinstance(key, str) or not isinstance(entry, dict):
            continue
        att, md5 = entry.get("att"), entry.get("md5")
        if not (isinstance(att, str) and att and isinstance(md5, str) and md5):
            continue
        maps = {}
        for kind in ("cited", "located"):
            src = entry.get(kind) if isinstance(entry.get(kind), dict) else {}
            maps[kind] = {str(k): int(v) for k, v in src.items()
                          if isinstance(v, int) and not isinstance(v, bool) and 0 <= v < 100000}
        if maps["cited"] or maps["located"]:
            out[key] = {"att": att, "md5": md5, **maps}
    return out


def import_project(engine: Engine, payload: bytes) -> dict:
    if len(payload) > MAX_BYTES:
        raise ValueError("Die Datei ist zu gross.")
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as z:
            data = json.loads(z.read("project.json").decode("utf-8"))
    except (zipfile.BadZipFile, KeyError, ValueError) as e:
        raise ValueError("Keine gültige Projektdatei (.zoty oder .zllm).") from e
    if data.get("format") not in (FORMAT, *LEGACY_FORMATS) or int(data.get("version", 0)) > FORMAT_VERSION:
        raise ValueError("Unbekanntes Dateiformat – stammt die Datei aus einer neueren Zotivity-Version?")
    store = engine.store
    try:  # Sammlungen aktuell aus Zotero lesen, damit fehlende sicher erkannt werden
        online = fetch_tree(engine)["online"]
    except Exception:  # noqa: BLE001
        online = False
    p = data.get("project") or {}
    name = _unique(str(p.get("name") or "Importiertes Projekt"), {x["name"].casefold() for x in store.projects()})
    pid = store.create_project(name, str(p.get("description") or ""), str(p.get("color") or ""))
    if isinstance(p.get("embedding"), dict):
        from zotivity.engine import project_embedding

        store.update_project(pid, embedding=project_embedding(p["embedding"]))

    # Korpora: gleiche Quellen = derselbe Korpus (kein Duplikat); sonst neu anlegen
    existing = {tuple(sorted(c["sources"])): c["id"] for c in store.corpora()}
    taken = {c["name"].casefold() for c in store.corpora()}
    reused, created, missing = 0, 0, []
    for c in data.get("corpora") or []:
        sources = sorted({s for s in c.get("sources") or [] if isinstance(s, str) and ":" in s})
        cid = existing.get(tuple(sources))
        if cid:
            reused += 1
        else:
            cid = store.create_corpus(_unique(str(c.get("name") or "Korpus"), taken), sources,
                                      str(c.get("description") or ""))
            existing[tuple(sources)] = cid
            created += 1
        store.assign_corpus(pid, cid)
        for info in describe_sources(engine, sources) if online else []:
            if info.get("missing"):
                label = next((x.get("label") for x in c.get("sources_info") or [] if x.get("id") == info["id"]),
                             info["id"])
                missing.append(f"{c.get('name')}: {label}")

    for chat in data.get("chats") or []:
        chat_id = store.create_chat(str(chat.get("title") or "Chat"), project_id=pid)
        for m in chat.get("messages") or []:
            store.add_chat_message(chat_id, m.get("role", "user"), m.get("content", ""), sources=m.get("sources"),
                                   meta=m.get("meta"))
    for r in data.get("reviews") or []:
        state = dict(r.get("state") or {})
        state.pop("_pdf_pages", None)
        pages = _clean_pages(r.get("pages"))
        if pages:
            state["_pdf_pages"] = pages
        rid = store.create_review(str(r.get("title") or r.get("question") or "Forschungsstand"),
                                  str(r.get("question") or ""), state, project_id=pid)
        store.put_review_docs(rid, [d for d in r.get("docs") or [] if d.get("lib") and d.get("item_key")])
    for s in data.get("searches") or []:  # Dateien früherer Versionen: Der Suchverlauf gilt jetzt für alle Projekte
        if isinstance(s.get("query"), dict) and s["query"]:
            store.remember_search(s["query"], str(s.get("sort") or "relevance"), int(s.get("total") or 0),
                                  used_at=s.get("used_at"))
    sheets = 0
    for s in data.get("sheets") or []:
        if s.get("lib") and s.get("item_key") and not store.get_sheet(s["lib"], s["item_key"]):
            store.put_sheet(s["lib"], s["item_key"], s.get("sheet") or {}, s.get("model") or "")
            sheets += 1
    return {"project_id": pid, "name": name, "corpora_created": created, "corpora_reused": reused,
            "chats": len(data.get("chats") or []), "reviews": len(data.get("reviews") or []), "sheets": sheets,
            "missing_sources": missing, "checked": online}
