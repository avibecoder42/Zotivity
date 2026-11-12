"""Katalog der Literaturdatenbanken: Disziplin, Zugang und was maschinell erlaubt ist (Stand 29.10.2026,
siehe ``litsearch/Datenbanken_API.md``).

``access``:
- ``live``    offen, die App sucht direkt (ggf. mit kostenlosem Schlüssel)
- ``license`` nur mit Lizenz der Hochschule oder auf Antrag – in der App noch nicht angebunden
- ``export``  keine offene API: Suche in der Datenbank, Export als RIS, Import in der App
- ``never``   keine API, und die Bedingungen verbieten automatische Abfragen
"""

from __future__ import annotations

DISCIPLINES = {
    "multi": "Fächerübergreifend",
    "health": "Medizin, Gesundheit, Pflege",
    "psych": "Psychologie",
    "social": "Sozialwissenschaften, Soziale Arbeit",
    "edu": "Erziehungswissenschaft",
    "econ": "Wirtschaft",
    "tech": "Informatik, Technik, Naturwissenschaften",
    "hum": "Philosophie, Geisteswissenschaften",
    "art": "Kunst, Design, künstlerische Forschung",
    "books": "Bücher (Bibliothekskataloge)",
}
ACCESS = {
    "live": "direkt in der App",
    "license": "Lizenz oder Antrag",
    "export": "RIS-Import",
    "never": "nicht erlaubt",
}


def _s(id, label, disciplines, access, note, terms="", boolean="ja", key=None, live=None, recommended=False,
       languages=()):
    """``languages``: erfasst Literatur in diesen Sprachen (ausser Englisch), die in den englischsprachigen
    Fachdatenbanken meist fehlt (Forschungsberichte, Bücher, Zeitschriften) – wird vorgeschlagen, wenn eine davon zu den
    Sprachen der Literatur gehört."""
    return {"id": id, "label": label, "disciplines": disciplines, "access": access, "note": note, "terms": terms,
            "boolean": boolean, "key": key, "in_app": access == "live" if live is None else live,
            "recommended": recommended, "languages": list(languages)}


CATALOG = [
    _s("openalex", "OpenAlex", ["multi", "social", "edu", "psych", "econ", "hum", "tech", "health", "art"], "live",
       "Sehr breit, viel graue Literatur. Seit Februar 2026 mit (kostenlosem) API-Schlüssel, 1 $ Nutzung pro Tag.",
       "https://help.openalex.org/access/pricing/", key="openalex", recommended=True),
    _s("pubmed", "PubMed / MEDLINE", ["health", "psych"], "live",
       "Mit MeSH; 3 Anfragen/s, mit NCBI-Schlüssel 10/s.", "https://www.ncbi.nlm.nih.gov/books/NBK25497/",
       key="ncbi", recommended=True),
    _s("europepmc", "Europe PMC", ["health", "psych"], "live",
       "PubMed plus Preprints und Volltexte aus PMC; ohne Schlüssel.",
       "https://europepmc.org/RestfulWebService"),
    _s("semanticscholar", "Semantic Scholar", ["multi", "tech", "health"], "live",
       "Bulk-Suche in Titel und Abstract. Ohne Schlüssel oft überlastet; Lizenz von AI2, nicht kommerziell.",
       "https://api.semanticscholar.org/license", key="semanticscholar"),
    _s("eric", "ERIC", ["edu"], "live", "Bildungsforschung (US), mit Thesaurus; ohne Schlüssel.",
       "https://eric.ed.gov/?api"),
    _s("econbiz", "EconBiz", ["econ"], "live",
       "Wirtschaft (ZBW). Liefert keine Abstracts – nach der Suche nachladen. Keine Massenkopien.",
       "https://api.econbiz.de/"),
    _s("arxiv", "arXiv", ["tech"], "live", "Preprints; 1 Anfrage alle 3 Sekunden; keine Platzhalter.",
       "https://info.arxiv.org/help/api/tou.html"),
    _s("osf", "OSF-Preprints (PsyArXiv, SocArXiv, EdArXiv)", ["psych", "social", "edu"], "live",
       "Einfache Suche: alle Stichwörter müssen vorkommen, kein ODER.", "https://help.osf.io/", boolean="einfach"),
    _s("swisscovery", "swisscovery (Schweizer Hochschulbibliotheken)", ["books", "art"], "live",
       "Bücher und Sammelbände, auch Berichte auf Deutsch, Französisch und Italienisch; SRU, höchstens 10'000 Treffer je Suche, keine Massenexporte.",
       "https://slsp.ch/en", boolean="ja (CQL)", languages=("de", "fr", "it")),
    _s("core", "CORE", ["multi"], "live",
       "Open-Access-Volltexte. Phrasen werden nicht zuverlässig erkannt – noch nicht als Suchquelle eingebaut.",
       "https://core.ac.uk/services/api", boolean="eingeschränkt", live=False),
    _s("base", "BASE (Bielefeld)", ["multi"], "license", "Nur nicht kommerziell, Zugang auf Antrag (IP-gebunden).",
       "https://www.base-search.net/about/en/contact.php"),
    _s("openaire", "OpenAIRE Graph", ["multi"], "live", "Offen (60 Anfragen/h, mit Token 7200/h); noch nicht eingebaut.",
       "https://graph.openaire.eu/docs/apis/terms", boolean="einfach", live=False),
    _s("wos", "Web of Science (Starter API)", ["multi"], "license",
       "Gratis nur persönlich (50/Tag); mit Lizenz der Hochschule 5000/Tag. RIS-Import geht immer (in der Datenbank exportieren).",
       "https://developer.clarivate.com/apis/wos-starter"),
    _s("scopus", "Scopus", ["multi"], "license",
       "Nur mit Lizenz der Hochschule; TDM-Rechte für Abonnent*innen im Schengen-Raum. Sonst RIS-Import.",
       "https://dev.elsevier.com/academic_research_scopus.html"),
    _s("lens", "Lens.org", ["multi"], "license", "Token auf Antrag.", "https://docs.api.lens.org/getting-started.html"),
    _s("ieee", "IEEE Xplore", ["tech"], "license", "Schlüssel nach Prüfung durch IEEE.",
       "https://developer.ieee.org/API_Terms_of_Use2"),
    _s("springer", "Springer Nature (Meta API)", ["multi", "tech"], "live",
       "Kostenloser Schlüssel, 500 Anfragen/Tag; noch nicht eingebaut.",
       "https://dev.springernature.com/docs/rate-limit-details/rate-limits", live=False),
    _s("psycinfo", "APA PsycInfo", ["psych", "social", "edu"], "export",
       "Über Ovid oder EBSCO mit Lizenz; RIS-Import (Suchstring für Ovid übersetzt die App).", recommended=True),
    _s("cinahl", "CINAHL", ["health"], "export", "Pflege; EBSCO, nur mit Lizenz; RIS-Import."),
    _s("socindex", "SocINDEX", ["social"], "export", "EBSCO, nur mit Lizenz; RIS-Import."),
    _s("assia", "ASSIA", ["social"], "export", "ProQuest, nur mit Lizenz; RIS-Import."),
    _s("socabs", "Sociological Abstracts / Social Services Abstracts", ["social"], "export",
       "ProQuest, nur mit Lizenz; RIS-Import."),
    _s("fis", "FIS Bildung (Fachportal Pädagogik)", ["edu", "social"], "export",
       "Deutschsprachige Bildungsforschung, auch Sozialpädagogik; keine API gefunden, RIS-Import.", languages=("de",)),
    _s("ssoar", "SSOAR (Social Science Open Access Repository)", ["social", "edu", "psych"], "export",
       "Deutschsprachige Sozialwissenschaften, Open Access (GESIS), auch Forschungsberichte; Suche auf ssoar.info, "
       "Treffer exportieren und als RIS importieren.", "https://www.ssoar.info/", languages=("de",)),
    _s("philpapers", "PhilPapers", ["hum"], "license", "Schlüssel auf Antrag; strenge Bedingungen zur Weitergabe.",
       "https://philpapers.org/help/api/"),
    _s("philindex", "Philosopher's Index, MLA", ["hum"], "export", "EBSCO, nur mit Lizenz; RIS-Import."),
    # Kunst, Design, künstlerische Forschung (Stand 30.10.2026; Schnittstellen geprüft, wo „keine API“ steht: keine gefunden)
    _s("iba", "International Bibliography of Art (IBA)", ["art"], "export",
       "ProQuest, nur mit Lizenz; Nachfolgerin der BHA, westliche Kunst von der Spätantike bis heute; RIS-Import.",
       "https://proquest.libguides.com/iba/content", recommended=True),
    _s("artfulltext", "Art Full Text / Art & Architecture Source", ["art"], "export",
       "EBSCO, nur mit Lizenz; Kunst, Design, Architektur; RIS-Import (EDS-API nur, wenn die Hochschule sie freischaltet)."),
    _s("avery", "Avery Index to Architectural Periodicals", ["art"], "export", "ProQuest, nur mit Lizenz; Architektur; RIS-Import."),
    _s("daai", "DAAI (Design and Applied Arts Index)", ["art"], "export",
       "ProQuest, nur mit Lizenz; Design und angewandte Kunst; RIS-Import."),
    _s("openbibart", "OpenBibArt (BHA und RAA 1910–2007)", ["art"], "export",
       "Frei online (INHA, Getty, CNRS), rund 1,2 Mio. Einträge bis 2007; keine API gefunden – Suche und Export von Hand.",
       "https://www.openbibart.fr/"),
    _s("kubikat", "kubikat (Kunsthistorische Institute Florenz, München, Rom, Paris)", ["art", "books"], "export",
       "Grösster Katalog der Kunstgeschichte, auch Aufsätze; keine Schnittstelle gefunden – Suche und Export von Hand.",
       "https://www.kubikat.org/"),
    _s("artdiscovery", "ART-Discovery (arthistoricum.net)", ["art"], "export",
       "Durchsucht kubikat, weitere Kunstbibliotheken und BASE; keine API erwähnt – Suche und Export von Hand.",
       "https://www.arthistoricum.net/en/search/data-sources"),
    _s("researchcatalogue", "Research Catalogue (Society for Artistic Research)", ["art"], "export",
       "Zentrale Plattform der künstlerischen Forschung (über 5'700 Expositionen, u. a. JAR); keine öffentliche API "
       "dokumentiert – Suche von Hand, Export bei der SAR erfragen.", "https://societyforartisticresearch.org/research-catalogue",
       recommended=True),
    _s("doaj", "DOAJ (Directory of Open Access Journals)", ["multi", "art", "hum"], "live",
       "Open-Access-Zeitschriften aller Fächer, auch Kunst und künstlerische Forschung; Metadaten CC0, offene API; noch "
       "nicht eingebaut.", "https://doaj.org/terms", boolean="ja", live=False),
    _s("artdok", "ART-Dok (UB Heidelberg)", ["art"], "live",
       "Rund 9'600 Open-Access-Volltexte der Kunstgeschichte; nur Gesamtabzug über OAI-PMH, keine Such-API; noch nicht "
       "eingebaut.", "https://archiv.ub.uni-heidelberg.de/artdok/", boolean="nur Gesamtabzug", live=False),
    _s("europeana", "Europeana", ["art", "hum"], "live",
       "Kulturgut-Objekte (Werke, Archivalien), keine Fachliteratur; Gratis-Schlüssel je Software-Projekt, Metadaten CC0; "
       "noch nicht eingebaut.", "https://www.europeana.eu/rights/terms-of-use", key="europeana", live=False),
    _s("jstor", "JSTOR", ["hum", "social", "art"], "never", "Keine Such-API (Constellate seit 1.7.2025 eingestellt)."),
    _s("cochrane", "Cochrane Library", ["health"], "never", "Keine öffentliche Such-API; Reviews sind auch in PubMed."),
    _s("scholar", "Google Scholar", ["multi"], "never", "Keine API; die Nutzungsbedingungen verbieten automatische Abfragen."),
]
BY_ID = {s["id"]: s for s in CATALOG}


def suggest(disciplines: list[str], languages: list[str] | None = None) -> list[str]:
    """Vorschlag für die gewählten Disziplinen: alle offenen, eingebauten Quellen dieser Disziplinen, dazu OpenAlex
    und die Fachdatenbanken, die nur per Export gehen (damit sie im Protokoll nicht fehlen). Gehört eine andere Sprache
    als Englisch zu den Sprachen der Literatur, kommen die Quellen dieser Disziplinen dazu, die Literatur in dieser
    Sprache erfassen, und swisscovery (Bücher und Berichte aller Fächer)."""
    chosen = set(disciplines or [])
    wanted = set(languages or []) - {"en"}
    out = []
    for s in CATALOG:
        if s["access"] == "never":
            continue
        fits = bool(chosen & set(s["disciplines"]))
        usable = s["in_app"] or s["access"] == "export"
        speaks = bool(wanted & set(s["languages"]))
        # Exportquellen für eine bestimmte Sprache (FIS Bildung, SSOAR) nur, wenn diese Sprache gesucht ist
        if s["id"] == "openalex" or (fits and usable and (speaks or not s["languages"] or s["in_app"]))                 or (speaks and usable and "books" in s["disciplines"]):
            out.append(s["id"])
    return out


# ------------------------------------------------------------------------------------- Sprachen der Literatur
# Die Landessprachen der Schweiz und Englisch
LITERATURE_LANGUAGES = {"de": "Deutsch", "fr": "Französisch", "it": "Italienisch", "en": "Englisch"}
# Häufige Wörter, die es nur in einer der Sprachen gibt (gemeinsame wie „in“ zählen nicht)
_WORDS = {
    "de": {"und", "der", "die", "das", "ist", "nicht", "mit", "von", "für", "zu", "den", "im", "sich", "eine", "einer",
           "einen", "eines", "auf", "wie", "werden", "wird", "welche", "welchen", "welcher", "bei", "aus", "oder", "dem",
           "des", "zur", "zum", "über", "durch", "sind", "nach", "unter", "sowie", "haben", "hat"},
    "fr": {"le", "les", "des", "du", "et", "est", "dans", "pour", "avec", "sur", "une", "comment", "quels", "quelles",
           "qui", "que", "aux", "leur", "leurs", "chez", "par", "ou", "sont", "entre", "jeunes"},
    "it": {"il", "lo", "gli", "della", "delle", "degli", "dei", "nel", "nella", "nei", "per", "con", "che", "una",
           "come", "quali", "sono", "tra", "fra", "loro", "giovani", "è", "e", "o"},
    "en": {"the", "and", "of", "to", "is", "for", "with", "on", "are", "how", "what", "by", "from", "or", "among",
           "between", "do", "does", "which", "their", "this", "that"},
}


def question_language(text: str) -> str:
    """Sprache der Fragestellung (de, fr, it, en) nach häufigen Wörtern; im Zweifel Englisch."""
    words = [w.strip(".,;:!?()«»„“\"'").lower() for w in (text or "").split()]
    score = {lang: sum(w in vocab for w in words) for lang, vocab in _WORDS.items()}
    if any(c in (text or "").lower() for c in "äöüß"):
        score["de"] += 1
    best = max(("de", "fr", "it"), key=lambda lang: score[lang])
    return best if score[best] > score["en"] else "en"


def clean_languages(languages) -> list[str]:
    return [code for code in LITERATURE_LANGUAGES if code in (languages or [])]


def literature_languages(project: dict) -> list[str]:
    """Sprachen der gesuchten Literatur: wie im Projekt gewählt; sonst Englisch und, ist die Fragestellung deutsch,
    französisch oder italienisch, auch diese Sprache (solche Studien haben oft keinen englischen Titel oder Abstract)."""
    chosen = clean_languages((project.get("settings") or {}).get("languages"))
    if chosen:
        return chosen
    text = " ".join(str(project.get(k) or "") for k in ("question", "description", "title"))
    return clean_languages({question_language(text), "en"})
