"""Wahl im Installer übernehmen: «Beim Start nach Updates suchen» (Einstellung ``check_updates_on_start``).

Das Setup (``packaging/installer.iss``) schreibt die Wahl als DWORD ``CheckUpdatesOnStart`` unter
``HKCU\\Software\\Zotivity`` – nur bei einer Installation mit Assistent, nicht beim unbeaufsichtigten Update aus dem
Programm. Zotivity übernimmt den Wert beim nächsten Start einmal und löscht ihn; danach gilt wieder die Einstellung
im Programm (Einstellungen → Programm & Daten).
"""

from __future__ import annotations

import logging
import sys

log = logging.getLogger(__name__)

REG_KEY = r"Software\Zotivity"
REG_VALUE = "CheckUpdatesOnStart"


def _read_and_delete() -> int | None:  # pragma: no cover - nur unter Windows
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as k:
            value, _ = winreg.QueryValueEx(k, REG_VALUE)
            winreg.DeleteValue(k, REG_VALUE)
            return int(value)
    except (OSError, ValueError, TypeError):
        return None


def take_installer_choice(store, read_and_delete=None) -> bool | None:
    """Wahl des Installers in die Einstellungen übernehmen. Rückgabe: die übernommene Wahl oder None (keine)."""
    if read_and_delete is None:
        if sys.platform != "win32":
            return None
        read_and_delete = _read_and_delete
    value = read_and_delete()
    if value is None:
        return None
    choice = bool(value)
    try:
        if store.get().check_updates_on_start != choice:
            store.update({"check_updates_on_start": choice})
    except Exception as e:  # noqa: BLE001 - der Start darf daran nicht scheitern
        log.warning("Wahl des Installers nicht übernommen: %s", e)
        return None
    return choice
