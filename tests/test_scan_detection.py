"""Scans mit einer Stempelzeile je Seite (z. B. subito): als Scan erkennen, Zotero-Volltext aus Stempeln nicht nehmen."""

from fakes import make_pdf
from test_indexing_search import TEEN, run_index

from zotivity.extract.pdf import extract_with_pymupdf, substantive_chars

STAMP = "Kopie von subito e.V., geliefert für Hochschule für Soziale Arbeit FHNW (SLI-Nr. 4711)"


def test_stamp_only_pages_count_as_scan(tmp_path):
    scan = make_pdf(tmp_path / "scan.pdf", [STAMP] * 6, first_page=None)
    assert len(STAMP) > 40  # die Stempelzeile allein läge über der Grenze „Zeichen je Seite“
    assert extract_with_pymupdf(scan).needs_ocr is True
    # Echter Text mit derselben Kopfzeile bleibt Text
    text = make_pdf(tmp_path / "text.pdf", [TEEN * 4] * 6, header=STAMP)
    doc = extract_with_pymupdf(text)
    assert doc.needs_ocr is False and doc.char_count > 1000


def test_substantive_chars_ignores_repeated_lines():
    stamps = "\n".join(f"{STAMP} Seite {i}" for i in range(1, 25))
    assert len(stamps) > 1900 and substantive_chars(stamps) == 0
    assert substantive_chars(stamps + "\n" + TEEN * 2) > 400


def test_zotero_fulltext_of_stamps_is_not_indexed(zengine, fake_zotero, monkeypatch):
    fake_zotero.add_item("ITEMS001", "Gescannter Aufsatz", "Scanner", "2019")
    fake_zotero.add_pdf("ATTS0001", "ITEMS001", [STAMP] * 6, first_page=None)
    stamps = "\n".join(f"{STAMP} Seite {i}" for i in range(1, 25))
    monkeypatch.setattr(zengine.zotero(), "fulltext", lambda lib, key: stamps)
    run_index(zengine)
    att = zengine.store.attachment("users/0", "ATTS0001")
    assert att["status"] == "no_text" and "OCR" in (att.get("error") or "")
    assert not [r for r in zengine.store.document_chunks("users/0", "ITEMS001") if r["kind"] == "text"]
