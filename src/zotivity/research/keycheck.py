"""Schlüssel der Datenbanken prüfen (Einstellungen → Datenbanken, Knopf «Testen»).

Je Dienst eine kleine Anfrage mit dem gespeicherten Schlüssel, ohne Wiederholung. Das Ergebnis unterscheidet, ob es
am Schlüssel liegt («rejected») oder am Dienst («throttled», «service», «offline»). Weder der Schlüssel noch der Text
der Antwort gehen zurück: Manche Dienste wiederholen den Schlüssel in ihrer Fehlermeldung (NCBI).
"""

from __future__ import annotations

import httpx

from .errors import SourceError
from .http import Http
from .ports import KEY_NCBI, KEY_OPENALEX, KEY_SEMANTICSCHOLAR, KEY_ZOTERO_WEB

TIMEOUT = 20.0

# Ergebnis: ok · rejected (Schlüssel abgelehnt) · throttled (Dienst drosselt) · service (Fehler beim Dienst)
# · offline (keine Verbindung) · missing (kein Schlüssel gespeichert) · unknown (anderer HTTP-Status)
STATUSES = ("ok", "rejected", "throttled", "service", "offline", "missing", "unknown")


def _request(name: str, key: str) -> tuple[str, str, dict]:
    """Methode, Adresse und Parameter der Probeanfrage je Schlüssel."""
    if name == KEY_OPENALEX:
        return "GET", "https://api.openalex.org/works", {
            "params": {"filter": "title.search:test", "per-page": 1, "select": "id", "api_key": key}}
    if name == KEY_NCBI:
        return "GET", "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi", {
            "params": {"db": "pubmed", "term": "test", "retmax": 0, "retmode": "json", "api_key": key}}
    if name == KEY_SEMANTICSCHOLAR:
        # die leichte Relevanzsuche mit einem Treffer und nur der ID – eine schwere Anfrage löst selbst eine Drosselung aus
        return "GET", "https://api.semanticscholar.org/graph/v1/paper/search", {
            "params": {"query": "test", "limit": 1, "fields": "paperId"}, "headers": {"x-api-key": key}}
    if name == KEY_ZOTERO_WEB:
        return "GET", "https://api.zotero.org/keys/current", {
            "headers": {"Zotero-API-Key": key, "Zotero-API-Version": "3"}}
    raise ValueError(f"Unbekannter Schlüssel: {name}")


def _rejected(name: str, r: httpx.Response) -> bool:
    if r.status_code in (401, 403):
        return True
    # NCBI: ungültiger Schlüssel → HTTP 400 mit {"error": "API key invalid", …}
    if name == KEY_NCBI and r.status_code == 400:
        try:
            return "api key" in str((r.json() or {}).get("error", "")).lower()
        except ValueError:
            return False
    return False


def check_key(http: Http, name: str, key: str | None) -> dict:
    """{"status": …, "http": Statuscode oder None, "account": Zotero-Benutzername (nur Zotero-Web-API)}."""
    out: dict = {"status": "missing", "http": None}
    if not key:
        return out
    method, url, kw = _request(name, key)
    try:
        r = http.request(method, url, retries=1, check=False, timeout=TIMEOUT, **kw)
    except SourceError:  # nur bei Verbindungsfehlern (check=False)
        out["status"] = "offline"
        return out
    out["http"] = r.status_code
    if r.status_code == 200:
        out["status"] = "ok"
        if name == KEY_ZOTERO_WEB:
            try:
                out["account"] = str((r.json() or {}).get("username") or "")
            except ValueError:
                pass
    elif _rejected(name, r):
        out["status"] = "rejected"
    elif r.status_code == 429:
        out["status"] = "throttled"
    elif r.status_code >= 500:
        out["status"] = "service"
    else:
        out["status"] = "unknown"
    return out
