"""Vorarbeiten für die Code-Signierung: Wahl «Beim Start nach Updates suchen» aus dem Setup, Datenschutzerklärung
im Setup, Angaben in der Programmdatei."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

from zotivity.install_options import take_installer_choice
from zotivity.settings import SettingsStore

ROOT = Path(__file__).resolve().parent.parent


def build_tools():
    spec = importlib.util.spec_from_file_location("build_tools", ROOT / "packaging" / "build_tools.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_installer_choice_is_taken_once(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    assert store.get().check_updates_on_start is True
    values = [0]
    taken = lambda: values.pop() if values else None  # noqa: E731 - liest und löscht wie die Registry
    assert take_installer_choice(store, taken) is False
    assert store.get().check_updates_on_start is False
    assert '"check_updates_on_start": false' in (tmp_path / "settings.json").read_text(encoding="utf-8")  # wie installer.iss sucht
    # Danach gilt die Einstellung im Programm: kein Wert mehr, nichts geändert
    store.update({"check_updates_on_start": True})
    assert take_installer_choice(store, taken) is None and store.get().check_updates_on_start is True
    values.append(1)
    assert take_installer_choice(store, taken) is True and store.get().check_updates_on_start is True


def test_installer_shows_privacy_and_offers_the_opt_out():
    iss = (ROOT / "packaging" / "installer.iss").read_bytes()
    assert iss.startswith(b"\xef\xbb\xbf")  # Inno Setup braucht UTF-8 mit BOM
    text = iss.decode("utf-8-sig")
    assert text.count('InfoBeforeFile: "..\\build\\privacy-') == 4
    assert re.search(r'Name: "updatecheck"; Description: "\{cm:UpdateCheckTask\}"', text)
    assert 'ValueName: "CheckUpdatesOnStart"; ValueData: 1; Tasks: updatecheck; Check: not WizardSilent' in text
    assert 'ValueName: "CheckUpdatesOnStart"; ValueData: 0; Tasks: not updatecheck; Check: not WizardSilent' in text
    from zotivity import install_options
    assert install_options.REG_KEY == "Software\\Zotivity" and install_options.REG_VALUE == "CheckUpdatesOnStart"
    assert "VersionInfoCopyright=" in text and "AppCopyright=" in text


def test_privacy_text_for_the_setup():
    bt = build_tools()
    for name in ("PRIVACY.md", "PRIVACY.de.md"):
        md = (ROOT / name).read_text(encoding="utf-8")
        assert "api.github" not in md and "github.com/avibecoder42/Zotivity" in md
        text = bt.privacy_text(md)
        assert "**" not in text and "`" not in text and "](" not in text and not text.startswith("#")
        # ein Absatz je Zeile: kein Umbruch mitten im Satz
        assert all(len(p.split("\n")) == 1 or all(x.startswith("- ") for x in p.split("\n")) for p in text.strip().split("\n\n"))
    de = bt.privacy_text((ROOT / "PRIVACY.de.md").read_text(encoding="utf-8"))
    assert "Beim Start nach Updates suchen" in de and "Einstellungen → Programm & Daten" in de
    en = bt.privacy_text((ROOT / "PRIVACY.md").read_text(encoding="utf-8"))
    assert "Check for updates at start-up" in en and "Settings → Program & data" in en


def test_program_file_names_product_version_and_copyright(tmp_path, monkeypatch):
    bt = build_tools()
    monkeypatch.setattr(bt, "ROOT", tmp_path)
    bt.version_info()
    info = (tmp_path / "build" / "version_info.txt").read_text(encoding="utf-8")
    from zotivity import __version__
    for field, value in (("ProductName", "Zotivity"), ("ProductVersion", __version__), ("FileVersion", __version__),
                         ("LegalCopyright", "Copyright (c) 2026 avibecoder. MIT License.")):
        assert f"StringStruct('{field}', '{value}')" in info
    spec = (ROOT / "packaging" / "zotivity.spec").read_text(encoding="utf-8")
    assert 'version=str(ROOT / "build" / "version_info.txt")' in spec
