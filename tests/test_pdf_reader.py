"""Neuer PDF-Leser auf PDFium: Text und Blöcke, Seitenbeschriftungen, EPUB, OCR über Tesseract (simuliert),
Seitenbilder, Kandidaten für Abbildungen – und kein PyMuPDF mehr im Programm."""

from __future__ import annotations

import re
import subprocess
import zipfile
from pathlib import Path

import pytest

from zotivity.extract import pdf as reader
from zotivity.extract import pdfmake
from zotivity.extract.pdf import extract_file, extract_pdf, render_page_png

ROOT = Path(__file__).resolve().parent.parent
PARA = ("Die Fachpersonen dokumentieren ihre Arbeit in einem elektronischen System. Dabei entstehen "
        "Spannungen zwischen Vorgaben und professionellem Ermessen. ")


def test_text_umlauts_paragraphs_and_hyphenation(tmp_path):
    page = pdfmake.Page()
    page.text(60, 80, "Überschrift über Ärger und Öffnung", 16)
    y = 120
    for line in ["Erste Zeile eines Absatzes mit einer Silben-", "trennung am Zeilenende und weiterem Text,",
                 "der hier endet."]:
        page.text(60, y, line, 10)
        y += 12
    y += 14  # Absatzabstand
    page.text(60, y, "Zweiter Absatz nach einem Abstand.", 10)
    path = tmp_path / "t.pdf"
    path.write_bytes(pdfmake.build([page]))
    doc = extract_pdf(path)
    paras = doc.pages[0].text.split("\n\n")
    assert paras[0] == "Überschrift über Ärger und Öffnung"
    assert paras[1] == "Erste Zeile eines Absatzes mit einer Silbentrennung am Zeilenende und weiterem Text, der hier endet."
    assert paras[2] == "Zweiter Absatz nach einem Abstand."
    assert doc.method == "pdfium" and doc.n_pages == 1


def test_indented_paragraphs_without_spacing_are_split(tmp_path):
    page = pdfmake.Page()
    lines = [(60, "Ein Absatz im Blocksatz mit mehreren Zeilen, die bis an den rechten Rand"),
             (60, "reichen und am Ende kurz ausläuft."),
             (75, "Der nächste Absatz beginnt eingerückt ohne zusätzlichen Abstand und läuft"),
             (60, "wieder bis an den Rand.")]
    for k, (x, text) in enumerate(lines):
        page.text(x, 100 + 12 * k, text, 10)
    path = tmp_path / "p.pdf"
    path.write_bytes(pdfmake.build([page]))
    assert len(extract_pdf(path).pages[0].text.split("\n\n")) == 2


def test_two_columns_stay_apart(tmp_path):
    page = pdfmake.Page()
    for k in range(6):
        page.text(50, 100 + 12 * k, f"Linke Spalte Zeile {k} mit Text", 10)
    for k in range(6):
        page.text(320, 100 + 12 * k, f"Rechte Spalte Zeile {k} mit Text", 10)
    path = tmp_path / "c.pdf"
    path.write_bytes(pdfmake.build([page]))
    text = extract_pdf(path).pages[0].text
    left, right = text.index("Linke Spalte Zeile 5"), text.index("Rechte Spalte Zeile 0")
    assert left < right and "Zeile 5 mit Text Rechte" not in text


def test_pdf_page_labels_are_used(tmp_path):
    pages = [pdfmake.text_page(PARA * 6) for _ in range(5)]
    path = tmp_path / "l.pdf"
    path.write_bytes(pdfmake.build(pages, labels=[(0, "r", 1), (2, "D", 1)]))
    doc = extract_pdf(path)
    assert [p.label for p in doc.pages] == ["i", "ii", "1", "2", "3"] and doc.label_source == "pdf"


def test_figures_drawings_images_and_captions(tmp_path):
    page = pdfmake.text_page(PARA * 3, box=(60, 60, 540, 200))
    page.charts.append((100, 250, 450, 450))
    page.text(100, 470, "Abbildung 2: Anteil der Fachpersonen nach Arbeitsfeld", 9)
    page.images.append((100, 520, 300, 700))
    page.text(100, 715, "Fig. 3 Foto einer Beratungssituation", 9)
    page.images.append((500, 30, 520, 50))  # Logo: zu klein
    path = tmp_path / "f.pdf"
    path.write_bytes(pdfmake.build([page]))
    assert extract_pdf(path).figures == []  # nur auf Wunsch
    figs = extract_pdf(path, figures=True).figures
    kinds = sorted((f.kind, f.caption.split(":")[0].split(" Foto")[0]) for f in figs)
    assert kinds == [("drawing", "Abbildung 2"), ("image", "Fig. 3")]
    drawing = next(f for f in figs if f.kind == "drawing")
    assert 95 <= drawing.bbox[0] <= 110 and 440 <= drawing.bbox[3] <= 455
    png = render_page_png(path, 0, clip=drawing.bbox)
    assert png.startswith(b"\x89PNG\r\n\x1a\n") and len(png) > 200


def test_full_page_scan_image_is_not_a_figure(tmp_path):
    page = pdfmake.Page()
    page.images.append((0, 0, 595, 842))
    path = tmp_path / "s.pdf"
    path.write_bytes(pdfmake.build([page] * 4))
    doc = extract_pdf(path, figures=True)
    assert doc.needs_ocr and doc.figures == [] and all(p.has_images for p in doc.pages)


def test_ocr_with_tesseract_tsv(tmp_path, monkeypatch):
    page = pdfmake.Page()
    page.images.append((0, 0, 595, 842))
    path = tmp_path / "scan.pdf"
    path.write_bytes(pdfmake.build([page] * 4))
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        n = len(calls)
        rows = ["level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"]
        word = ["eins", "zwei", "drei", "vier"][n - 1]  # Ziffern zählen beim Erkennen von Kopfzeilen nicht
        words = f"Erkannter Text aus dem Scan {word} mit genug Zeichen für eine ganze Seite".split() * 2
        for k, w in enumerate(words):
            rows.append(f"5\t1\t1\t1\t{1 + k // 6}\t{k % 6}\t{200 + 80 * (k % 6)}\t{300 + 40 * (k // 6)}\t70\t30\t95\t{w}")
        rows.append(f"5\t1\t2\t1\t1\t1\t800\t2250\t40\t30\t95\t{40 + n}")  # Seitenzahl unten
        return subprocess.CompletedProcess(cmd, 0, stdout="\n".join(rows).encode(), stderr=b"")

    monkeypatch.setattr(reader, "find_tesseract", lambda: "tesseract.exe")
    monkeypatch.setattr(reader.subprocess, "run", fake_run)
    doc = extract_pdf(path, ocr=True, ocr_languages="deu")
    assert doc.method == "ocr" and not doc.needs_ocr and len(calls) == 4
    assert calls[0][0] == "tesseract.exe" and calls[0][-1] == "tsv" and "deu" in calls[0]
    assert "Erkannter Text aus dem Scan" in doc.pages[0].text
    assert [p.label for p in doc.pages] == ["41", "42", "43", "44"]


def test_ocr_without_tesseract_says_so(tmp_path, monkeypatch):
    page = pdfmake.Page()
    page.images.append((0, 0, 595, 842))
    path = tmp_path / "scan.pdf"
    path.write_bytes(pdfmake.build([page] * 2))
    monkeypatch.setattr(reader, "find_tesseract", lambda: None)
    doc = extract_pdf(path, ocr=True)
    assert doc.needs_ocr and any("Tesseract" in w for w in doc.warnings)


def test_epub_chapters_in_spine_order(tmp_path):
    path = tmp_path / "buch.epub"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", '<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                   '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        z.writestr("OEBPS/content.opf", '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf"><manifest>'
                   '<item id="c2" href="Text/kap%202.xhtml" media-type="application/xhtml+xml"/>'
                   '<item id="c1" href="Text/kap1.xhtml" media-type="application/xhtml+xml"/></manifest>'
                   '<spine><itemref idref="c1"/><itemref idref="c2"/></spine></package>')
        z.writestr("OEBPS/Text/kap1.xhtml", "<html><head><title>x</title></head><body><h1>Kapitel 1</h1><p>Erster Text über Jugendhilfe.</p></body></html>")
        z.writestr("OEBPS/Text/kap 2.xhtml", "<html><body><h1>Kapitel 2</h1><p>Zweiter Text.</p></body></html>")
    doc = extract_file(path, "application/epub+zip")
    text = "\n".join(p.text for p in doc.pages)
    assert doc.method == "epub" and text.index("Kapitel 1") < text.index("Kapitel 2")
    assert "Erster Text über Jugendhilfe." in text and all(p.label == "" for p in doc.pages)


def test_characters_outside_the_basic_plane(tmp_path):
    """Formel-PDFs: N und x in mathematischer Kursive und Emojis liefert PDFium als zwei UTF-16-Hälften. Ohne
    Zusammensetzen liess sich der Text nicht speichern («surrogates not allowed»). Mathematische Buchstaben werden zu
    gewöhnlichen, damit die Suche «N = 120» findet; beide Lesewege (Zeilen, Zeichen für Zeichen) liefern dasselbe."""
    import pypdfium2 as pdfium

    page = pdfmake.Page()
    page.text(60, 80, "Stichprobe N = 120, Mittelwert x # Ende.", 11)
    page.text(60, 100, "Zweite Zeile ohne Sonderzeichen.", 11)
    path = tmp_path / "formel.pdf"
    path.write_bytes(pdfmake.build([page], to_unicode={"N": "\U0001d441", "x": "\U0001d465", "#": "\U0001f99c"}))
    expected = "Stichprobe N = 120, Mittelwert x \U0001f99c Ende."
    text = extract_pdf(path).pages[0].text
    assert text.split("\n\n")[0] == expected
    text.encode("utf-8")  # speicherbar
    doc = pdfium.PdfDocument(str(path))
    try:
        p = doc[0]
        lines = reader._page_lines_by_char(p.get_textpage(), p.get_cropbox()[3], p.get_mediabox())
        assert lines[0].text == expected
    finally:
        doc.close()


def test_fix_text():
    fix = reader._fix_text
    assert fix("a𝑁b") == "aNb"  # Paar zusammengesetzt, mathematisches N als N
    assert fix("x\ud835 y\udc41z") == "x yz"  # einzelne Hälften fallen weg
    assert fix("\U0001d499 = \U0001d7cf, ℎ") == "x = 1, h"  # fett-kursives x, fette 1, Planck-h
    assert fix("Papagei \U0001f99c, m² und ﬁ") == "Papagei \U0001f99c, m² und ﬁ"  # sonst gleich


def test_png_encoder():
    import numpy as np

    png = reader.encode_png(np.zeros((3, 4, 3), dtype=np.uint8))
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and png[12:16] == b"IHDR" and png.endswith(b"IEND\xaeB`\x82")


def test_no_pymupdf_in_the_program():
    """SignPath verlangt Bestandteile ohne kommerzielle Doppellizenz: PyMuPDF (AGPL oder kommerziell) ist ersetzt."""
    for f in (ROOT / "src" / "zotivity").rglob("*.py"):
        text = f.read_text(encoding="utf-8")
        assert not re.search(r"^\s*(import pymupdf|import fitz|from pymupdf|from fitz)", text, re.M), f
    deps = (ROOT / "pyproject.toml").read_text(encoding="utf-8").lower()
    assert "pymupdf" not in deps and "pypdfium2" in deps
    assert "pymupdf" not in (ROOT / "packaging" / "zotivity.spec").read_text(encoding="utf-8").lower()
    assert "pymupdf" not in (ROOT / "packaging" / "constraints.txt").read_text(encoding="utf-8").lower()


@pytest.mark.parametrize("name", ["passwort"])
def test_password_protected_pdf_is_reported(tmp_path, monkeypatch, name):
    import pypdfium2

    def locked(*a, **k):
        raise pypdfium2.PdfiumError("Failed to load document (PDFium: Incorrect password error).")
    monkeypatch.setattr(pypdfium2, "PdfDocument", locked)
    path = tmp_path / f"{name}.pdf"
    path.write_bytes(b"%PDF-1.7")
    doc = extract_pdf(path)
    assert doc.warnings == ["Passwortgeschütztes Dokument"] and doc.pages == []
