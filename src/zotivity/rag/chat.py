"""Fragen an die Bibliothek: Mehrfachsuche, Recherche-Runden, Antwort mit Belegen."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass, field

from zotivity.engine import Engine
from zotivity.catalog import PROVIDERS
from zotivity.keystore import get_store
from zotivity.llm import ChatMessage, GenParams, LLMError, UsageMeter, helper_model, main_model, truncation
from zotivity.rag import prompts, wholetext
from zotivity.rag.plan import WHOLE_TOLERANCE, Plan, make_plan, retrieval_cfg
from zotivity.rag.sources import build_sources, format_sources
from zotivity.retrieval import Searcher
from zotivity.retrieval.rerank import CrossEncoderReranker, LLMReranker
from zotivity.settings import RetrievalSettings

log = logging.getLogger(__name__)

HISTORY_MESSAGES = 6
WHOLE_NOT_POSSIBLE = "Ganze Texte sind hier nicht möglich; die Antwort stützt sich auf Textstellen."


@dataclass
class AskOptions:
    filters: dict = field(default_factory=dict)
    # Überschreibungen einzelner Experteneinstellungen für diese Frage
    retrieval: dict = field(default_factory=dict)
    provider: str | None = None
    model: str | None = None
    search_only: bool = False
    project_id: int | None = None
    # Umfang: "auto" (ganze Texte, wenn sie passen), "passages" (Textstellen), "whole" (ganze Texte)
    scope: str = "passages"


def _retrieval_cfg(engine: Engine, overrides: dict) -> RetrievalSettings:
    return retrieval_cfg(engine.settings, overrides)


def _history_messages(history: list[dict]) -> list[ChatMessage]:
    """Verlauf für das Modell; Antworten aus ganzen Texten in der Fassung des Modells (Belege [1, S. 64])."""
    messages: list[ChatMessage] = []
    for m in history[-HISTORY_MESSAGES:]:
        content = (m.get("meta") or {}).get("raw") or m["content"]
        if content:
            messages.append(ChatMessage(m["role"], content))
    if messages and messages[0].role != "user":
        messages = messages[1:]
    return messages


def missing_key_message(provider: str) -> str | None:
    """Verständliche Meldung, wenn für den eingestellten Anbieter kein Schlüssel vorhanden ist."""
    info = PROVIDERS.get(provider)
    if info is None or not info.needs_key or get_store().get(provider):
        return None
    others = [p.label for p in PROVIDERS.values() if p.needs_key and p.chat and get_store().get(p.id)]
    hint = (f" Gespeichert ist ein Schlüssel für {', '.join(others)} – unter Einstellungen → Sprachmodelle "
            f"diesen Anbieter wählen." if others else " Unter Einstellungen → Sprachmodelle einen Schlüssel eintragen.")
    return f"Für Antworten ist {info.label} eingestellt, aber dafür ist kein API-Schlüssel gespeichert.{hint}"


def generate_queries(model, question: str, history: list[dict], n: int, effort: str) -> list[str]:
    hist = ""
    if history:
        lines = [f"{m['role']}: {m['content'][:500]}" for m in history[-4:]]
        hist = "Bisheriger Gesprächsverlauf:\n" + "\n".join(lines) + "\n\n"
    data = model.complete_json(
        prompts.QUERY_SYSTEM,
        prompts.QUERY_PROMPT.format(history=hist, question=question, n=n),
        GenParams(max_tokens=3000, effort=effort),
    )
    queries = [q.strip() for q in (data or {}).get("queries", []) if isinstance(q, str) and q.strip()]
    return queries[:n]


class ChatService:
    def __init__(self, engine: Engine):
        self.engine = engine
        self.searcher = Searcher(engine)

    def _reranker(self, cfg: RetrievalSettings, meter: UsageMeter):
        if cfg.rerank == "llm":
            llm_cfg = self.engine.settings.llm
            return LLMReranker(helper_model(llm_cfg, meter), effort=llm_cfg.helper_effort)
        if cfg.rerank == "cross_encoder":
            return CrossEncoderReranker(cfg.rerank_model)
        return None

    def ask(self, question: str, chat_id: int | None = None, options: AskOptions | None = None) -> Iterator[dict]:
        """Erzeugt Ereignisse für die Oberfläche (Server-Sent Events)."""
        options = options or AskOptions()
        settings = self.engine.settings
        llm_cfg = settings.llm.model_copy()
        if options.provider and options.model:
            llm_cfg.provider, llm_cfg.model = options.provider, options.model
        meter = UsageMeter()
        store = self.engine.store
        f = self.engine.make_filter(options.filters)
        plan = make_plan(self.engine, f, options.scope, options.retrieval, llm_cfg.provider, options.search_only)
        cfg = plan.cfg

        if chat_id is None:
            chat_id = store.create_chat(question[:200], project_id=options.project_id)
            yield {"type": "chat", "chat_id": chat_id}
        history = [m for m in store.chat_messages(chat_id) if m["role"] in ("user", "assistant")]
        store.add_chat_message(chat_id, "user", question, meta={"filters": options.filters, "scope": plan.requested})
        yield {"type": "plan", "plan": plan.as_dict()}

        # Fehlt der Schlüssel für das eingestellte Modell, früh und verständlich melden (Suche läuft trotzdem)
        setup_problem = None
        if not options.search_only:
            setup_problem = missing_key_message(llm_cfg.provider)

        if plan.requested == "whole" and plan.mode != "whole" and plan.reason in ("too_large", "off", "empty"):
            yield {"type": "warning", "text": WHOLE_NOT_POSSIBLE}
        if plan.mode == "whole":
            done = yield from self._ask_whole(question, chat_id, history, plan, llm_cfg, meter, setup_problem)
            if done:
                return

        # 1. Suchanfragen
        queries = [question]
        helper = None
        try:
            helper_ok = missing_key_message(llm_cfg.helper_provider or llm_cfg.provider) is None
            if not options.search_only and helper_ok and (cfg.multi_query or history or cfg.research_rounds):
                helper = helper_model(llm_cfg, meter)
            if helper is not None and (cfg.multi_query or history):
                yield {"type": "status", "text": "Formuliere Suchanfragen …"}
                generated = generate_queries(helper, question, history, cfg.n_queries if cfg.multi_query else 1,
                                             llm_cfg.helper_effort)
                queries = ([question] if not history else []) + generated or [question]
                yield {"type": "queries", "queries": queries}
        except LLMError as e:
            yield {"type": "warning", "text": f"Mehrfachsuche nicht möglich ({e}); suche nur mit der Frage."}

        # 2. Suche (+ Recherche-Runden)
        yield {"type": "status", "text": "Durchsuche die Bibliothek …"}
        reranker = self._reranker(cfg, meter) if not options.search_only else None
        try:
            hits = self.searcher.search(queries, f, cfg, reranker=reranker, rerank_query=question)
        except Exception as e:  # noqa: BLE001
            yield {"type": "error", "message": f"Suche fehlgeschlagen: {e}"}
            return

        for round_no in range(cfg.research_rounds if helper else 0):
            yield {"type": "status", "text": f"Recherche-Runde {round_no + 1}: prüfe Lücken …"}
            found = "\n".join(
                f"- {h.item.get('creator_summary', '')} {h.item.get('year') or ''}: {h.chunk['text'][:220]}"
                for h in hits[:20]
            )
            try:
                data = helper.complete_json(
                    prompts.QUERY_SYSTEM,
                    prompts.RESEARCH_PROMPT.format(question=question, queries="; ".join(queries), found=found,
                                                   n=max(2, cfg.n_queries // 2)),
                    GenParams(max_tokens=3000, effort=llm_cfg.helper_effort),
                )
            except LLMError as e:
                yield {"type": "warning", "text": f"Recherche-Runde abgebrochen: {e}"}
                break
            new = [q for q in (data or {}).get("queries", []) if isinstance(q, str) and q.strip()]
            if (data or {}).get("sufficient") or not new:
                break
            queries += new
            yield {"type": "queries", "queries": queries, "missing": (data or {}).get("missing", "")}
            hits = self.searcher.search(queries, f, cfg, reranker=reranker, rerank_query=question)

        sources = build_sources(store, hits, cfg.neighbor_chunks, cfg.context_token_budget)
        source_dicts = [s.as_dict() for s in sources]
        yield {"type": "sources", "sources": source_dicts}
        basis = {"mode": "passages", "passages": len(sources), "docs": len({(s.lib, s.item_key) for s in sources}),
                 "scope_docs": plan.n_docs}

        if options.search_only:
            msg_id = store.add_chat_message(chat_id, "assistant", "", sources=source_dicts,
                                            meta={"search_only": True, "queries": queries, "basis": basis})
            yield {"type": "done", "chat_id": chat_id, "message_id": msg_id, "usage": meter.usage.as_dict(),
                   "basis": basis}
            return
        if not sources:
            text = ("Zu dieser Frage habe ich in den indexierten Texten nichts gefunden. "
                    "Prüfe den gewählten Wissenskorpus und ob seine Texte indexiert sind.")
            store.add_chat_message(chat_id, "assistant", text, sources=[], meta={"queries": queries})
            yield {"type": "delta", "text": text}
            yield {"type": "done", "chat_id": chat_id, "usage": meter.usage.as_dict()}
            return
        if setup_problem:
            store.add_chat_message(chat_id, "assistant", "", sources=source_dicts,
                                   meta={"queries": queries, "error": setup_problem})
            yield {"type": "error", "message": setup_problem, "kind": "setup"}
            return

        # 3. Antwort
        yield {"type": "status", "text": f"Schreibe Antwort mit {llm_cfg.model} …"}
        system = prompts.ANSWER_SYSTEM.format(language=prompts.language_rule(settings.output_language))
        messages = _history_messages(history)
        messages.append(ChatMessage(
            "user",
            f"Textstellen aus der Bibliothek:\n\n{format_sources(sources)}\n\n---\n\nFrage: {question}",
        ))
        params = GenParams(max_tokens=llm_cfg.max_output_tokens, temperature=llm_cfg.temperature, effort=llm_cfg.effort)
        parts: list[str] = []
        served = [llm_cfg.model]
        try:
            yield from self._stream_answer(main_model(llm_cfg, meter), system, messages, params, parts, served)
        except LLMError as e:
            if parts:
                store.add_chat_message(chat_id, "assistant", "".join(parts), sources=source_dicts,
                                       meta={"queries": queries, "error": str(e)})
            yield {"type": "error", "message": str(e), "kind": e.kind}
            return
        answer = "".join(parts)
        msg_id = store.add_chat_message(
            chat_id, "assistant", answer, sources=source_dicts,
            meta={"queries": queries, "model": served[0], "usage": meter.usage.as_dict(), "basis": basis},
        )
        yield {"type": "done", "chat_id": chat_id, "message_id": msg_id, "model": served[0],
               "usage": meter.usage.as_dict(), "basis": basis}

    def _stream_answer(self, model, system: str, messages: list[ChatMessage], params: GenParams, parts: list[str],
                       served: list[str]) -> Iterator[dict]:
        """Antwort des Hauptmodells streamen (Text in parts, Modellname in served[0])."""
        for ev in model.stream(system, messages, params):
            if ev.type == "text":
                parts.append(ev.text)
                yield {"type": "delta", "text": ev.text}
            elif ev.type == "done":
                served[0] = ev.model or served[0]
                cut = truncation(ev.stop_reason)  # Gemini meldet „MAX_TOKENS“ bzw. „RECITATION“
                if cut == "length":
                    yield {"type": "warning", "text": "Antwort wurde wegen der Längenbegrenzung abgeschnitten."}
                elif cut == "recitation":
                    yield {"type": "warning", "text": "Die Antwort brach ab, weil sie eine längere Passage wörtlich "
                           "wiedergab (Sperre des Anbieters). Nochmals fragen, z. B. mit der Bitte um kürzere Zitate."}

    def _ask_whole(self, question: str, chat_id: int, history: list[dict], plan: Plan, llm_cfg, meter: UsageMeter,
                   setup_problem: str | None):
        """Antwort aus ganzen Texten. Rückgabe False: die Texte sind doch zu lang – weiter mit Textstellen."""
        store = self.engine.store
        yield {"type": "status", "text": "Lese die ganzen Texte …"}
        texts = wholetext.load_texts(self.engine, [(d.lib, d.item_key) for d in plan.docs])
        tokens = sum(w.tokens for w in texts)
        if not texts or tokens > plan.limit * WHOLE_TOLERANCE:
            yield {"type": "warning", "text": WHOLE_NOT_POSSIBLE}
            plan.mode = "passages"
            return False
        basis = {"mode": "whole", "texts": [w.as_dict() for w in texts], "tokens": tokens, "scope_docs": plan.n_docs}
        if setup_problem:
            store.add_chat_message(chat_id, "assistant", "", sources=[],
                                   meta={"mode": "whole", "basis": basis, "error": setup_problem})
            yield {"type": "error", "message": setup_problem, "kind": "setup"}
            return True
        yield {"type": "status", "text": f"Schreibe Antwort mit {llm_cfg.model} …"}
        system = prompts.WHOLE_SYSTEM.format(language=prompts.language_rule(self.engine.settings.output_language),
                                             texts=wholetext.format_texts(texts))
        messages = _history_messages(history)
        messages.append(ChatMessage("user", question))
        params = GenParams(max_tokens=llm_cfg.max_output_tokens, temperature=llm_cfg.temperature, effort=llm_cfg.effort)
        parts: list[str] = []
        served = [llm_cfg.model]
        try:
            yield from self._stream_answer(main_model(llm_cfg, meter), system, messages, params, parts, served)
        except LLMError as e:
            if parts:
                raw = "".join(parts)
                text, sources = wholetext.resolve_citations(raw, texts, store)
                store.add_chat_message(chat_id, "assistant", text, sources=wholetext.source_dicts(sources),
                                       meta={"mode": "whole", "raw": raw, "basis": basis, "error": str(e)})
            yield {"type": "error", "message": str(e), "kind": e.kind}
            return True
        raw = "".join(parts)
        text, sources = wholetext.resolve_citations(raw, texts, store)
        dicts = wholetext.source_dicts(sources)
        yield {"type": "sources", "sources": dicts}
        yield {"type": "rewrite", "text": text}
        msg_id = store.add_chat_message(
            chat_id, "assistant", text, sources=dicts,
            meta={"mode": "whole", "raw": raw, "model": served[0], "usage": meter.usage.as_dict(), "basis": basis})
        yield {"type": "done", "chat_id": chat_id, "message_id": msg_id, "model": served[0],
               "usage": meter.usage.as_dict(), "basis": basis}
        return True
