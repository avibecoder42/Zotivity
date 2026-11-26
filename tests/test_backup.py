"""Sicherung in einen selbst gewählten Ordner (z. B. switchdrive): Inhalt, Aufbewahrung, Fälligkeit, Schnittstelle."""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from zotivity import backup, paths
from zotivity.index.indexer import Indexer
from zotivity.jobs import Job
from zotivity.server.app import create_app

H = {"X-Zotivity": "1"}


class Clock:
    """Fester Takt für die Namen der Sicherungen (sonst kollidieren Sicherungen in derselben Sekunde)."""

    def __init__(self):
        self.t = datetime(2026, 11, 26, 9, 0, 0)

    def now(self):
        self.t += timedelta(seconds=1)
        return self.t

    @staticmethod
    def strptime(*a):
        return datetime.strptime(*a)


@pytest.fixture
def target(tmp_path_factory):
    return tmp_path_factory.mktemp("switchdrive")


@pytest.fixture
def data(engine):
    """Datenordner mit Recherche, eigenem Zitierstil und eigenen Modellangaben (wie bei einer echten Installation)."""
    d = paths.data_dir()
    con = sqlite3.connect(d / "research.db")
    con.execute("CREATE TABLE projects(id INTEGER PRIMARY KEY, title TEXT)")
    con.execute("INSERT INTO projects(title) VALUES('Onlineaggression (erfunden)')")
    con.commit()
    con.close()
    (d / "csl-styles").mkdir(exist_ok=True)
    (d / "csl-styles" / "mein-stil.csl").write_text("<style/>", encoding="utf-8")
    (paths.models_dir() / "user_models.json").write_text("[]", encoding="utf-8")
    (paths.models_dir() / "modell.gguf").write_bytes(b"x" * 1000)  # Modelldateien gehören nicht in die Sicherung
    engine.store.create_corpus("Demo", ["users/0:*"])
    return engine


def test_backup_contains_everything_that_zotero_cannot_restore(data, target):
    r = backup.run_backup(data, target, keep=3)
    folder = target / r["name"]
    assert folder.is_dir() and backup.NAME_RE.match(folder.name)
    assert not list(target.glob("*.unfertig"))
    names = sorted(str(p.relative_to(folder)).replace("\\", "/") for p in folder.rglob("*") if p.is_file())
    assert names == ["WIEDERHERSTELLEN.txt", "csl-styles/mein-stil.csl", "index.sqlite3", "models/user_models.json",
                     "research.db", "settings.json", "zotivity-sicherung.json"]
    # Die Kopien sind gültige Datenbanken mit dem Inhalt
    con = sqlite3.connect(folder / "index.sqlite3")
    assert con.execute("SELECT name FROM corpora").fetchall() == [("Demo",)]
    con.close()
    con = sqlite3.connect(folder / "research.db")
    assert con.execute("SELECT title FROM projects").fetchone()[0] == "Onlineaggression (erfunden)"
    con.close()
    manifest = json.loads((folder / backup.MANIFEST).read_text(encoding="utf-8"))
    assert manifest["app"] == "Zotivity" and "research.db" in manifest["files"] and manifest["bytes"] == r["bytes"]
    assert "%LOCALAPPDATA%" in (folder / "WIEDERHERSTELLEN.txt").read_text(encoding="utf-8")


def test_only_the_newest_backups_stay_and_nothing_else_is_touched(data, target, monkeypatch):
    monkeypatch.setattr(backup, "datetime", Clock())
    (target / "Meine Dateien").mkdir()
    (target / "Zotivity-Sicherung-alt-von-Hand").mkdir()  # passt nicht zum Muster: bleibt
    (target / "notiz.txt").write_text("bleibt", encoding="utf-8")
    names = [backup.run_backup(data, target, keep=2)["name"] for _ in range(4)]
    left = [b["name"] for b in backup.list_backups(target)]
    assert left == [names[3], names[2]]
    assert (target / "Meine Dateien").is_dir() and (target / "Zotivity-Sicherung-alt-von-Hand").is_dir()
    assert (target / "notiz.txt").read_text(encoding="utf-8") == "bleibt"


def test_folder_must_exist_and_lie_outside_the_data_folder(data, target):
    with pytest.raises(backup.BackupError, match="nicht erreichbar"):
        backup.check_folder(target / "fehlt")
    with pytest.raises(backup.BackupError, match="Datenordner"):
        backup.check_folder(paths.data_dir() / "csl-styles")
    with pytest.raises(backup.BackupError, match="Kein Ordner"):
        backup.check_folder("  ")
    assert backup.check_folder(str(target)) == target


def test_automatic_backup_when_due_and_errors_are_kept(data, target):
    assert not backup.due(data)  # kein Ordner gewählt
    data.settings_store.update({"backup": {"folder": str(target), "every_hours": 24}})
    assert backup.due(data)
    assert backup.maybe_backup(data)["name"]
    assert not backup.due(data)  # gerade gesichert
    assert backup.due(data, now=time.time() + 25 * 3600)
    data.settings_store.update({"backup": {"every_hours": 0}})
    assert not backup.due(data, now=time.time() + 999 * 3600)  # nur von Hand

    # Ordner weg (z. B. USB-Stick abgezogen): kein Abbruch, die Meldung bleibt für die Einstellungen
    data.settings_store.update({"backup": {"folder": str(target / "fehlt"), "every_hours": 24}})
    data.store.set_meta(backup.META_AT, "0")
    assert backup.maybe_backup(data) is None
    assert "nicht erreichbar" in backup.status(data)["error"]


def test_index_run_backs_up_into_the_folder(data, target):
    data.settings_store.update({"backup": {"folder": str(target)}})
    Indexer(data).run(Job("index", "t"), sync=False)
    assert len(backup.list_backups(target)) == 1


def test_backup_api(data, target):
    client = TestClient(create_app(data))
    s = client.get("/api/backup").json()
    assert s["folder"] == "" and s["backups"] == [] and s["every_hours"] == 24 and s["keep"] == 3
    assert client.post("/api/backup/check", json={"folder": str(target / "fehlt")}, headers=H).status_code == 400
    assert client.post("/api/backup/check", json={"folder": str(target)}, headers=H).json()["folder"] == str(target)
    r = client.post("/api/backup", json={"folder": str(target)}, headers=H)
    assert r.status_code == 200 and len(r.json()["backups"]) == 1 and r.json()["error"] == ""
    assert data.settings.backup.folder == str(target)
    assert client.put("/api/settings", json={"backup": {"keep": 5, "every_hours": 168}}, headers=H).status_code == 200
    assert client.get("/api/backup").json()["keep"] == 5
