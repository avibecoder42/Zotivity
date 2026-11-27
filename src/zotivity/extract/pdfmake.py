"""Kleiner PDF-Schreiber für den Selbsttest und die Tests – ohne Fremdbibliothek.

Text in Helvetica (WinAnsi, also mit Umlauten), auf Wunsch Seitenbeschriftungen (/PageLabels), graue Rechtecke als
eingebettete Bilder und gezeichnete Balkendiagramme (Vektorgrafik) für die Erkennung von Abbildungen. Koordinaten
wie auf dem Bildschirm: x von links, y von oben, in Punkt (1/72 Zoll).
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass, field

PAGE_W, PAGE_H = 595.0, 842.0
AVG_WIDTH = 0.53  # mittlere Zeichenbreite von Helvetica in em – reicht für den Zeilenumbruch


@dataclass
class Page:
    texts: list[tuple[float, float, float, str]] = field(default_factory=list)  # x, y (Grundlinie), Grösse, Text
    images: list[tuple[float, float, float, float]] = field(default_factory=list)  # x0, y0, x1, y1
    charts: list[tuple[float, float, float, float]] = field(default_factory=list)  # Balkendiagramm in diesem Rahmen

    def text(self, x: float, y: float, text: str, size: float = 10) -> Page:
        self.texts.append((x, y, size, text))
        return self


def wrap(text: str, width: float, size: float) -> list[str]:
    """Zeilen für eine Spaltenbreite (Absätze bleiben getrennt, leere Zeile dazwischen)."""
    per_line = max(10, int(width / (size * AVG_WIDTH)))
    out: list[str] = []
    for para in text.split("\n"):
        line = ""
        for word in para.split():
            if line and len(line) + 1 + len(word) > per_line:
                out.append(line)
                line = word
            else:
                line = f"{line} {word}" if line else word
        out.append(line)
    return out


def text_page(text: str, *, size: float = 10, box: tuple[float, float, float, float] = (60, 60, 540, 780),
              leading: float = 1.2) -> Page:
    """Text in einen Rahmen setzen (wie ein Textfeld); was nicht passt, fällt weg."""
    page = Page()
    x0, y0, x1, y1 = box
    y = y0 + size
    for line in wrap(text, x1 - x0, size):
        if y > y1:
            break
        if line:
            page.texts.append((x0, y, size, line))
        y += size * leading
    return page


def _escape(text: str) -> bytes:
    data = text.encode("cp1252", errors="replace")
    return data.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def _num(v: float) -> bytes:
    return (f"{v:.2f}".rstrip("0").rstrip(".") or "0").encode()


def _content(page: Page) -> bytes:
    out = []
    for x0, y0, x1, y1 in page.images:
        out.append(b"q " + _num(x1 - x0) + b" 0 0 " + _num(y1 - y0) + b" " + _num(x0) + b" " + _num(PAGE_H - y1)
                   + b" cm /Im1 Do Q")
    for x0, y0, x1, y1 in page.charts:
        w, h = x1 - x0, y1 - y0
        out.append(b"q 0.8 w 0 0 0 RG " + _num(x0) + b" " + _num(PAGE_H - y1) + b" m " + _num(x0) + b" "
                   + _num(PAGE_H - y0) + b" l S " + _num(x0) + b" " + _num(PAGE_H - y1) + b" m " + _num(x1) + b" "
                   + _num(PAGE_H - y1) + b" l S Q")
        for k in range(5):  # fünf Balken
            bx = x0 + w * (0.08 + 0.18 * k)
            bh = h * (0.25 + 0.14 * k)
            out.append(b"q 0.4 0.5 0.7 rg " + _num(bx) + b" " + _num(PAGE_H - y1) + b" " + _num(w * 0.12) + b" "
                       + _num(bh) + b" re f Q")
    for x, y, size, text in page.texts:
        out.append(b"BT /F1 " + _num(size) + b" Tf 1 0 0 1 " + _num(x) + b" " + _num(PAGE_H - y) + b" Tm ("
                   + _escape(text) + b") Tj ET")
    return b"\n".join(out)


def _to_unicode_cmap(mapping: dict[str, str]) -> bytes:
    """ToUnicode-Tabelle: Zeichen der Schrift → beliebiger Unicode-Text (auch ausserhalb der Grundebene, wie 𝑁 in
    Formel-PDFs)."""
    rows = "\n".join(f"<{ord(src):02X}> <{dst.encode('utf-16-be').hex().upper()}>" for src, dst in mapping.items())
    cmap = ("/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n/CMapName /Zotivity def\n/CMapType 2 def\n"
            "1 begincodespacerange\n<00> <FF>\nendcodespacerange\n"
            f"{len(mapping)} beginbfchar\n{rows}\nendbfchar\n"
            "endcmap\nCMapName currentdict /CMap defineresource pop\nend\nend").encode("ascii")
    return b"<< /Length " + str(len(cmap)).encode() + b" >>\nstream\n" + cmap + b"\nendstream"


def build(pages: list[Page], labels: list[tuple[int, str, int]] | None = None,
          to_unicode: dict[str, str] | None = None) -> bytes:
    """PDF-Datei als Bytes. labels: [(erste Seite 0-basiert, Stil „D“ oder „r“, erste Nummer)]. to_unicode: Zeichen,
    die beim Auslesen als anderer Text erscheinen sollen (ToUnicode-Tabelle der Schrift)."""
    objs: list[bytes] = []

    def add(body: bytes) -> int:
        objs.append(body)
        return len(objs)

    catalog = add(b"")  # 1, später gefüllt
    pages_id = add(b"")  # 2
    cmap = b" /ToUnicode " + str(add(_to_unicode_cmap(to_unicode))).encode() + b" 0 R" if to_unicode else b""
    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding" + cmap + b" >>")
    pixels = bytes([96 + (i * 7) % 64 for i in range(64 * 64)])  # 64×64 Graustufen
    data = zlib.compress(pixels)
    image = add(b"<< /Type /XObject /Subtype /Image /Width 64 /Height 64 /ColorSpace /DeviceGray /BitsPerComponent 8"
                b" /Filter /FlateDecode /Length " + str(len(data)).encode() + b" >>\nstream\n" + data + b"\nendstream")
    kids = []
    for page in pages:
        content = _content(page)
        cid = add(b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream")
        kids.append(add(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 " + _num(PAGE_W) + b" " + _num(PAGE_H)
                        + b"] /Resources << /Font << /F1 " + str(font).encode() + b" 0 R >> /XObject << /Im1 "
                        + str(image).encode() + b" 0 R >> >> /Contents " + str(cid).encode() + b" 0 R >>"))
    objs[pages_id - 1] = (b"<< /Type /Pages /Kids [" + b" ".join(str(k).encode() + b" 0 R" for k in kids)
                          + b"] /Count " + str(len(kids)).encode() + b" >>")
    cat = b"<< /Type /Catalog /Pages 2 0 R"
    if labels:
        nums = b" ".join(str(start).encode() + b" << /S /" + style.encode() + b" /St " + str(first).encode() + b" >>"
                         for start, style, first in labels)
        cat += b" /PageLabels << /Nums [" + nums + b"] >>"
    objs[catalog - 1] = cat + b" >>"

    out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += str(i).encode() + b" 0 obj\n" + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 " + str(len(objs) + 1).encode() + b"\n0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (b"trailer\n<< /Size " + str(len(objs) + 1).encode() + b" /Root 1 0 R >>\nstartxref\n"
            + str(xref).encode() + b"\n%%EOF\n")
    return bytes(out)
