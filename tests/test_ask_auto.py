"""Umfang „Automatisch“: Suchwerte nach der Zahl der Texte im Suchbereich (Stufe 2 des Suchplans)."""

import pytest
from test_indexing_search import build_library, run_index

from zotivity.rag.plan import auto_values, expert_fixed, make_plan
from zotivity.settings import RetrievalSettings, Settings


@pytest.fixture
def indexed(zengine, fake_zotero):
    build_library(fake_zotero)
    run_index(zengine)
    return zengine


def test_auto_values_by_number_of_texts():
    cfg = RetrievalSettings()  # Ausgewogen: 15 Stellen, 3 je Text, MMR 0.75, Budget 16 000
    band, few = auto_values(cfg, 1, 400)
    assert band == "few" and few["max_chunks_per_doc"] == 50 and few["mmr_lambda"] == 1.0 and few["neighbor_chunks"] == 1
    assert few["top_k"] == 15  # Nachbarabschnitte verdreifachen jede Stelle: mehr passen nicht ins Budget
    band, some = auto_values(cfg, 8, 400)
    assert band == "some" and some == {"top_k": 25, "max_chunks_per_doc": 4}
    assert auto_values(cfg, 25, 400)[1]["max_chunks_per_doc"] == 3
    band, many = auto_values(cfg, 120, 400)
    assert band == "many" and many == {"top_k": 40, "max_chunks_per_doc": 2, "candidate_k": 150}
    assert auto_values(cfg, 500, 400)[0] == "very_many"
    assert auto_values(cfg, 0, 400) == ("", {})
    # Kleines Budget (Schnell): nicht mehr Stellen, als hineinpassen
    small = RetrievalSettings(top_k=8, context_token_budget=8000)
    assert auto_values(small, 8, 400)[1]["top_k"] == 20


def test_auto_plan_respects_expert_and_session_values(indexed):
    f = indexed.make_filter({})
    indexed.settings_store.update({"retrieval": {"fulltext_limits": {"ollama": 100}}})
    plan = make_plan(indexed, f, "auto", {}, "ollama")  # die 2 Texte passen nicht ganz → Textstellen
    assert plan.mode == "passages" and plan.band == "few"
    assert plan.cfg.max_chunks_per_doc == 50 and plan.cfg.mmr_lambda == 1.0 and plan.cfg.neighbor_chunks == 1
    assert plan.as_dict()["auto"]["max_chunks_per_doc"] == 50
    # „Textstellen“: feste Werte aus Preset und Experten
    fixed = make_plan(indexed, f, "passages", {}, "ollama")
    assert fixed.auto == {} and fixed.cfg.max_chunks_per_doc == 3
    # Unter Experten bewusst gesetzt (weicht vom Preset ab): bleibt
    indexed.settings_store.update({"retrieval": {"max_chunks_per_doc": 5}})
    assert expert_fixed(indexed.settings) == {"max_chunks_per_doc"}
    plan = make_plan(indexed, f, "auto", {}, "ollama")
    assert plan.cfg.max_chunks_per_doc == 5 and "max_chunks_per_doc" in plan.fixed and plan.cfg.mmr_lambda == 1.0
    # Sitzungswerte gehen vor
    plan = make_plan(indexed, f, "auto", {"mmr_lambda": 0.5}, "ollama")
    assert plan.cfg.mmr_lambda == 0.5 and "mmr_lambda" not in plan.auto
    # Abschaltbar: feste Werte
    indexed.settings_store.update({"retrieval": {"auto_adjust": False}})
    assert make_plan(indexed, f, "auto", {}, "ollama").auto == {}


def test_expert_fixed_with_unknown_preset():
    s = Settings(active_preset="gibt-es-nicht")
    assert expert_fixed(s) == set()
