"""Knopf «Testen» je Schlüssel (Einstellungen → Datenbanken) und Semantic Scholar: Zählen ohne Abstracts, Drosselung
trotz Schlüssel – ohne Netz."""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from zotivity.research.errors import SourceError
from zotivity.research.http import Http
from zotivity.research.keycheck import check_key
from zotivity.research.ports import KEY_NCBI, KEY_OPENALEX, KEY_SEMANTICSCHOLAR, KEY_ZOTERO_WEB
from zotivity.research.sources.semanticscholar import KEY_WAITS, NO_KEY, THROTTLED, SemanticScholar
from zotivity.server.app import create_app

H = {"X-Zotivity": "1"}
SECRET = "SECRET-KEY-0042"


def http_for(handler, waits=None):
    return Http(transport=httpx.MockTransport(handler), sleep=(waits.append if waits is not None else lambda s: None))


@pytest.mark.parametrize("name, host", [(KEY_OPENALEX, "api.openalex.org"), (KEY_NCBI, "eutils.ncbi.nlm.nih.gov"),
                                        (KEY_SEMANTICSCHOLAR, "api.semanticscholar.org"), (KEY_ZOTERO_WEB, "api.zotero.org")])
def test_working_key(name, host):
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json={"username": "demo-user", "key": SECRET, "meta": {"count": 1}, "data": []})
    r = check_key(http_for(handler), name, SECRET)
    assert r["status"] == "ok" and r["http"] == 200 and len(seen) == 1 and seen[0].url.host == host
    # der Schlüssel geht an den Dienst (Parameter oder Kopfzeile), aber nie zurück an die Oberfläche
    assert SECRET in str(seen[0].url) or SECRET in seen[0].headers.values()
    assert SECRET not in str(r)
    assert (r.get("account") == "demo-user") == (name == KEY_ZOTERO_WEB)


def test_rejected_key_is_told_apart_from_throttling():
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(403 if req.headers.get("x-api-key") == "wrong" else 429, json={"message": "x"})
    assert check_key(http_for(handler), KEY_SEMANTICSCHOLAR, "wrong")["status"] == "rejected"
    assert check_key(http_for(handler), KEY_SEMANTICSCHOLAR, SECRET)["status"] == "throttled"
    assert len(calls) == 2  # keine Wiederholung beim Testen
    assert calls[1].url.params["fields"] == "paperId" and calls[1].url.params["limit"] == "1"


def test_ncbi_invalid_key_echoes_the_key_but_it_is_not_returned():
    def handler(req):
        return httpx.Response(400, json={"error": "API key invalid", "api-key": req.url.params["api_key"], "type": "invalid"})
    r = check_key(http_for(handler), KEY_NCBI, SECRET)
    assert r == {"status": "rejected", "http": 400}


def test_service_offline_and_missing():
    assert check_key(http_for(lambda req: httpx.Response(503)), KEY_OPENALEX, SECRET)["status"] == "service"
    assert check_key(http_for(lambda req: httpx.Response(404)), KEY_OPENALEX, SECRET)["status"] == "unknown"

    def down(req):
        raise httpx.ConnectError("no route", request=req)
    assert check_key(http_for(down), KEY_ZOTERO_WEB, SECRET) == {"status": "offline", "http": None}
    assert check_key(http_for(down), KEY_ZOTERO_WEB, None) == {"status": "missing", "http": None}


def test_test_endpoint(zengine, service, sim):
    client = TestClient(create_app(zengine, research=service))
    assert client.post("/api/research-keys/unbekannt/test", headers=H).status_code == 404
    assert client.post("/api/research-keys/semanticscholar/test", headers=H).json()["status"] == "missing"
    client.put("/api/research-keys/semanticscholar", json={"key": SECRET}, headers=H)
    sim.extra["api.semanticscholar.org"] = lambda req: httpx.Response(
        200 if req.headers.get("x-api-key") == SECRET else 403, json={"total": 1, "data": [{"paperId": "a"}]})
    r = client.post("/api/research-keys/semanticscholar/test", headers=H)
    assert r.status_code == 200 and r.json() == {"status": "ok", "http": 200} and SECRET not in r.text


# ------------------------------------------------------------------------------------------ Semantic Scholar
def test_count_fetches_only_ids():
    params = []

    def handler(req):
        params.append(dict(req.url.params))
        return httpx.Response(200, json={"total": 1234, "token": "t", "data": [{"paperId": "a"}]})
    assert SemanticScholar(http_for(handler), SECRET).count("teasing + teen*").count == 1234
    assert params[0]["fields"] == "paperId"


def test_throttling_with_key_waits_longer_and_says_so():
    waits, calls = [], []

    def handler(req):
        calls.append(req)
        return httpx.Response(429, json={"message": "Too Many Requests"})
    with pytest.raises(SourceError) as e:
        SemanticScholar(http_for(handler, waits), SECRET).count("x")
    assert str(e.value) == THROTTLED and "Schlüssel" not in str(e.value)
    assert waits == list(KEY_WAITS) and len(calls) == len(KEY_WAITS) + 1


def test_throttling_recovers_after_a_wait():
    waits, answers = [], [429, 200]

    def handler(req):
        return httpx.Response(answers.pop(0), json={"total": 7, "data": []})
    assert SemanticScholar(http_for(handler, waits), SECRET).count("x").count == 7
    assert waits == [KEY_WAITS[0]]


def test_throttling_without_key_suggests_one():
    waits = []
    with pytest.raises(SourceError, match="kostenlosen Schlüssel") as e:
        SemanticScholar(http_for(lambda req: httpx.Response(429), waits)).count("x")
    assert str(e.value) == NO_KEY and waits == [2]  # ohne Schlüssel wie bisher kurz
