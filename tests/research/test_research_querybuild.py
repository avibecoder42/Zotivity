"""Konzeptblöcke → Suchstrings je Quelle; PubMed → OpenAlex."""

from __future__ import annotations

import pytest
from research_fakes import QUERIES

from zotivity.research.querybuild import build, build_all, parse_pubmed, pubmed_to_openalex, translate

BLOCKS = [
    {"name": "Population", "terms": ["adolescen*", "teen*", '"young people"'], "mesh": ["Adolescent"], "psycinfo": []},
    {"name": "Phänomen", "terms": ["cyber-aggression", "online harassment"], "psycinfo": ["Cyberbullying"]},
    {"name": "leer", "terms": []},
    {"name": "Ausschluss", "kind": "exclude", "field": "ti", "terms": ["cyberbully*"]},
]


def test_pubmed():
    s = build("pubmed", BLOCKS)["string"]
    assert s == ('(adolescen*[tiab] OR teen*[tiab] OR "young people"[tiab] OR "Adolescent"[Mesh]) AND '
                 '("cyber-aggression"[tiab] OR "online harassment"[tiab]) NOT cyberbully*[ti]')


def test_openalex_drops_wildcards_and_mesh():
    r = build("openalex", BLOCKS)
    assert r["string"] == ('(adolescen OR teen OR "young people") AND ("cyber-aggression" OR "online harassment") '
                           'NOT cyberbully')
    assert any("Platzhalter" in n for n in r["notes"]) and any("MeSH" in n for n in r["notes"])


def test_ovid_lines():
    r = build("psycinfo", BLOCKS)
    assert r["lines"] == [
        '(adolescen* or teen* or "young people").ti,ab,id.',
        '(cyber-aggression or "online harassment").ti,ab,id. or exp Cyberbullying/',
        "cyberbully*.ti.",
        "1 and 2 not 3",
    ]


def test_wos():
    assert build("wos", BLOCKS)["string"] == ('TS=(adolescen* OR teen* OR "young people") AND '
                                              'TS=(cyber-aggression OR "online harassment") NOT TI=(cyberbully*)')


def test_empty_and_unknown():
    assert all(not v["string"] for v in build_all([]).values())
    with pytest.raises(ValueError):
        build("scopus", BLOCKS)


def test_pubmed_to_openalex():
    r = pubmed_to_openalex('(coping[tiab] OR "de-escalat*"[tiab] OR help-seeking[tiab]) AND "Internet"[Mesh] '
                           'NOT (cyberbully*[ti] OR cyber-bully*[ti])')
    assert r["string"] == ('(coping OR "de-escalat" OR "help-seeking") AND "Internet" NOT (cyberbully OR "cyber-bully")')
    assert len(r["notes"]) == 3  # MeSH, Titel, Platzhalter


# Von Hand geschriebene PubMed-Strings: Die Übersetzung darf die Suche nicht still ändern (Code-Prüfung 2026-11-11, E11)
def test_translation_warns_about_unknown_field_tags():
    r = translate("teasing[tiab] AND english[la] AND 2015:2024[dp]", "europepmc")
    note = next(n for n in r["notes"] if n.startswith("Feldkürzel"))
    assert "[dp], [la]" in note and "Freitext" in note
    assert any(n.startswith("Feldkürzel [la]") for n in pubmed_to_openalex("teasing[tiab] AND english[la]")["notes"])
    # Bekannte Kürzel, kurz oder ausgeschrieben, geben keinen Hinweis
    for q in ('teasing[tiab] AND "Adolescent"[MeSH Terms]', "teasing[Title/Abstract] OR banter[Title]", "teasing[tw]",
              '"Adolescent"[mesh:noexp]'):
        assert not any("Feldkürzel" in n for n in translate(q, "eric")["notes"]), q
    r = pubmed_to_openalex('"online teasing"[Title/Abstract] AND Adolescent[MeSH Terms]')
    assert r["string"] == '"online teasing" AND Adolescent' and not any("Feldkürzel" in n for n in r["notes"])


def test_field_tag_applies_to_the_whole_phrase():
    # PubMed liest „Internet Addiction Disorder[Mesh]“ als ein Schlagwort – nicht als drei Wörter mit UND
    tree = parse_pubmed("Internet Addiction Disorder[Mesh] AND teen*[tiab]")
    assert tree == ("and", ("term", '"Internet Addiction Disorder"', "mesh"), ("term", "teen*", "tiab"))
    assert translate("Internet Addiction Disorder[Mesh]", "europepmc")["string"] == 'MESH:"Internet Addiction Disorder"'
    assert pubmed_to_openalex("teens AND Internet Addiction Disorder[MeSH Terms]")["string"] == 'teens AND "Internet Addiction Disorder"'
    # ohne Kürzel bleiben Wörter einzeln (wie bisher), auch vor einem Begriff in Anführungszeichen
    assert parse_pubmed('cyber bullying OR "online harassment"[tiab]')[0] == "or"


def test_mixed_chains_keep_the_reading_of_pubmed():
    q = "teasing[tiab] OR banter[tiab] AND adolescen*[tiab]"  # PubMed: (teasing OR banter) AND adolescen*
    r = translate(q, "econbiz")
    assert r["string"] == "(teasing OR banter) AND adolescen*" and any("von links nach rechts" in n for n in r["notes"])
    assert translate("a[ti] OR b[ti] NOT c[ti]", "arxiv")["string"] == "(ti:a OR ti:b) ANDNOT ti:c"
    assert translate(q, "semanticscholar")["string"] == "(teasing | banter) + adolescen*"
    # CQL liest wie PubMed von links: swisscovery braucht keine Klammern (und wertet Klammerblöcke heikel aus)
    assert translate(q, "swisscovery")["string"] == ('alma.all_for_ui="teasing" or alma.all_for_ui="banter" and '
                                                    'alma.all_for_ui all "adolescen*"')
    # OpenAlex: der String bleibt, wie er ist – mit Hinweis
    r = pubmed_to_openalex(q)
    assert r["string"] == "teasing OR banter AND adolescen" and any("Klammern setzen" in n for n in r["notes"])
    # Geklammert oder gleichartig: weder Klammern dazu noch Hinweis; „a AND b NOT c“ ist in beiden Lesarten gleich
    for q in ("(a[tiab] OR b[tiab]) AND c[tiab]", "a[tiab] OR b[tiab] OR c[tiab]", "a[tiab] AND b[tiab] NOT c[tiab]"):
        r = translate(q, "econbiz")
        assert r["string"].count("(") == q.count("(") and not r["notes"], q
        assert not any("Klammern" in n for n in pubmed_to_openalex(q)["notes"]), q


def test_unbalanced_parentheses_do_not_drop_the_rest():
    r = translate("teasing[tiab] OR banter[tiab]) AND adolescen*[tiab]", "econbiz")
    assert "adolescen*" in r["string"] and any("Klammern im Suchstring" in n for n in r["notes"])  # früher fiel der Rest weg
    r = pubmed_to_openalex("(teasing[tiab] OR banter[tiab] AND (adolescen*[tiab] OR teen*[tiab]")
    assert r["string"].count("(") == r["string"].count(")") == 2 and any("Klammern im Suchstring" in n for n in r["notes"])
    assert not translate("(teasing[tiab] OR banter[tiab]) AND teen*[tiab]", "eric")["notes"]


@pytest.mark.skipif(not QUERIES.exists(), reason="Suchstrings des Projekts nicht vorhanden")
def test_translates_project_files():
    for f in QUERIES.glob("*.txt"):
        r = pubmed_to_openalex(f.read_text(encoding="utf-8-sig"))
        s = r["string"]
        assert "[" not in s and "*" not in s and "," not in s, f.name
        assert s.count("(") == s.count(")") and s.count('"') % 2 == 0, f.name
