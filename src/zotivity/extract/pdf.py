"""Textextraktion mit Seitenzahlen (PDF, EPUB, HTML).

PDF liest PDFium (über pypdfium2, BSD/Apache). PDFium liefert Zeichen mit Position; daraus entstehen hier Zeilen und
Textblöcke (Absätze, Kopf- und Fusszeilen) – dieselbe Form, aus der die Seitenzahl-Erkennung (``pagenumbers.py``) und
das Entfernen wiederkehrender Kopfzeilen arbeiten. Gerechnet wird nur mit den Rahmen der Zeichen; das Zerlegen der PDF
erledigt PDFium (C++), darum bleibt das Einlesen schnell.

Texterkennung (OCR) für Scans: Seite als Bild rendern und ``tesseract.exe`` aufrufen (TSV mit Blöcken und Positionen).
EPUB: die XHTML-Kapitel der Reihe nach (``html_to_text``).
"""

from __future__ import annotations

import ctypes
import logging
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import unicodedata
import zipfile
import zlib
from collections import Counter
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import unquote
from xml.etree import ElementTree

from zotivity.extract.pagenumbers import clean_pdf_label, detect_page_labels, margin_candidates

log = logging.getLogger(__name__)

# PDFium ist nicht threadsicher: alle Aufrufe in diesem Prozess nacheinander
_PDFIUM_LOCK = threading.RLock()


@dataclass
class PageText:
    index: int  # 0-basiert (physische Seite)
    label: str  # gedruckte Seitenzahl (Kopf-/Fusszeile), "" = ohne Seitenzahl („o. S.“)
    text: str  # Absätze durch Leerzeile getrennt
    has_images: bool = False


@dataclass
class FigureCandidate:
    """Mögliche Abbildung auf einer Seite (Bild oder Zeichnung), mit Bildunterschrift, falls gefunden."""
    page: int  # 0-basiert
    bbox: tuple[float, float, float, float]  # x0, y0, x1, y1 in Punkt, y von oben
    kind: str  # "image" (eingebettetes Bild) oder "drawing" (Vektorgrafik)
    caption: str = ""
    text: str = ""  # Text innerhalb der Abbildung (Achsen, Legende) – nur bei Zeichnungen lesbar


@dataclass
class ExtractedDoc:
    pages: list[PageText] = field(default_factory=list)
    n_pages: int = 0
    method: str = "pdfium"
    needs_ocr: bool = False
    warnings: list[str] = field(default_factory=list)
    label_source: str = ""  # woher die Seitenzahlen stammen (kopfzeile, pdf, zotero) – "" = keine erkannt
    figures: list[FigureCandidate] = field(default_factory=list)

    @property
    def char_count(self) -> int:
        return sum(len(p.text) for p in self.pages)


_WS = re.compile(r"[ \t   ]+")
_SOFT_HYPHEN = "­"
_DIGITS = re.compile(r"\d+")
_LEADERS = re.compile(r"(?:\s?[.·…_]){4,}\s?")  # Punktlinien in Inhaltsverzeichnissen


def _clean_block(text: str) -> str:
    text = text.replace(_SOFT_HYPHEN, "")
    # Zeilenumbrüche innerhalb eines Blocks sind Satzspiegel, keine Absätze
    lines = [ln.strip() for ln in text.splitlines()]
    out = ""
    for ln in lines:
        if not ln:
            continue
        if out.endswith("-") and ln[:1].islower() and not out.endswith(" -"):
            out = out[:-1] + ln  # Silbentrennung am Zeilenende
        else:
            out = f"{out} {ln}" if out else ln
    out = _LEADERS.sub(" … ", out)
    return _WS.sub(" ", out).strip()


def _norm_line(line: str) -> str:
    return _DIGITS.sub("#", line.strip().lower())


def _strip_running_heads(pages: list[list[str]]) -> list[list[str]]:
    """Entfernt wiederkehrende Kopf-/Fusszeilen (Zeitschriftentitel, Seitenzahlen)."""
    if len(pages) < 4:
        return pages
    counter: Counter[str] = Counter()
    for blocks in pages:
        edge = blocks[:2] + blocks[-2:]
        counter.update({_norm_line(b) for b in edge if len(b) < 160})
    threshold = max(3, int(len(pages) * 0.4))
    repeated = {k for k, v in counter.items() if v >= threshold}
    repeated.add("#")  # reine Seitenzahlen
    out = []
    for blocks in pages:
        keep = list(blocks)
        for pos in (0, 1, -1, -2):
            if not keep:
                break
            idx = pos if pos >= 0 else len(keep) + pos
            if 0 <= idx < len(keep) and _norm_line(keep[idx]) in repeated:
                keep[idx] = ""
        out.append([b for b in keep if b])
    return out


def substantive_chars(text: str) -> int:
    """Zeichen ohne Zeilen, die sich (bis auf Zahlen) mindestens dreimal wiederholen – Stempel, Kopf- und Fusszeilen.
    So zählt ein Volltext, der nur aus Lieferstempeln besteht, nicht als Text."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    counts = Counter(_norm_line(ln) for ln in lines)
    return sum(len(ln) for ln in lines if counts[_norm_line(ln)] < 3)


# ------------------------------------------------------------------------------------------------ PDFium: Zeichen
@dataclass
class _Line:
    chars: list[str]
    x0: float
    y0: float  # oben (von oben gemessen)
    x1: float
    y1: float  # unten
    size: float

    @property
    def height(self) -> float:
        return max(self.y1 - self.y0, 0.1)

    @property
    def text(self) -> str:
        return _fix_text("".join(self.chars)).strip()


_SPACES = str.maketrans({"\t": " ", "\xa0": " ", "\x00": "", "￾": "", "￿": ""})
_SURROGATE = re.compile("[\ud800-\udfff]")
_MATH_LETTERS = re.compile("[\U0001d400-\U0001d7ffℎ]")


def _fix_text(s: str) -> str:
    """Zeichen ausserhalb der Grundebene von Unicode (𝑁, 𝑥 in Formeln, Emojis) liefert PDFium als zwei UTF-16-Hälften
    (Surrogatpaar). Paare zusammensetzen, einzelne Hälften weglassen – sonst lässt sich der Text nicht als UTF-8
    speichern. Mathematische Buchstaben und Ziffern (𝑁, 𝒙, ℎ, 𝟏) als gewöhnliche, damit die Suche „N = 120“ findet."""
    if _SURROGATE.search(s):
        s = s.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "ignore")
    if _MATH_LETTERS.search(s):
        s = _MATH_LETTERS.sub(lambda m: unicodedata.normalize("NFKC", m.group()), s)
    return s


def _page_lines(textpage, top: float, clip: tuple[float, float, float, float]) -> list[_Line]:
    """Zeilen einer Seite in der Reihenfolge des Inhalts. PDFium trennt Zeilen mit eingefügtem \\r\\n. Der Text kommt in
    einem Aufruf; Rahmen und Schriftgrösse nur je Zeile (erstes und letztes Zeichen, „lose“ Rahmen nach Schriftgrösse,
    damit Zeilen mit und ohne Unterlängen gleich hoch sind). top: Oberkante der sichtbaren Seite, clip: Mediabox –
    Zeilen ganz ausserhalb fallen weg. Zeichen ausserhalb der Grundebene von Unicode (𝑁 in Formeln) zählt PDFium als
    zwei Zeichen (UTF-16-Hälften): dann den Text Codeeinheit für Codeeinheit übernehmen, damit die Stellen zu den
    Zeichennummern passen; _fix_text setzt die Hälften je Zeile wieder zusammen. Passt auch das nicht, liest die
    langsamere Fassung Zeichen für Zeichen."""
    import pypdfium2.raw as raw

    tp = textpage.raw
    n = raw.FPDFText_CountChars(tp)
    if n <= 0:
        return []
    text = textpage.get_text_range(0, n)
    if len(text) != n:
        buf = (ctypes.c_ushort * (n + 1))()
        if raw.FPDFText_GetText(tp, 0, n, buf) - 1 != n:
            return _page_lines_by_char(textpage, top, clip)
        text = "".join(map(chr, buf[:n]))
    left, bottom, right, ctop = clip
    rect = raw.FS_RECTF()
    ref = ctypes.byref(rect)
    get_box = raw.FPDFText_GetLooseCharBox
    lines: list[_Line] = []
    for m in re.finditer(r"[^\r\n]+", text):
        seg = m.group()
        stripped = seg.strip()
        if not stripped:
            continue
        i0 = m.start() + (len(seg) - len(seg.lstrip()))
        i1 = m.start() + len(seg.rstrip()) - 1
        if not get_box(tp, i0, ref):
            continue
        x0, t0, b0 = rect.left, rect.top, rect.bottom
        if i1 != i0 and get_box(tp, i1, ref):
            x1, t1, b1 = rect.right, rect.top, rect.bottom
        else:
            x1, t1, b1 = rect.right, t0, b0
        lt, lb = max(t0, t1), min(b0, b1)
        if x1 < left or x0 > right or lt < bottom or lb > ctop:
            continue  # ganz ausserhalb der Mediabox (wie bisher „Mediabox-Clip“)
        line_text = _fix_text(stripped.translate(_SPACES))
        if line_text.strip():
            lines.append(_Line([line_text], min(x0, x1), top - lt, max(x0, x1), top - lb,
                               raw.FPDFText_GetFontSize(tp, i0)))
    return lines


def _page_lines_by_char(textpage, top: float, clip: tuple[float, float, float, float]) -> list[_Line]:
    """Wie _page_lines, aber Zeichen für Zeichen (für Seiten, deren Text sich nicht 1:1 den Zeichen zuordnen lässt)."""
    import pypdfium2.raw as raw

    tp = textpage.raw
    left, bottom, right, ctop = clip
    get_unicode, get_box = raw.FPDFText_GetUnicode, raw.FPDFText_GetLooseCharBox
    n = raw.FPDFText_CountChars(tp)
    rect = raw.FS_RECTF()
    ref = ctypes.byref(rect)
    lines: list[_Line] = []
    cur: _Line | None = None
    for i in range(n):
        u = get_unicode(tp, i)
        if u == 10 or u == 13:
            if cur is not None:
                lines.append(cur)
                cur = None
            continue
        if u == 32 or u == 9 or u == 0xA0:  # Leerzeichen (auch von PDFium eingefügte zwischen Wörtern)
            if cur is not None and cur.chars[-1] != " ":
                cur.chars.append(" ")
            continue
        if 0xDC00 <= u < 0xE000:  # zweite Hälfte eines Surrogatpaars: gehört zum vorigen Zeichen
            if cur is not None and "\ud800" <= cur.chars[-1] < "\udc00":
                cur.chars.append(chr(u))
            continue
        if u == 0 or u >= 0xFFFE or not get_box(tp, i, ref):
            continue
        rl, rr, rt, rb = rect.left, rect.right, rect.top, rect.bottom
        if rr < left or rl > right or rt < bottom or rb > ctop:
            continue  # ausserhalb der Mediabox (wie bisher „Mediabox-Clip“)
        ch = chr(u)
        if ch.isspace():
            if cur is not None and cur.chars[-1] != " ":
                cur.chars.append(" ")
            continue
        y0, y1 = top - rt, top - rb  # von oben gemessen
        if cur is not None:
            h = cur.y1 - cur.y0
            if y0 > cur.y1 + 0.5 * h or y1 < cur.y0 - 0.5 * h:
                lines.append(cur)  # Sprung ohne Zeilenwechsel (z. B. Spalte, Fussnote)
                cur = None
        if cur is None:
            cur = _Line([ch], rl, y0, rr, y1, raw.FPDFText_GetFontSize(tp, i))
        else:
            cur.chars.append(ch)
            if rl < cur.x0:
                cur.x0 = rl
            if rr > cur.x1:
                cur.x1 = rr
            if y0 < cur.y0:
                cur.y0 = y0
            if y1 > cur.y1:
                cur.y1 = y1
    if cur is not None:
        lines.append(cur)
    return [ln for ln in lines if ln.text]


def _blocks(lines: list[_Line]) -> list[list[_Line]]:
    """Zeilen zu Textblöcken: neuer Block bei grösserem Abstand (Absatz), Rücksprung nach oben (neue Spalte),
    anderer Schriftgrösse oder fehlender Überlappung in der Breite."""
    blocks: list[list[_Line]] = []
    bx0 = bx1 = 0.0
    for ln in lines:
        if blocks:
            block = blocks[-1]
            last = block[-1]
            gap = ln.y0 - last.y1
            h = max(last.height, ln.height)
            overlap = min(bx1, ln.x1) - max(bx0, ln.x0)
            same_size = abs(ln.size - last.size) <= 0.15 * max(ln.size, last.size, 1)
            size = max(ln.size, 1.0)
            # Absatzende im Blocksatz: kurze letzte Zeile, nächste Zeile eingerückt
            paragraph = (len(block) > 1 and last.x1 < bx1 - 0.15 * (bx1 - bx0) and ln.x0 > bx0 + 0.8 * size)
            if (-0.5 * h <= gap <= 0.45 * h and same_size and not paragraph
                    and overlap > 0.3 * min(bx1 - bx0, ln.x1 - ln.x0)):
                block.append(ln)
                bx0, bx1 = min(bx0, ln.x0), max(bx1, ln.x1)
                continue
        blocks.append([ln])
        bx0, bx1 = ln.x0, ln.x1
    return blocks


def _page_blocks(page, textpage) -> tuple[list[str], list[tuple[float, float, float, float, str]], float]:
    """Textblöcke einer Seite, dieselben mit Position (für die Seitenzahl-Erkennung) und die Seitenhöhe."""
    crop = page.get_cropbox()
    lines = _page_lines(textpage, crop[3], page.get_mediabox())
    texts: list[str] = []
    positioned: list[tuple[float, float, float, float, str]] = []
    for block in _blocks(lines):
        t = _clean_block("\n".join(ln.text for ln in block))
        if t:
            texts.append(t)
            positioned.append((min(x.x0 for x in block) - crop[0], min(x.y0 for x in block),
                               max(x.x1 for x in block) - crop[0], max(x.y1 for x in block), t))
    return texts, positioned, crop[3] - crop[1]


# ------------------------------------------------------------------------------------------------ Abbildungen
_CAPTION = re.compile(r"^\s*(?:abbildung|abb\.|figure|fig\.|grafik|schaubild|diagramm|chart|illustration)\s*\d",
                      re.IGNORECASE)
MIN_FIGURE_SIDE = 60.0  # Punkt; kleiner sind Logos, Symbole, Zierrat
MIN_FIGURE_AREA = 0.02  # Anteil der Seite
MIN_UNCAPTIONED_AREA = 0.06  # Bilder ohne Bildunterschrift erst ab diesem Anteil der Seite
MAX_CHART_TEXT = 350  # Zeichen: mehr Text in einer Zeichnung ohne Bildunterschrift = Tabelle oder Kasten
MAX_CAPTION_GAP = 60.0  # Punkt zwischen Abbildung und Bildunterschrift
FIGURE_MARGIN = 24.0  # Punkt Rand um eine Abbildung (Beschriftungen; auch für das Bild ans Sprachmodell)


def _merge_boxes(boxes: list[tuple[float, float, float, float]], pad: float = 12.0) -> list[list[float]]:
    """Überlappende oder nahe Rahmen zusammenfassen (Linien und Flächen eines Diagramms zu einem Rahmen)."""
    merged: list[list[float]] = []
    for b in sorted(boxes, key=lambda r: (r[1], r[0])):
        for m in merged:
            if b[0] <= m[2] + pad and b[2] >= m[0] - pad and b[1] <= m[3] + pad and b[3] >= m[1] - pad:
                m[0], m[1], m[2], m[3] = min(m[0], b[0]), min(m[1], b[1]), max(m[2], b[2]), max(m[3], b[3])
                break
        else:
            merged.append(list(b))
    changed = True
    while changed:  # Zusammenfassen kann neue Überlappungen ergeben
        changed = False
        for i in range(len(merged)):
            for j in range(i + 1, len(merged)):
                a, b = merged[i], merged[j]
                if b[0] <= a[2] + pad and b[2] >= a[0] - pad and b[1] <= a[3] + pad and b[3] >= a[1] - pad:
                    a[:] = [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]
                    del merged[j]
                    changed = True
                    break
            if changed:
                break
    return merged


def _page_objects(page, want_figures: bool) -> tuple[bool, list, list]:
    """Hat die Seite Bilder? Und (nur auf Wunsch) Rahmen der Bilder und der gezeichneten Flächen und Linien."""
    import pypdfium2.raw as raw

    crop = page.get_cropbox()
    top = crop[3]
    has_images = False
    images: list[tuple[float, float, float, float]] = []
    paths: list[tuple[float, float, float, float]] = []
    types = (raw.FPDF_PAGEOBJ_IMAGE, raw.FPDF_PAGEOBJ_PATH) if want_figures else (raw.FPDF_PAGEOBJ_IMAGE,)
    for obj in page.get_objects(filter=types, max_depth=3):
        if obj.type == raw.FPDF_PAGEOBJ_IMAGE:
            has_images = True
            if not want_figures:
                break
        try:
            l, b, r, t = obj.get_bounds()
        except Exception:  # noqa: BLE001
            continue
        box = (l - crop[0], top - t, r - crop[0], top - b)
        (images if obj.type == raw.FPDF_PAGEOBJ_IMAGE else paths).append(box)
    return has_images, images, paths


def _figures_on_page(index: int, page_w: float, page_h: float, images: list, paths: list,
                     positioned: list[tuple[float, float, float, float, str]], scanned: bool) -> list[FigureCandidate]:
    """Bilder und Zeichnungen, die gross genug sind; dazu die Bildunterschrift direkt darunter oder darüber."""
    area = max(page_w * page_h, 1.0)
    out: list[FigureCandidate] = []

    def big(b) -> bool:
        w, h = b[2] - b[0], b[3] - b[1]
        return w >= MIN_FIGURE_SIDE and h >= MIN_FIGURE_SIDE and w * h >= MIN_FIGURE_AREA * area

    for b in images:
        # Ein Bild über (fast) die ganze Seite ist ein Scan der Seite, keine Abbildung
        if scanned or (b[2] - b[0]) * (b[3] - b[1]) > 0.8 * area:
            continue
        if big(b):
            out.append(FigureCandidate(index, tuple(b), "image"))
    # Gezeichnete Linien und Flächen: viele kleine Teile zu Diagrammen zusammenfassen; Tabellenlinien und
    # Unterstreichungen (flach) fallen heraus
    for m in _merge_boxes([p for p in paths if (p[2] - p[0]) < 0.95 * page_w or (p[3] - p[1]) < 0.95 * page_h]):
        if big(m) and not any(_overlap(m, f.bbox) > 0.5 for f in out):
            out.append(FigureCandidate(index, tuple(m), "drawing"))
    kept = []
    for f in out:
        f.caption = _caption_for(f.bbox, positioned)
        if f.kind == "drawing":
            pad = FIGURE_MARGIN  # Achsen- und Kategorienbeschriftungen stehen oft knapp ausserhalb der Linien
            inner = " ".join(t for x0, y0, x1, y1, t in positioned
                             if x0 >= f.bbox[0] - pad and x1 <= f.bbox[2] + pad and y0 >= f.bbox[1] - pad
                             and y1 <= f.bbox[3] + pad and not _CAPTION.match(t))
            # Ohne Bildunterschrift: viel Text in den Linien ist eine Tabelle oder ein Kasten, kein Diagramm
            if not f.caption and len(inner) > MAX_CHART_TEXT:
                continue
            f.text = inner[:1500]
        elif not f.caption and (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]) < MIN_UNCAPTIONED_AREA * area:
            continue  # kleines Bild ohne Unterschrift: meist Logo oder Porträt
        kept.append(f)
    return kept


def _overlap(a, b) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    return w * h / max(min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1])), 1.0)


def _caption_for(bbox, positioned) -> str:
    """Bildunterschrift: ein Block, der mit „Abbildung 3“, „Fig. 2“ … beginnt, direkt unter oder über der Abbildung
    (höchstens MAX_CAPTION_GAP entfernt und in der Breite überlappend); sonst keine."""
    best, best_d = "", 1e9
    for x0, y0, x1, y1, t in positioned:
        if not _CAPTION.match(t):
            continue
        dy = y0 - bbox[3] if y0 >= bbox[3] - 5 else bbox[1] - y1 if y1 <= bbox[1] + 5 else 0
        overlap = min(bbox[2], x1) - max(bbox[0], x0)
        if abs(dy) > MAX_CAPTION_GAP or overlap <= 0:
            continue
        if abs(dy) < best_d:
            best, best_d = t, abs(dy)
    return best[:600]


# ------------------------------------------------------------------------------------------------ OCR (Tesseract)
OCR_DPI = 200
_TESSERACT_DIRS = (r"C:\Program Files\Tesseract-OCR", r"C:\Program Files (x86)\Tesseract-OCR")


def find_tesseract() -> str | None:
    """tesseract.exe: im Suchpfad, im üblichen Installationsordner oder unter %LOCALAPPDATA%\\Programs."""
    found = shutil.which("tesseract")
    if found:
        return found
    dirs = list(_TESSERACT_DIRS)
    if os.environ.get("LOCALAPPDATA"):
        dirs.append(os.path.join(os.environ["LOCALAPPDATA"], "Programs", "Tesseract-OCR"))
    for d in dirs:
        exe = os.path.join(d, "tesseract.exe")
        if os.path.isfile(exe):
            return exe
    return None


def _render_gray(page, dpi: int):
    """Seite als Graustufenbild (NumPy-Array H×W)."""
    bitmap = page.render(scale=dpi / 72, grayscale=True)
    try:
        arr = bitmap.to_numpy()
    finally:
        bitmap.close()
    return arr.reshape(arr.shape[0], arr.shape[1], -1)[:, :, 0].copy()


def _ocr_page(page, languages: str, tesseract: str) -> tuple[list[str], list[tuple[str, int]]]:
    """Seite mit Tesseract lesen: Blöcke aus der TSV-Ausgabe (Block, Absatz, Zeile) mit Position."""
    arr = _render_gray(page, OCR_DPI)
    height_pt = page.get_cropbox()[3] - page.get_cropbox()[1]
    fd, img = tempfile.mkstemp(suffix=".pgm", prefix="zotivity-ocr-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(f"P5\n{arr.shape[1]} {arr.shape[0]}\n255\n".encode() + arr.tobytes())
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        r = subprocess.run([tesseract, img, "stdout", "-l", languages or "deu+eng", "--psm", "3", "tsv"],
                           capture_output=True, timeout=180, creationflags=flags)
        if r.returncode != 0:
            raise RuntimeError((r.stderr or b"").decode("utf-8", "replace").strip()[:300] or "Tesseract-Fehler")
        tsv = r.stdout.decode("utf-8", "replace")
    finally:
        try:
            os.unlink(img)
        except OSError:
            pass
    scale = 72 / OCR_DPI
    blocks: dict[tuple[int, int], dict] = {}
    for row in tsv.splitlines()[1:]:
        parts = row.split("\t")
        if len(parts) < 12 or parts[0] != "5" or not parts[11].strip():
            continue
        key = (int(parts[2]), int(parts[3]))
        line = int(parts[4])
        x, y, w, h = (int(v) for v in parts[6:10])
        b = blocks.setdefault(key, {"lines": {}, "box": [x, y, x + w, y + h]})
        b["lines"].setdefault(line, []).append(parts[11])
        b["box"] = [min(b["box"][0], x), min(b["box"][1], y), max(b["box"][2], x + w), max(b["box"][3], y + h)]
    texts: list[str] = []
    positioned = []
    for key in sorted(blocks):
        b = blocks[key]
        t = _clean_block("\n".join(" ".join(words) for _, words in sorted(b["lines"].items())))
        if t:
            texts.append(t)
            x0, y0, x1, y1 = (v * scale for v in b["box"])
            positioned.append((x0, y0, x1, y1, t))
    return texts, margin_candidates(positioned, height_pt)


# ------------------------------------------------------------------------------------------------ PDF lesen
def extract_pdf(
    path: Path,
    *,
    max_pages: int = 3000,
    min_chars_per_page: int = 40,
    ocr: bool = False,
    ocr_languages: str = "deu+eng",
    page_range: tuple[int, int] | None = None,
    figures: bool = False,
) -> ExtractedDoc:
    """page_range: Seitenspanne laut Zotero („Seiten“), zur Gegenprobe der erkannten Seitenzahlen.
    figures: auch mögliche Abbildungen sammeln (Bilder und Zeichnungen mit Bildunterschrift)."""
    import pypdfium2 as pdfium

    with _PDFIUM_LOCK:
        try:
            doc = pdfium.PdfDocument(str(path))
        except pdfium.PdfiumError as e:
            if "password" in str(e).lower():
                return ExtractedDoc(warnings=["Passwortgeschütztes Dokument"])
            raise
        result = ExtractedDoc(n_pages=len(doc))
        try:
            n = min(len(doc), max_pages)
            if len(doc) > max_pages:
                result.warnings.append(f"Nur die ersten {max_pages} von {len(doc)} Seiten indexiert")
            raw: list[list[str]] = []
            cands: list[list[tuple[str, int]]] = []
            positions: list[list] = []
            sizes: list[tuple[float, float]] = []
            pdf_labels: list[str] = []
            images: list[bool] = []
            objects: list[tuple[list, list]] = []
            for i in range(n):
                page = doc[i]
                textpage = page.get_textpage()
                try:
                    blocks, positioned, height = _page_blocks(page, textpage)
                    has_img, img_boxes, path_boxes = _page_objects(page, figures)
                    sizes.append((page.get_width(), height))
                finally:
                    textpage.close()
                    page.close()
                raw.append(blocks)
                positions.append(positioned)
                cands.append(margin_candidates(positioned, height))
                images.append(has_img)
                objects.append((img_boxes, path_boxes))
                try:
                    pdf_labels.append(clean_pdf_label(doc.get_page_label(i)))
                except Exception:  # noqa: BLE001
                    pdf_labels.append("")
            # Ob die Seiten Text haben, ohne Kopf-/Fusszeilen gezählt: ein Scan mit einer Stempelzeile je Seite (z. B.
            # „Kopie von subito e.V. …“) ist sonst kein Scan, und die Texterkennung startet nie
            total = sum(len(" ".join(b)) for b in _strip_running_heads(raw))
            scanned = bool(n and total / n < min_chars_per_page)
            if scanned:
                result.needs_ocr = True
                if ocr:
                    tess = find_tesseract()
                    if not tess:
                        result.warnings.append("Texterkennung nicht möglich: Tesseract ist nicht installiert "
                                               "(tesseract.exe nicht gefunden)")
                    else:
                        try:
                            ocr_pages = []
                            for i in range(n):
                                page = doc[i]
                                try:
                                    ocr_pages.append(_ocr_page(page, ocr_languages, tess))
                                finally:
                                    page.close()
                            raw = [p[0] for p in ocr_pages]
                            cands = [p[1] for p in ocr_pages]
                            result.method = "ocr"
                            result.needs_ocr = False
                        except Exception as e:  # noqa: BLE001  (Sprachdaten fehlen o. ä.)
                            result.warnings.append(f"Texterkennung nicht möglich: {e}")
            if figures:
                for i in range(n):
                    w, h = sizes[i]
                    result.figures += _figures_on_page(i, w, h, objects[i][0], objects[i][1], positions[i], scanned)
            # Gedruckte Seitenzahlen bestimmen, bevor Kopf-/Fusszeilen aus dem Text entfernt werden
            labels, result.label_source = detect_page_labels(cands, pdf_labels, page_range)
            raw = _strip_running_heads(raw)
            for i in range(n):
                text = _fix_text("\n\n".join(raw[i]).strip())  # Sicherheitsnetz: nie halbe Zeichen speichern
                result.pages.append(PageText(index=i, label=labels[i], text=text, has_images=images[i]))
        finally:
            doc.close()
    return result


# ------------------------------------------------------------------------------------------------ EPUB
def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def extract_epub(path: Path, max_pages: int = 3000) -> ExtractedDoc:
    """EPUB: Kapitel in der Lesereihenfolge (Spine), je Kapitel Pseudoseiten von ~3000 Zeichen, ohne Seitenzahlen."""
    with zipfile.ZipFile(path) as z:
        container = ElementTree.fromstring(z.read("META-INF/container.xml"))
        rootfile = next((el.get("full-path") for el in container.iter() if _local(el.tag) == "rootfile"), None)
        if not rootfile:
            raise ValueError("EPUB ohne Inhaltsverzeichnis (container.xml)")
        opf = ElementTree.fromstring(z.read(rootfile))
        base = PurePosixPath(rootfile).parent
        manifest = {el.get("id"): el.get("href") for el in opf.iter() if _local(el.tag) == "item"}
        spine = [el.get("idref") for el in opf.iter() if _local(el.tag) == "itemref"]
        names = set(z.namelist())
        pages: list[PageText] = []
        for idref in spine:
            href = manifest.get(idref)
            if not href:
                continue
            name = str(base / unquote(href.split("#")[0])) if str(base) != "." else unquote(href.split("#")[0])
            if name not in names:
                continue
            text = html_to_text(z.read(name).decode("utf-8", errors="replace"))
            for chunk in text_as_document(text, "epub").pages:
                pages.append(PageText(index=len(pages), label="", text=chunk.text))
                if len(pages) >= max_pages:
                    break
            if len(pages) >= max_pages:
                break
    return ExtractedDoc(pages=pages, n_pages=len(pages), method="epub")


class _HTMLText(HTMLParser):
    """Sichtbarer Text einer Webseite. Der Kopfbereich endet – wie im Browser – spätestens mit <body>: Gespeicherte
    Verlagsseiten (z. B. Wiley-Schnappschüsse aus Zotero) schliessen <head> nicht sauber, sonst ginge der ganze Text
    verloren. lenient=True überspringt nur Skripte, Stile und Grafiken (für Seiten mit ungeschlossenen nav/footer)."""
    _SKIP = {"script", "style", "noscript", "nav", "footer", "svg", "template"}
    _SKIP_LENIENT = {"script", "style", "svg", "template"}
    _BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "tr", "blockquote"}

    def __init__(self, lenient: bool = False):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_tags = self._SKIP_LENIENT if lenient else self._SKIP
        self._skip = 0
        self._head = False

    def handle_starttag(self, tag, attrs):
        if tag == "head":
            self._head = True
        elif tag == "body":
            self._head = False
        elif tag in self._skip_tags:
            self._skip += 1
        elif tag in self._BLOCK:
            self.parts.append("\n\n")

    def handle_startendtag(self, tag, attrs):
        # <svg/>, <br/> usw.: öffnet keinen Bereich
        if tag in self._BLOCK:
            self.parts.append("\n\n")

    def handle_endtag(self, tag):
        if tag == "head":
            self._head = False
        elif tag in self._skip_tags:
            if self._skip:
                self._skip -= 1
        elif tag in self._BLOCK:
            self.parts.append("\n\n")

    def handle_data(self, data):
        if not self._skip and not self._head:
            self.parts.append(data)


def _html_text(html: str, lenient: bool) -> str:
    p = _HTMLText(lenient)
    p.feed(html)
    p.close()
    paras = [_WS.sub(" ", x).strip() for x in re.split(r"\n\s*\n", "".join(p.parts))]
    return "\n\n".join(x for x in paras if x)


def html_to_text(html: str) -> str:
    text = _html_text(html, lenient=False)
    if len(text) < 500:  # z. B. ein ungeschlossenes <nav> verschluckt den Rest: grosszügiger lesen
        loose = _html_text(html, lenient=True)
        if len(loose) > 2 * max(len(text), 1):
            return loose
    return text


def text_as_document(text: str, method: str) -> ExtractedDoc:
    """Fliesstext ohne Seiten (z. B. Zotero-Volltext) als Pseudoseiten von ~3000 Zeichen."""
    paras = [p.strip() for p in re.split(r"\n\s*\n|\n", text) if p.strip()]
    pages: list[PageText] = []
    buf: list[str] = []
    size = 0
    for para in paras:
        buf.append(para)
        size += len(para)
        if size >= 3000:
            pages.append(PageText(index=len(pages), label="", text="\n\n".join(buf)))
            buf, size = [], 0
    if buf:
        pages.append(PageText(index=len(pages), label="", text="\n\n".join(buf)))
    return ExtractedDoc(pages=pages, n_pages=len(pages), method=method)


def extract_file(
    path: Path,
    content_type: str,
    *,
    max_pages: int = 3000,
    min_chars_per_page: int = 40,
    ocr: bool = False,
    ocr_languages: str = "deu+eng",
    page_range: tuple[int, int] | None = None,
    figures: bool = False,
) -> ExtractedDoc:
    ct = (content_type or "").lower()
    suffix = path.suffix.lower()
    if ct == "application/pdf" or suffix == ".pdf":
        return extract_pdf(path, max_pages=max_pages, min_chars_per_page=min_chars_per_page, ocr=ocr,
                           ocr_languages=ocr_languages, page_range=page_range, figures=figures)
    if ct == "application/epub+zip" or suffix == ".epub":
        return extract_epub(path, max_pages=max_pages)
    if ct in ("text/html", "application/xhtml+xml") or suffix in (".html", ".htm", ".xhtml"):
        raw = path.read_bytes()
        html = raw.decode("utf-8", errors="replace")
        return text_as_document(html_to_text(html), "html")
    from zotivity.extract import office  # hier, weil office die Datenklassen aus diesem Modul verwendet

    if ct == office.DOCX_TYPE or suffix == ".docx":
        return office.extract_docx(path, max_pages=max_pages)
    if ct == office.ODT_TYPE or suffix == ".odt":
        return office.extract_odt(path, max_pages=max_pages)
    if ct.startswith("text/") or suffix in (".txt", ".md"):
        return text_as_document(path.read_text(encoding="utf-8", errors="replace"), "text")
    raise ValueError(f"Nicht unterstützter Dateityp: {content_type or suffix}")


# ------------------------------------------------------------------------------------------------ Seitenbilder
def encode_png(arr) -> bytes:
    """PNG aus einem NumPy-Array (H×W Graustufen oder H×W×3 RGB), ohne Bildbibliothek."""
    import numpy as np

    arr = np.ascontiguousarray(arr, dtype=np.uint8)
    h, w = arr.shape[:2]
    channels = 1 if arr.ndim == 2 else arr.shape[2]
    color = {1: 0, 3: 2, 4: 6}[channels]
    rows = np.empty((h, 1 + w * channels), dtype=np.uint8)
    rows[:, 0] = 0
    rows[:, 1:] = arr.reshape(h, w * channels)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, color, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows.tobytes(), 6)) + chunk(b"IEND", b""))


def render_page_png(path: Path, page_index: int, zoom: float = 1.6,
                    clip: tuple[float, float, float, float] | None = None) -> bytes:
    """Seite (oder mit clip einen Ausschnitt x0, y0, x1, y1 in Punkt, y von oben) als PNG – für Bildanfragen an
    multimodale Sprachmodelle und für Abbildungen. Der Ausschnitt wird auf die Seite begrenzt (Rand um eine Abbildung
    am Seitenrand, Objekte, die über die Seite hinausragen); bleibt nichts übrig oder ist die Seite gedreht, kommt die
    ganze Seite."""
    import pypdfium2 as pdfium

    with _PDFIUM_LOCK:
        doc = pdfium.PdfDocument(str(path))
        try:
            page = doc[page_index]
            try:
                crop = (0, 0, 0, 0)
                if clip and page.get_rotation() % 180 == 0:
                    w, h = page.get_width(), page.get_height()
                    x0, y0, x1, y1 = (min(max(v, 0.0), lim) for v, lim in zip(clip, (w, h, w, h)))
                    if x1 - x0 >= 2 and y1 - y0 >= 2:
                        crop = (x0, h - y1, w - x1, y0)
                bitmap = page.render(scale=zoom, crop=crop, rev_byteorder=True)
                try:
                    arr = bitmap.to_numpy()
                finally:
                    bitmap.close()
            finally:
                page.close()
        finally:
            doc.close()
    return encode_png(arr[:, :, :3] if arr.ndim == 3 else arr)
