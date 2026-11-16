"""Suchplan für „Fragen“: ganze Texte oder Textstellen – und mit welchen Werten gesucht wird.

Umfang (Schalter unter Fragen): „auto“ nimmt ganze Texte, wenn die Texte im Suchbereich zusammen unter der Grenze des
Sprachmodells bleiben (Einstellungen → Experten), sonst Textstellen; „passages“ immer Textstellen; „whole“ ganze Texte,
sofern sie passen.

Die Werte für Textstellen kommen aus dem Preset bzw. den Experten-Einstellungen; Suchoptionen unter Fragen gelten nur für
die Sitzung und gehen vor (die Oberfläche verwirft sie, sobald sich die Experten-Werte oder das Preset ändern).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from zotivity.engine import Engine
from zotivity.extract.chunker import CHARS_PER_TOKEN
from zotivity.index.store import SearchFilter
from zotivity.settings import FULLTEXT_LIMITS, RetrievalSettings, Settings

SCOPE_MODES = ("auto", "passages", "whole")
# Ganze Texte dürfen etwas über der Schätzung liegen, bevor auf Textstellen ausgewichen wird
WHOLE_TOLERANCE = 1.15


def fulltext_limit(settings: Settings, provider: str) -> int:
    """Grenze für ganze Texte (Tokens) beim Anbieter des Hauptmodells; unbekannte Anbieter wie ein kompatibler Server."""
    limits = settings.retrieval.fulltext_limits or {}
    if provider in limits:
        return int(limits[provider])
    return FULLTEXT_LIMITS.get(provider, FULLTEXT_LIMITS["openai_compatible"])


def retrieval_cfg(settings: Settings, overrides: dict | None) -> RetrievalSettings:
    """Experten-Werte (bzw. Preset) mit den Sitzungswerten der Suchoptionen darüber."""
    base = settings.retrieval.model_dump()
    base.update({k: v for k, v in (overrides or {}).items() if k in base and v is not None})
    return RetrievalSettings.model_validate(base)


@dataclass
class ScopeDoc:
    lib: str
    item_key: str
    tokens: int


def estimate_text_tokens(chars: int, chunks: int, overlap_tokens: int) -> int:
    """Länge eines ganzen Texts aus der Summe seiner Abschnitte – ohne die Überlappung zwischen den Abschnitten."""
    raw = chars / CHARS_PER_TOKEN
    return int(max(raw * 0.5, raw - max(0, chunks - 1) * overlap_tokens))


def scope_docs(engine: Engine, f: SearchFilter) -> list[ScopeDoc]:
    """Gelesene Texte im Suchbereich (Dubletten: nur die gewählte Kopie) mit geschätzter Länge, kürzeste zuerst."""
    allowed = engine.store.allowed_items(f)
    hidden = engine.copy_resolution()
    if allowed is not None and hidden:
        allowed = {hidden.get(k, k) for k in allowed}
    overlap = engine.settings.chunking.overlap_tokens
    docs = [ScopeDoc(lib, key, estimate_text_tokens(chars, n, overlap))
            for (lib, key), (chars, n) in engine.store.text_lengths(allowed).items()
            if allowed is not None or (lib, key) not in hidden]
    return sorted(docs, key=lambda d: (d.tokens, d.lib, d.item_key))


@dataclass
class Plan:
    requested: str  # auto | passages | whole
    mode: str  # whole | passages
    n_docs: int  # gelesene Texte im Suchbereich
    tokens: int  # geschätzte Länge aller dieser Texte
    limit: int  # Grenze für ganze Texte beim Anbieter
    provider: str
    # Warum keine ganzen Texte: "" (möglich), search_only, empty, off (Grenze 0), too_large
    reason: str
    cfg: RetrievalSettings
    docs: list[ScopeDoc] = field(default_factory=list)
    session: dict = field(default_factory=dict)  # Sitzungswerte der Suchoptionen

    @property
    def whole_ok(self) -> bool:
        return not self.reason

    def as_dict(self) -> dict:
        c = self.cfg
        return {
            "requested": self.requested, "mode": self.mode, "n_docs": self.n_docs, "tokens": self.tokens,
            "limit": self.limit, "provider": self.provider, "whole_ok": self.whole_ok, "reason": self.reason,
            "session": self.session,
            "values": {"top_k": c.top_k, "max_chunks_per_doc": c.max_chunks_per_doc, "mmr_lambda": c.mmr_lambda,
                       "neighbor_chunks": c.neighbor_chunks, "context_token_budget": c.context_token_budget,
                       "multi_query": c.multi_query, "research_rounds": c.research_rounds, "rerank": c.rerank},
        }


def make_plan(engine: Engine, f: SearchFilter, requested: str | None, overrides: dict | None, provider: str,
              search_only: bool = False) -> Plan:
    settings = engine.settings
    requested = requested if requested in SCOPE_MODES else "auto"
    docs = scope_docs(engine, f)
    tokens = sum(d.tokens for d in docs)
    limit = fulltext_limit(settings, provider)
    reason = ("search_only" if search_only else "empty" if not docs else "off" if limit <= 0
              else "too_large" if tokens > limit else "")
    mode = "whole" if not reason and requested in ("auto", "whole") else "passages"
    session = {k: v for k, v in (overrides or {}).items() if k in RetrievalSettings.model_fields and v is not None}
    return Plan(requested=requested, mode=mode, n_docs=len(docs), tokens=tokens, limit=limit, provider=provider,
                reason=reason, cfg=retrieval_cfg(settings, session), docs=docs, session=session)
