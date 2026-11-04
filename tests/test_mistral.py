"""Mistral über die OpenAI-kompatible Schnittstelle: Parameter, Denkspuren im Stream, Ablehnungen mit 422."""

import json
from types import SimpleNamespace as NS

import httpx
import openai
import pytest

from zotivity.catalog import PROVIDERS, sort_chat_models
from zotivity.llm import ChatMessage, GenParams, LLMError, base_url_for, make_chat_model, truncation
from zotivity.llm.openai_provider import OpenAIChat, _delta_text, _mistral_effort
from zotivity.research_adapters import SCHEMA_PROVIDERS, VENDORS
from zotivity.settings import LLMSettings

THINKING = {"type": "thinking", "thinking": [{"type": "text", "text": "Ich überlege."}]}


def chunk(content=None, finish=None, usage=None):
    choices = [NS(delta=NS(content=content), finish_reason=finish)] if content is not None or finish else []
    return NS(model="mistral-large-4", usage=usage, choices=choices)


class FakeCompletions:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def create(self, **kw):
        self.calls.append(kw)
        out = self.outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return iter(out)


def scripted(*outcomes, model="mistral-large-4"):
    chat = OpenAIChat(model, "k", provider="mistral", base_url="https://api.mistral.ai/v1")
    chat.client = NS(chat=NS(completions=FakeCompletions(*outcomes)))
    return chat


def rejected(param):
    response = httpx.Response(422, request=httpx.Request("POST", "https://api.mistral.ai/v1/chat/completions"))
    return openai.UnprocessableEntityError(f"Extra inputs are not permitted: {param}", response=response, body=None)


def test_catalog_and_base_url():
    p = PROVIDERS["mistral"]
    assert p.needs_key and p.chat and not p.embeddings
    assert p.default_main in p.chat_models and p.default_helper in p.chat_models
    assert base_url_for("mistral", LLMSettings()) == "https://api.mistral.ai/v1"
    assert base_url_for("mistral", LLMSettings(base_urls={"mistral": "https://api.eu.mistral.ai"})) == "https://api.eu.mistral.ai/v1"
    assert "mistral" in SCHEMA_PROVIDERS and VENDORS["mistral"] == "Mistral AI"


def test_model_list_hides_what_cannot_answer():
    models = ["mistral-embed", "mistral-ocr-2", "voxtral-small", "mistral-moderation", "mistral-small-2603", "codestral-2", "mistral-large-4"]
    assert sort_chat_models("mistral", models) == ["mistral-large-4", "mistral-small-2603", "codestral-2"]


def test_key_is_required_and_read_from_the_environment(monkeypatch):
    with pytest.raises(LLMError) as e:
        OpenAIChat("mistral-large-4", None, provider="mistral")
    assert e.value.kind == "auth"
    monkeypatch.setenv("MISTRAL_API_KEY", "test-value")
    chat = make_chat_model("mistral", "mistral-large-4", LLMSettings())
    assert isinstance(chat, OpenAIChat) and str(chat.client.base_url).startswith("https://api.mistral.ai/v1")


@pytest.mark.parametrize("model, effort, expected", [
    ("mistral-large-4", "low", "low"),
    ("mistral-large-4", "medium", "medium"),
    ("mistral-large-4", "high", "high"),
    ("mistral-large-4", "xhigh", "high"),
    ("mistral-large-4", "max", "high"),
    ("mistral-small-2603", "low", "none"),
    ("mistral-small-2603", "medium", "none"),
    ("mistral-small-2603", "high", "high"),
    ("mistral-small-2603", "max", "high"),
])
def test_effort_uses_the_levels_of_the_model(model, effort, expected):
    assert _mistral_effort(model, effort) == expected


def test_request_parameters():
    params = GenParams(max_tokens=100, effort="max", json_mode=True, json_schema={"type": "object"})
    kw = scripted()._kwargs("Auftrag", [ChatMessage("user", "Frage")], params)
    assert "stream_options" not in kw and kw["max_tokens"] == 100
    assert kw["reasoning_effort"] == "high"
    assert kw["messages"][0] == {"role": "system", "content": "Auftrag"}
    assert kw["response_format"]["type"] == "json_schema"
    assert "reasoning_effort" not in scripted()._kwargs("", [ChatMessage("user", "Frage")], GenParams())


def test_delta_text_keeps_only_the_answer():
    assert _delta_text("Text") == "Text"
    assert _delta_text(None) == ""
    assert _delta_text([THINKING]) == ""
    assert _delta_text([THINKING, {"type": "text", "text": "Antwort"}]) == "Antwort"
    assert _delta_text([NS(type="thinking", thinking=[]), NS(type="text", text="Antwort")]) == "Antwort"


def test_stream_with_thinking_yields_only_the_answer():
    usage = NS(prompt_tokens=10, completion_tokens=5)
    chat = scripted([chunk([THINKING]), chunk([THINKING, {"type": "text", "text": "Erste"}]), chunk(" Antwort"), chunk("", "stop", usage)])
    c = chat.complete("", [ChatMessage("user", "Frage")], GenParams())
    assert c.text == "Erste Antwort" and c.stop_reason == "stop"
    assert (c.usage.input_tokens, c.usage.output_tokens) == (10, 5)


def test_rejected_parameter_is_dropped_and_not_sent_again():
    chat = scripted(rejected("reasoning_effort"), [chunk("Antwort", "stop")], [chunk("Noch eine", "stop")], model="mistral-large-3")
    params = GenParams(effort="high")
    assert chat.complete("", [ChatMessage("user", "Frage")], params).text == "Antwort"
    assert chat.complete("", [ChatMessage("user", "Frage")], params).text == "Noch eine"
    calls = chat.client.chat.completions.calls
    assert "reasoning_effort" in calls[0] and "reasoning_effort" not in calls[1] and "reasoning_effort" not in calls[2]


def sse(*chunks):
    lines = []
    for c in chunks:
        body = {"id": "1", "object": "chat.completion.chunk", "created": 1, "model": "mistral-large-4",
                "choices": [{"index": 0, "delta": {"role": "assistant", "content": c["content"]}, "finish_reason": c.get("finish")}]}
        if "usage" in c:
            body["usage"] = c["usage"]
        lines.append(f"data: {json.dumps(body)}\n\n")
    return ("".join(lines) + "data: [DONE]\n\n").encode()


def with_transport(handler):
    """Das echte OpenAI-Paket, nur ohne Netz: So zählt auch, wie es die Antworten von Mistral einliest."""
    chat = OpenAIChat("mistral-large-4", "k", provider="mistral", base_url="https://api.mistral.ai/v1")
    chat.client = openai.OpenAI(api_key="k", base_url="https://api.mistral.ai/v1", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    return chat


def test_real_client_reads_thinking_chunks():
    seen = {}

    def handler(request):
        seen["url"], seen["body"] = str(request.url), json.loads(request.content)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse(
            {"content": [THINKING]},
            {"content": [THINKING, {"type": "text", "text": "Erste"}]},
            {"content": " Antwort"},
            {"content": "", "finish": "stop", "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}))

    c = with_transport(handler).complete("Auftrag", [ChatMessage("user", "Frage")], GenParams(effort="medium"))
    assert c.text == "Erste Antwort" and c.stop_reason == "stop"
    assert (c.usage.input_tokens, c.usage.output_tokens) == (10, 5)
    assert seen["url"] == "https://api.mistral.ai/v1/chat/completions"
    assert seen["body"]["reasoning_effort"] == "medium" and "stream_options" not in seen["body"]


def test_real_client_drops_a_parameter_after_422():
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        if len(bodies) == 1:
            return httpx.Response(422, json={"object": "error", "type": "invalid_request_error", "code": "extra_forbidden",
                                             "detail": [{"loc": ["body", "reasoning_effort"], "msg": "Extra inputs are not permitted"}]})
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse({"content": "Antwort", "finish": "stop"}))

    c = with_transport(handler).complete("", [ChatMessage("user", "Frage")], GenParams(effort="high"))
    assert c.text == "Antwort"
    assert "reasoning_effort" in bodies[0] and "reasoning_effort" not in bodies[1]


def test_other_rejection_is_reported():
    chat = scripted(rejected("etwas anderes"))
    with pytest.raises(LLMError, match="ungültige Anfrage"):
        chat.complete("", [ChatMessage("user", "Frage")], GenParams())


def test_stop_at_the_model_limit_counts_as_truncated():
    assert truncation("model_length") == "length"
