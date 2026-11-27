"""Abbildungen in PDFs: finden, von einem Sprachmodell beschreiben lassen und durchsuchbar machen.

1. **Finden** (beim Einlesen, ``extract_pdf(figures=True)``): eingebettete Bilder und gezeichnete Diagramme, die gross
   genug sind, mit Bildunterschrift („Abbildung 3 …“) und dem Text in der Grafik (Achsen, Legende).
2. **Beschreiben** (eigener Auftrag, ``describe``): je Abbildung eine kurze Beschreibung durch das Hilfs- oder
   Hauptmodell, mit dem Bild, wenn das Modell Bilder versteht – sonst nur aus Bildunterschrift und Text. Die
   Beschreibung hängt an der Prüfsumme der Datei (``fkey``): Erneutes Einlesen derselben Datei kostet nichts.
3. **Durchsuchbar** als Abschnitte der Art „figure“ mit Seite, gekennzeichnet als maschinell beschrieben.
"""

from __future__ import annotations

import hashlib
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from zotivity.extract.chunker import Chunk
from zotivity.llm import ChatMessage, GenParams, LLMError

log = logging.getLogger(__name__)

LABEL = "Abbildung (maschinell beschrieben)"
IMAGE_ZOOM = 2.0
OUTPUT_TOKENS = (150, 400)  # je Beschreibung, für die Schätzung
INPUT_TOKENS = 900  # Text je Abbildung (ohne Bild), für die Schätzung
IMAGE_TOKENS = 800  # ein Bild, grob

SYSTEM = ("Du beschreibst Abbildungen aus wissenschaftlichen Texten für eine Literatursuche. Du bist genau und "
          "zurückhaltend: Du beschreibst nur, was zu sehen oder zu lesen ist, und erfindest nichts.")

PROMPT = """Abbildung auf Seite {page} von „{title}“ ({citation}).
Bildunterschrift: {caption}
Text in der Abbildung (Achsen, Legende, Beschriftungen): {inner}
Text der Seite (Auszug): {context}

{image_hint}Beschreibe die Abbildung in der Sprache des Texts in höchstens 120 Wörtern: Art der Darstellung, was sie zeigt (Variablen, Gruppen, Zeitraum), die wichtigste Aussage und – nur wenn eindeutig lesbar – zentrale Zahlen. Was du nicht sicher erkennst, lässt du weg oder kennzeichnest es als unsicher. Ist es keine inhaltliche Abbildung (Logo, Foto ohne Aussage, Zierrat), antworte nur mit „Keine inhaltliche Abbildung.“ Kein Markdown, keine Einleitung."""

NOT_A_FIGURE = "keine inhaltliche abbildung"


def fkey(md5: str | None, page: int, bbox, caption: str) -> str:
    """Schlüssel einer Abbildung: Datei (Prüfsumme), Seite, Lage und Bildunterschrift."""
    raw = f"{md5 or ''}|{page}|{','.join(str(round(v)) for v in bbox)}|{caption}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def records(candidates, md5: str | None, max_n: int) -> list[dict]:
    """Abbildungen aus dem Einlesen als Datensätze für den Speicher (höchstens max_n, Bilder mit Unterschrift zuerst)."""
    ranked = sorted(candidates, key=lambda f: (not f.caption, f.page))[:max_n]
    ranked.sort(key=lambda f: (f.page, f.bbox[1]))
    return [{"page": f.page, "kind": f.kind, "bbox": [round(v, 1) for v in f.bbox], "caption": f.caption,
             "text": f.text, "fkey": fkey(md5, f.page, f.bbox, f.caption)} for f in ranked]


def chunks_for(figures: list[dict], labels: list[str] | None) -> list[Chunk]:
    """Abschnitte der beschriebenen Abbildungen (ohne „keine inhaltliche Abbildung“)."""
    out = []
    for f in figures:
        desc = (f.get("description") or "").strip()
        if not desc or desc.lower().startswith(NOT_A_FIGURE):
            continue
        label = labels[f["page"]] if labels and f["page"] < len(labels) else ""
        head = LABEL + (f": {f['caption']}" if f.get("caption") else "")
        out.append(Chunk(ord=0, text=f"{head}\n{desc}", page_start=f["page"], page_end=f["page"], page_label=label,
                         kind="figure"))
    return out


def refresh_chunks(engine, lib: str, att_key: str, item_key: str, job=None) -> int:
    """Abschnitte der Abbildungen eines Anhangs neu schreiben und einbetten. Rückgabe: Zahl der Abschnitte."""
    store = engine.store
    chunks = chunks_for(store.figures(lib, att_key), store.page_labels(lib, att_key))
    store.set_figure_chunks(lib, item_key, att_key, chunks)
    if not chunks:
        return 0
    try:
        from zotivity.index.indexer import Indexer

        indexer = Indexer(engine)
        space = engine.space()
        while rows := store.document_chunks_to_embed(space, lib, att_key, item_key, 32):
            indexer._embed_rows(rows, job, space)
    except Exception as e:  # noqa: BLE001 - dann bettet der nächste Indexierlauf ein
        log.warning("Abbildungen nicht eingebettet: %s", e)
        if job:
            job.note(f"Abbildungen noch nicht eingebettet (nächster Indexierlauf): {e}")
    return len(chunks)


def _model(engine, job):
    from zotivity.rag.chat import helper_model, main_model

    cfg = engine.settings.llm
    meter = job.meter if job else None
    if engine.settings.figures.model == "main":
        return main_model(cfg, meter), cfg.effort
    return helper_model(cfg, meter), cfg.helper_effort


def _context(store, lib: str, att_key: str, page: int) -> str:
    rows = store.con.execute(
        "SELECT text FROM chunks WHERE lib=? AND att_key=? AND kind='text' AND page_start<=? AND page_end>=?"
        " ORDER BY ord", (lib, att_key, page, page)).fetchall()
    return " ".join(r[0] for r in rows)[:1200]


def estimate(engine, items: set[tuple[str, str]] | None = None) -> dict:
    """Umfang eines Auftrags „Abbildungen beschreiben“: Zahl, Token (Spanne), mit oder ohne Bild."""
    todo = engine.store.figures_to_describe(items)
    with_image = engine.settings.figures.send_image
    per_in = INPUT_TOKENS + (IMAGE_TOKENS if with_image else 0)
    n = len(todo)
    # figures: alle gefundenen, described: davon beschrieben, open: noch zu beschreiben (für die Schätzung)
    return {**engine.store.figure_stats(items), "open": n, "input_tokens": n * per_in,
            "output_tokens": [n * OUTPUT_TOKENS[0], n * OUTPUT_TOKENS[1]], "with_image": with_image}


def _no_images_key(model) -> str:
    return f"figures_no_images:{model.label}"


def describe(engine, job, items: set[tuple[str, str]] | None = None, limit: int | None = None) -> dict:
    """Abbildungen ohne Beschreibung beschreiben lassen; danach je Anhang die Abschnitte schreiben und einbetten."""
    from zotivity.extract.pdf import render_page_png
    from zotivity.rag.sources import author_year

    store = engine.store
    todo = store.figures_to_describe(items)
    if limit:
        todo = todo[:limit]
    model, effort = _model(engine, job)
    send_image = engine.settings.figures.send_image and store.get_meta(_no_images_key(model)) != "1"
    job.update(phase="Abbildungen beschreiben", total=len(todo), done=0,
               message=f"{len(todo)} Abbildungen mit {model.label}")
    result = {"described": 0, "errors": 0, "with_image": 0, "chunks": 0}
    items_meta = store.items({(f["lib"], f["item_key"]) for f in todo})
    state = {"images": send_image}

    def one(f: dict) -> tuple[dict, str, bool]:
        job.check()
        item = items_meta.get((f["lib"], f["item_key"])) or {}
        images: list[bytes] = []
        if state["images"] and f.get("path") and Path(f["path"]).is_file():
            try:
                images = [render_page_png(Path(f["path"]), f["page"], zoom=IMAGE_ZOOM, clip=tuple(f["bbox"]))]
            except Exception as e:  # noqa: BLE001 - dann ohne Bild
                log.info("Abbildung nicht gerendert: %s", e)
        prompt = PROMPT.format(
            page=f["page"] + 1, title=item.get("title", ""), citation=author_year(item) if item else "",
            caption=f.get("caption") or "(keine)", inner=f.get("text") or "(keiner)",
            context=_context(store, f["lib"], f["att_key"], f["page"]) or "(keiner)",
            image_hint="Das Bild der Abbildung ist beigefügt. " if images else
            "Es liegt kein Bild vor; stütze dich nur auf Bildunterschrift und Texte. ")
        params = GenParams(max_tokens=2000, effort=effort)
        try:
            text = model.complete(SYSTEM, [ChatMessage("user", prompt, images=images)], params).text
        except LLMError as e:
            if not images:
                raise
            # Versteht das Modell keine Bilder, ohne Bild weiter (und es sich für dieses Modell merken)
            log.info("Abbildung ohne Bild beschrieben (%s)", e)
            state["images"] = False
            store.set_meta(_no_images_key(model), "1")
            images = []
            prompt = prompt.replace("Das Bild der Abbildung ist beigefügt. ",
                                    "Es liegt kein Bild vor; stütze dich nur auf Bildunterschrift und Texte. ")
            text = model.complete(SYSTEM, [ChatMessage("user", prompt)], params).text
        return f, " ".join(text.split()), bool(images)

    touched: dict[tuple[str, str], str] = {}
    workers = max(1, min(engine.settings.review.parallel_requests, 4))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(one, f) for f in todo]
        for fut in as_completed(futures):
            try:
                f, text, with_image = fut.result()
            except Exception as e:  # noqa: BLE001
                if job.cancelled:
                    break
                result["errors"] += 1
                result["error"] = str(e)
                job.note(f"Abbildung nicht beschrieben: {e}")
                job.step()
                continue
            store.set_figure_description(f["fkey"], text, model.label, with_image)
            result["described"] += 1
            result["with_image"] += int(with_image)
            touched[(f["lib"], f["att_key"])] = f["item_key"]
            job.step(1, f"S. {f['page'] + 1}: {(f.get('caption') or '')[:80]}")
        if job.cancelled:
            for fut in futures:
                fut.cancel()
    for (lib, att_key), item_key in touched.items():
        result["chunks"] += refresh_chunks(engine, lib, att_key, item_key, job)
    job.check()
    job.update(phase="Fertig", message=f"{result['described']} Abbildungen beschrieben"
               + (f", {result['with_image']} mit Bild" if result["with_image"] else ", ohne Bild"))
    return result


def scan(engine, job, items: set[tuple[str, str]] | None = None) -> dict:
    """Abbildungen in bereits eingelesenen PDFs suchen (ohne den Text neu zu lesen oder neu einzubetten)."""
    from zotivity.extract.pdf import extract_pdf
    from zotivity.index.store import file_md5

    store = engine.store
    cfg = engine.settings.figures
    rows = store.con.execute(
        "SELECT lib, key, parent, path, content_type, indexed_md5 FROM attachments WHERE status='indexed'"
        " AND (content_type='application/pdf' OR path LIKE '%.pdf') AND path IS NOT NULL").fetchall()
    rows = [dict(r) for r in rows if items is None or (r["lib"], r["parent"]) in items]
    job.update(phase="Abbildungen suchen", total=len(rows), done=0, message=f"{len(rows)} PDFs")
    found = 0
    for r in rows:
        job.check()
        path = Path(r["path"])
        if not path.is_file():
            job.step()
            continue
        try:
            doc = extract_pdf(path, figures=True, max_pages=engine.settings.indexing.max_pages_per_document)
        except Exception as e:  # noqa: BLE001
            job.note(f"Nicht lesbar: {e}")
            job.step()
            continue
        recs = records(doc.figures, r["indexed_md5"] or file_md5(path), cfg.max_per_doc)
        store.put_figures(r["lib"], r["key"], r["parent"], recs)
        refresh_chunks(engine, r["lib"], r["key"], r["parent"], job)
        found += len(recs)
        job.step(1, f"{len(recs)} Abbildungen")
    job.update(phase="Fertig", message=f"{found} Abbildungen in {len(rows)} PDFs gefunden")
    return {"pdfs": len(rows), "figures": found}
