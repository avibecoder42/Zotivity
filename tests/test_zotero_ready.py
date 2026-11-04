"""Zotivity darf Zotero beim Start nicht stören: Solange Zotero nicht bereit ist, geht nur der Ping hinaus, und eine
hängende Schnittstelle meldet sich rasch mit einem klaren Hinweis."""

import httpx
import pytest

from zotivity.zotero import ZoteroUnavailable
from zotivity.zotero import client as client_mod
from zotivity.zotero.client import HANGING, STARTING, ZoteroClient


class Zotero:
    """Attrappe: Ping antwortet immer, Plugin und /api/ je nach Zustand. Zählt, was angefragt wurde."""

    def __init__(self, plugin=False, running=True, hanging=False):
        self.plugin, self.running, self.hanging = plugin, running, hanging
        self.paths = []

    def __call__(self, request):
        path = request.url.path
        self.paths.append(path)
        if not self.running:
            raise httpx.ConnectError("refused", request=request)
        if path == "/connector/ping":
            return httpx.Response(200, headers={"X-Zotero-Version": "10.0.7"})
        if path.endswith("/uris"):
            # Wie Zotero: Die Schnittstelle des Plugins kennt nur POST, GET gibt 400; ohne Plugin 404
            return httpx.Response(400 if self.plugin and path == "/zotivity/uris" else 404)
        if self.hanging:
            raise httpx.ReadTimeout("hängt", request=request)
        return httpx.Response(200, headers={"Zotero-Server-ID": "abc"}, json=[])

    @property
    def api_calls(self):
        return [p for p in self.paths if p.startswith("/api")]


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(client_mod.time, "monotonic", lambda: now[0])
    return now


def make(zotero, monkeypatch, grace=20.0, every=5.0, plugin_wait=60.0):
    monkeypatch.setattr(ZoteroClient, "STARTUP_GRACE", grace)
    monkeypatch.setattr(ZoteroClient, "PLUGIN_WAIT", plugin_wait)
    monkeypatch.setattr(ZoteroClient, "PROBE_EVERY", every)
    return ZoteroClient(keystore=object(), transport=httpx.MockTransport(zotero))


def test_no_api_request_while_zotero_is_starting(monkeypatch, clock):
    z = Zotero()
    c = make(z, monkeypatch)
    st = c.status()
    assert st["running"] and not st["local_api"] and st["message"] == STARTING
    with pytest.raises(ZoteroUnavailable, match="startet noch"):
        c.groups()
    assert z.api_calls == []
    clock[0] += 19
    assert c.status()["message"] == STARTING and z.api_calls == []
    clock[0] += 2  # Wartezeit vorbei
    st = c.status()
    assert st["local_api"] and st["server_id"] == "abc" and z.api_calls


def test_plugin_shows_that_zotero_is_ready(monkeypatch, clock):
    z = Zotero(plugin=True)
    c = make(z, monkeypatch)
    assert c.status()["local_api"]  # keine Wartezeit


def test_restart_is_noticed_through_the_plugin_without_a_failed_ping(monkeypatch, clock):
    z = Zotero(plugin=True)
    c = make(z, monkeypatch, every=0.0)
    assert c.status()["local_api"]
    z.plugin = False  # Zotero startet neu, das Plugin ist noch nicht geladen
    z.paths.clear()
    clock[0] += 100
    assert c.status()["message"] == STARTING and z.api_calls == []
    clock[0] += 30
    assert c.status()["message"] == STARTING and z.api_calls == []
    z.plugin = True  # Plugin geladen: sofort bereit
    assert c.status()["local_api"]


def test_closed_zotero_starts_the_wait_again(monkeypatch, clock):
    z = Zotero()
    c = make(z, monkeypatch, every=0.0)
    clock[0] += 1
    c.status()
    clock[0] += 25
    assert c.status()["local_api"]
    z.running = False
    st = c.status()
    assert not st["running"] and st["message"] == "Zotero läuft nicht."
    z.running = True
    z.paths.clear()
    clock[0] += 1
    assert c.status()["message"] == STARTING and z.api_calls == []


def test_hanging_interface_reports_fast_and_clearly(monkeypatch, clock):
    z = Zotero(plugin=True, hanging=True)
    c = make(z, monkeypatch)
    st = c.status()
    assert st["running"] and not st["local_api"] and st["message"] == HANGING
    calls = len(z.api_calls)
    # Eine Weile keine weiteren hängenden Anfragen: Die App bleibt bedienbar
    clock[0] += 10
    assert c.status()["message"] == HANGING
    with pytest.raises(ZoteroUnavailable, match="neu starten"):
        c.groups()
    assert len(z.api_calls) == calls
    # Danach wird wieder geprüft, und nach dem Neustart von Zotero geht es weiter
    clock[0] += 31
    z.hanging = False
    assert c.status()["local_api"]


def test_probe_uses_a_short_timeout(monkeypatch, clock):
    seen = []

    def handler(request):
        if request.url.path == "/api/":
            seen.append(request.extensions["timeout"]["read"])
        return httpx.Response(400 if request.url.path == "/zotivity/uris" else 200)

    c = make(handler, monkeypatch)
    c.status()
    assert seen and all(t <= ZoteroClient.PROBE_TIMEOUT for t in seen[:1])
