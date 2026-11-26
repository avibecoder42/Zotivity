"""Sicherung in einen selbst gewählten Ordner (z. B. switchdrive, OneDrive oder ein USB-Stick).

Die eingebaute Sicherung des Index (``Indexer.maybe_backup``) liegt im Datenordner, also auf demselben Laufwerk.
Diese hier kopiert zusätzlich alles, was sich nicht aus Zotero wiederherstellen lässt, in einen Ordner nach Wahl:

- ``index.sqlite3`` – Index, Projekte, Korpora, Chats, Forschungsstände, Auswertungsblätter (konsistente Kopie über
  die Sicherungsschnittstelle von SQLite, auch während Zotivity läuft);
- ``research.db`` – die Recherchen;
- ``settings.json`` – die Einstellungen;
- ``csl-styles/`` – eigene Zitierstile aus dem CSL-Editor;
- ``models/*.json`` – eigene Embedding-Modelle und Präfixe (nicht die Modelldateien selbst).

API-Schlüssel gehören nicht dazu: Sie bleiben im Schlüsselspeicher des Betriebssystems.

Jede Sicherung ist ein eigener Unterordner ``Zotivity-Sicherung-JJJJMMTT-HHMMSS``. Er entsteht zuerst unter einem
Namen mit ``.unfertig`` und wird erst am Ende umbenannt; von älteren Sicherungen bleiben die neuesten ``keep``.
Andere Dateien im gewählten Ordner fasst Zotivity nie an.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

from zotivity import __version__, paths

log = logging.getLogger(__name__)

PREFIX = "Zotivity-Sicherung-"
NAME_RE = re.compile(r"^Zotivity-Sicherung-(\d{8}-\d{6})$")
MANIFEST = "zotivity-sicherung.json"
META_AT = "external_backup_at"
META_ERROR = "external_backup_error"
STARTUP_DELAY = 120  # Sekunden nach dem Start, damit der Start selbst nicht gebremst wird

RESTORE_TEXT = """Zotivity – Sicherung wiederherstellen / Restoring a backup

1. Zotivity beenden (auch im Hintergrund: Symbol im Infobereich bzw. Task-Manager).
2. Den Datenordner öffnen: Windows %LOCALAPPDATA%\\Zotivity (Einstellungen → Programm & Daten zeigt ihn).
3. Die Dateien und Ordner aus diesem Sicherungsordner dorthin kopieren und die vorhandenen ersetzen
   (index.sqlite3, research.db, settings.json, csl-styles, models). Dateien mit -wal oder -shm am Ende
   im Datenordner vorher löschen.
4. Zotivity starten. API-Schlüssel sind nicht in der Sicherung: unter Einstellungen erneut eintragen, falls
   es ein anderer Computer ist.

1. Quit Zotivity (also in the background: notification area icon or Task Manager).
2. Open the data folder: Windows %LOCALAPPDATA%\\Zotivity (Settings → Program & data shows it).
3. Copy the files and folders of this backup there, replacing the existing ones (index.sqlite3, research.db,
   settings.json, csl-styles, models). Delete files ending in -wal or -shm in the data folder first.
4. Start Zotivity. API keys are not in the backup: enter them again under Settings on another computer.
"""


class BackupError(RuntimeError):
    pass


_lock = threading.Lock()


def research_db_file() -> Path:
    return paths.data_dir() / "research.db"


def _sqlite_copy(src: Path, dest: Path) -> None:
    """Konsistente Kopie einer SQLite-Datenbank, auch wenn ein anderer Teil von Zotivity gerade schreibt."""
    source = sqlite3.connect(src, timeout=30)  # nur lesend benutzt; „mode=ro“ scheitert bei WAL ohne -shm
    target = sqlite3.connect(dest)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()


def check_folder(folder: str | Path) -> Path:
    """Gewählten Ordner prüfen: vorhanden, beschreibbar und nicht im Datenordner von Zotivity."""
    if not str(folder or "").strip():
        raise BackupError("Kein Ordner für die Sicherung gewählt.")
    path = Path(str(folder).strip()).expanduser()
    if not path.is_dir():
        raise BackupError(f"Der Ordner für die Sicherung ist nicht erreichbar: {path}")
    data = paths.data_dir().resolve()
    target = path.resolve()
    if target == data or data in target.parents:
        raise BackupError("Der Ordner für die Sicherung darf nicht im Datenordner von Zotivity liegen.")
    return path


def list_backups(folder: str | Path) -> list[dict]:
    """Fertige Sicherungen im Ordner, neueste zuerst."""
    path = Path(str(folder or "").strip()).expanduser()
    if not str(folder or "").strip() or not path.is_dir():
        return []
    out = []
    for d in path.iterdir():
        m = NAME_RE.match(d.name)
        if not m or not d.is_dir():
            continue
        size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
        created = datetime.strptime(m.group(1), "%Y%m%d-%H%M%S").timestamp()
        out.append({"name": d.name, "path": str(d), "created_at": created, "bytes": size})
    return sorted(out, key=lambda x: x["name"], reverse=True)


def run_backup(engine, folder: str | Path | None = None, keep: int | None = None) -> dict:
    """Eine Sicherung anlegen. Rückgabe: Name, Pfad, Grösse, Dateien; BackupError bei einem Problem."""
    cfg = engine.settings.backup
    base = check_folder(folder if folder is not None else cfg.folder)
    keep = keep or cfg.keep
    with _lock:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        name = PREFIX + stamp
        final = base / name
        tmp = base / (name + ".unfertig")
        if final.exists():
            raise BackupError("In dieser Sekunde gibt es schon eine Sicherung – bitte nochmals.")
        try:
            tmp.mkdir()
            files: list[str] = []
            engine.store.backup(tmp / "index.sqlite3")
            files.append("index.sqlite3")
            rdb = research_db_file()
            if rdb.exists():
                _sqlite_copy(rdb, tmp / "research.db")
                files.append("research.db")
            settings = Path(getattr(engine.settings_store, "path", None) or paths.settings_file())
            if settings.exists():
                shutil.copy2(settings, tmp / "settings.json")
                files.append("settings.json")
            styles = paths.data_dir() / "csl-styles"
            if styles.is_dir() and any(styles.iterdir()):
                shutil.copytree(styles, tmp / "csl-styles")
                files.append("csl-styles/")
            models = paths.data_dir() / "models"
            own = sorted(models.glob("*.json")) if models.is_dir() else []
            if own:
                (tmp / "models").mkdir()
                for f in own:
                    shutil.copy2(f, tmp / "models" / f.name)
                files += [f"models/{f.name}" for f in own]
            (tmp / "WIEDERHERSTELLEN.txt").write_text(RESTORE_TEXT, encoding="utf-8")
            size = sum(f.stat().st_size for f in tmp.rglob("*") if f.is_file())
            (tmp / MANIFEST).write_text(json.dumps({
                "app": "Zotivity", "version": __version__, "created_at": time.time(), "files": files, "bytes": size,
            }, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.rename(final)
        except BackupError:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        except Exception as e:  # noqa: BLE001 - als verständliche Meldung weitergeben
            shutil.rmtree(tmp, ignore_errors=True)
            raise BackupError(f"Sicherung fehlgeschlagen: {e}") from e
        # Ältere Sicherungen (nur eigene Unterordner) und liegengebliebene unfertige entfernen
        for old in list_backups(base)[keep:]:
            shutil.rmtree(old["path"], ignore_errors=True)
        for leftover in base.glob(PREFIX + "*.unfertig"):
            if leftover.is_dir() and time.time() - leftover.stat().st_mtime > 3600:
                shutil.rmtree(leftover, ignore_errors=True)
        engine.store.set_meta(META_AT, str(time.time()))
        engine.store.set_meta(META_ERROR, "")
        return {"name": name, "path": str(final), "bytes": size, "files": files}


def due(engine, now: float | None = None) -> bool:
    """Ist eine automatische Sicherung fällig? (Ordner gewählt, Rhythmus nicht 0, letzte lange genug her)"""
    cfg = engine.settings.backup
    if not cfg.folder.strip() or cfg.every_hours <= 0:
        return False
    last = float(engine.store.get_meta(META_AT) or 0)
    return (now or time.time()) - last >= cfg.every_hours * 3600


def maybe_backup(engine, job=None) -> dict | None:
    """Automatische Sicherung, wenn fällig. Fehler werden gemerkt (Anzeige in den Einstellungen), nie geworfen."""
    if not due(engine):
        return None
    try:
        if job:
            job.update(phase="Sicherung", message="Sicherung in den gewählten Ordner …")
        result = run_backup(engine)
        if job:
            job.note(f"Gesichert in {result['path']}")
        return result
    except Exception as e:  # noqa: BLE001
        log.warning("Sicherung in den gewählten Ordner fehlgeschlagen: %s", e)
        engine.store.set_meta(META_ERROR, str(e))
        if job:
            job.note(f"Sicherung in den gewählten Ordner fehlgeschlagen: {e}")
        return None


def schedule_startup_backup(engine, delay: float = STARTUP_DELAY) -> threading.Timer | None:
    """Kurz nach dem Start sichern, wenn eine Sicherung fällig ist (z. B. nach einem Tag mit Recherche)."""
    if not due(engine):
        return None
    timer = threading.Timer(delay, maybe_backup, args=(engine,))
    timer.daemon = True
    timer.start()
    return timer


def status(engine) -> dict:
    cfg = engine.settings.backup
    last = float(engine.store.get_meta(META_AT) or 0) or None
    return {"folder": cfg.folder, "keep": cfg.keep, "every_hours": cfg.every_hours, "last_at": last,
            "error": engine.store.get_meta(META_ERROR) or "", "backups": list_backups(cfg.folder),
            "data_dir": str(paths.data_dir())}
