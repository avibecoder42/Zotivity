"""Lesen in Etappen: Texte über der Ganztext-Grenze kapitelweise lesen, je Etappe Notizen, dann Antwort aus den Notizen.

Die Etappen folgen den Seiten des Texts (Seitenmarken [S. x]) und beginnen nach Möglichkeit bei einer Kapitelüberschrift
(nummerierte Überschrift wie „4.2 Vier Aneignungstypen“, „Kapitel 3“, „Chapter 3“). Jede Etappe passt in das Modell, das
die Notizen schreibt (Hilfs- oder Hauptmodell, Einstellungen → Experten). Langsamer als ganze Texte, aber vollständig.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from zotivity.extract.chunker import estimate_tokens
from zotivity.rag.wholetext import WholeText

STAGE_MAX_TOKENS = 40000  # eine Etappe höchstens so lang (Aufmerksamkeit des Modells, Dauer je Aufruf)
STAGE_MIN_TOKENS = 2000
MAX_STAGES = 60
MAX_TEXTS = 5
NOTHING = "Nichts zur Frage."

_MARK = re.compile(r"\[(?:S\. [^\]\n]{1,20}|o\. S\.)\]")
_HEADING = re.compile(
    r"^\s*(?:(?:\d{1,2}(?:\.\d{1,2}){0,2})\.?\s+[A-ZÄÖÜ][^\n]{2,90}|(?:Kapitel|Chapter|Teil|Part)\s+[\dIVX]+\b[^\n]{0,90})\s*$",
    re.MULTILINE)


def stage_size(limit: int) -> int:
    """Länge einer Etappe aus der Ganztext-Grenze des Modells, das die Notizen schreibt (mit Platz für Frage und Notizen)."""
    return max(STAGE_MIN_TOKENS, min(STAGE_MAX_TOKENS, int(limit * 0.8)))


def estimate_stages(tokens: int, n_docs: int, size: int) -> int:
    return max(n_docs, math.ceil(tokens / max(1, size)) + max(0, n_docs - 1))


@dataclass
class Segment:
    marker: str  # „[S. 12]“ oder „“ (Text vor der ersten Seitenmarke, z. B. Abstract)
    text: str
    heading: str = ""


@dataclass
class Stage:
    text_n: int
    index: int  # 1-basiert über alle Texte
    total: int
    first: str  # erste Seitenmarke ohne Klammern, z. B. „S. 40“
    last: str
    text: str
    tokens: int
    headings: list[str] = field(default_factory=list)

    @property
    def pages(self) -> str:
        if not self.first:
            return ""
        if not self.last or self.last == self.first:
            return self.first
        return f"{self.first}–{self.last.removeprefix('S. ')}" if self.first.startswith("S. ") else f"{self.first}–{self.last}"


def segments(text: str) -> list[Segment]:
    """Text mit Seitenmarken → Abschnitte je Seite; eine Überschrift am Seitenanfang markiert einen Kapitelbeginn."""
    out: list[Segment] = []
    pos, marker = 0, ""
    for m in _MARK.finditer(text):
        if m.start() > pos or marker:
            out.append(Segment(marker, text[pos:m.start()]))
        marker, pos = m.group(0), m.end()
    out.append(Segment(marker, text[pos:]))
    for seg in out:
        h = _HEADING.search(seg.text[:400])
        if h and seg.marker:
            seg.heading = " ".join(h.group(0).split())
    return [s for s in out if s.text.strip() or s.marker]


def split_stages(texts: list[WholeText], size: int) -> list[Stage]:
    """Etappen je Text: Seiten sammeln bis zur Etappenlänge; ab halber Länge beginnt ein neues Kapitel eine neue Etappe."""
    stages: list[Stage] = []
    for w in texts:
        cur: list[Segment] = []
        cur_tokens = 0

        def close():
            nonlocal cur, cur_tokens
            if not cur:
                return
            marks = [s.marker.strip("[]") for s in cur if s.marker]
            stages.append(Stage(text_n=w.n, index=0, total=0, first=marks[0] if marks else "",
                                last=marks[-1] if marks else "", text="".join(s.marker + s.text for s in cur).strip(),
                                tokens=cur_tokens, headings=[s.heading for s in cur if s.heading]))
            cur, cur_tokens = [], 0

        for seg in segments(w.text):
            t = estimate_tokens(seg.marker + seg.text)
            if cur and (cur_tokens + t > size or (seg.heading and cur_tokens >= size / 2)):
                close()
            cur.append(seg)
            cur_tokens += t
        close()
    for i, s in enumerate(stages, start=1):
        s.index, s.total = i, len(stages)
    return stages


def has_notes(notes: str) -> bool:
    return bool(notes.strip()) and NOTHING.rstrip(".").lower() not in notes.strip().lower()[:40]


def format_notes(texts: list[WholeText], stages: list[Stage], notes: dict[int, str]) -> str:
    by_n = {w.n: w for w in texts}
    out = []
    for s in stages:
        if not has_notes(notes.get(s.index, "")):
            continue
        w = by_n[s.text_n]
        head = f"=== Notizen zu [{w.n}] {w.citation}, Etappe {s.index} von {s.total}" + (f" ({s.pages})" if s.pages else "")
        out.append(f"{head} ===\n{notes[s.index].strip()}")
    return "\n\n".join(out)
