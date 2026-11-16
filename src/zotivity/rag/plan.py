"""Suchplan für „Fragen“: ganze Texte oder Textstellen – und mit welchen Werten gesucht wird.

Umfang (Schalter unter Fragen): „auto“ nimmt ganze Texte, wenn die Texte im Suchbereich zusammen unter der Grenze des
Sprachmodells bleiben (Einstellungen → Experten), sonst Textstellen; „passages“ immer Textstellen; „whole“ ganze Texte,
sofern sie passen.

Die Werte für Textstellen kommen aus drei Ebenen (zuletzt Eingestelltes gilt):
- automatisch (Umfang „auto“): Textstellen, Grenze je Text, Vielfalt (MMR) und Nachbarabschnitte nach der Zahl der Texte
  im Suchbereich (auto_values) – ausser Werten, die unter Experten bewusst vom Preset abweichen;
- Preset und Experten (feste Werte; das Preset bestimmt vor allem die Textmenge, also Budget und Denktiefe);
- Suchoptionen unter Fragen: nur für die Sitzung, gehen vor (die Oberfläche verwirft sie, sobald sich die Experten-Werte
  oder das Preset ändern).
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


# Was „Automatisch“ je nach Zahl der Texte anpasst
AUTO_FIELDS = ("top_k", "max_chunks_per_doc", "mmr_lambda", "neighbor_chunks", "candidate_k")
MANY_DOCS = 200  # darüber: Hinweis zum Eingrenzen


def auto_values(cfg: RetrievalSettings, n_docs: int, chunk_tokens: int) -> tuple[str, dict]:
    """Werte für Textstellen nach der Zahl der Texte im Suchbereich → (Stufe, Werte).

    1–3 Texte: keine Grenze je Text, keine Vielfalt-Strafe (MMR 1), Nachbarabschnitte, so viele Stellen, wie ins
    Kontext-Budget passen. 4–30: mehr Stellen (bis 25), 3–4 je Text. Über 30: 2 je Text, mehr Stellen (bis 40), mehr
    Kandidaten, damit möglichst viele Texte vorkommen."""
    if n_docs <= 0:
        return "", {}

    def fit(neighbors: int) -> int:  # Textstellen, die ins Kontext-Budget passen
        return cfg.context_token_budget // max(100, chunk_tokens * (1 + 2 * neighbors))

    if n_docs <= 3:
        neighbors = max(cfg.neighbor_chunks, 1)
        return "few", {"top_k": min(60, max(cfg.top_k, fit(neighbors))), "max_chunks_per_doc": 50, "mmr_lambda": 1.0,
                       "neighbor_chunks": neighbors}
    if n_docs <= 30:
        return "some", {"top_k": max(cfg.top_k, min(25, fit(cfg.neighbor_chunks))),
                        "max_chunks_per_doc": 4 if n_docs <= 10 else 3}
    return ("many" if n_docs <= MANY_DOCS else "very_many"), {
        "top_k": max(cfg.top_k, min(40, fit(cfg.neighbor_chunks))), "max_chunks_per_doc": 2,
        "candidate_k": max(cfg.candidate_k, 150)}


def expert_fixed(settings: Settings) -> set[str]:
    """Suchwerte, die unter Experten bewusst vom aktiven Preset abweichen – „Automatisch“ lässt sie stehen."""
    from zotivity.presets import all_presets

    preset = all_presets(settings).get(settings.active_preset)
    if not preset:
        return set()
    cur = settings.retrieval
    return {f.split(".", 1)[1] for f, v in preset["values"].items()
            if f.startswith("retrieval.") and getattr(cur, f.split(".", 1)[1], v) != v}


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
    auto: dict = field(default_factory=dict)  # von „Automatisch“ angepasste Werte
    band: str = ""  # few | some | many | very_many
    fixed: list[str] = field(default_factory=list)  # unter Experten fest eingestellt

    @property
    def whole_ok(self) -> bool:
        return not self.reason

    def as_dict(self) -> dict:
        c = self.cfg
        return {
            "requested": self.requested, "mode": self.mode, "n_docs": self.n_docs, "tokens": self.tokens,
            "limit": self.limit, "provider": self.provider, "whole_ok": self.whole_ok, "reason": self.reason,
            "session": self.session, "auto": self.auto, "band": self.band, "fixed": self.fixed,
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
    base = settings.retrieval
    band, auto, fixed = "", {}, []
    if requested == "auto" and base.auto_adjust:
        fixed = sorted(expert_fixed(settings) & set(AUTO_FIELDS))
        band, values = auto_values(base, len(docs), settings.chunking.chunk_tokens)
        auto = {k: v for k, v in values.items() if k not in fixed and k not in session and getattr(base, k) != v}
    cfg = retrieval_cfg(settings, {**auto, **session})
    return Plan(requested=requested, mode=mode, n_docs=len(docs), tokens=tokens, limit=limit, provider=provider,
                reason=reason, cfg=cfg, docs=docs, session=session, auto=auto, band=band, fixed=fixed)
