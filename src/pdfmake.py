"""Small PDF writer for scans, text files and the test page."""
import struct
import textwrap

A4 = (595, 842)
MARGIN = 50
TEXT_SIZE = 10
TEXT_LEADING = 12
TEXT_COLUMNS = 82
TEXT_LINES = 60
FONTS = {"F1": "Helvetica", "F2": "Helvetica-Bold", "F3": "Courier"}
SWATCHES = [
    ("Cyan", (0, 1, 1)), ("Magenta", (1, 0, 1)), ("Yellow", (1, 1, 0)), ("Black", (0, 0, 0)),
    ("Red", (1, 0, 0)), ("Green", (0, 1, 0)), ("Blue", (0, 0, 1)),
]


def _escape(text):
    encoded = text.encode("cp1252", "replace")
    return encoded.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def _write(pages):
    """pages: (width, height, content, image or None). Images are (data, w, h, components)."""
    objects = {1: b"<< /Type /Catalog /Pages 2 0 R >>"}
    number = 3
    fonts = []
    for key, name in FONTS.items():
        objects[number] = (
            "<< /Type /Font /Subtype /Type1 /BaseFont /%s /Encoding /WinAnsiEncoding >>" % name
        ).encode()
        fonts.append("/%s %d 0 R" % (key, number))
        number += 1
    kids = []
    for width, height, content, image in pages:
        page, stream = number, number + 1
        number += 2
        xobject = ""
        if image:
            data, pixels_wide, pixels_high, components = image
            space = "/DeviceGray" if components == 1 else "/DeviceRGB"
            objects[number] = _stream(
                "/Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace %s "
                "/BitsPerComponent 8 /Filter /DCTDecode" % (pixels_wide, pixels_high, space),
                data,
            )
            xobject = "/XObject << /Im0 %d 0 R >>" % number
            number += 1
        objects[stream] = _stream("", content)
        objects[page] = (
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %g %g] /Contents %d 0 R "
            "/Resources << /Font << %s >> %s >> >>" % (width, height, stream, " ".join(fonts), xobject)
        ).encode()
        kids.append("%d 0 R" % page)
    objects[2] = ("<< /Type /Pages /Kids [%s] /Count %d >>" % (" ".join(kids), len(kids))).encode()

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = {}
    for key in sorted(objects):
        offsets[key] = len(out)
        out += b"%d 0 obj\n" % key + objects[key] + b"\nendobj\n"
    table = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (number,)
    for key in range(1, number):
        out += b"%010d 00000 n \n" % offsets[key]
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (number, table)
    return bytes(out)


def _stream(dictionary, data):
    head = ("<< %s /Length %d >>\nstream\n" % (dictionary, len(data))).encode()
    return head + data + b"\nendstream"


def jpeg_size(data):
    """Return (width, height, components) from a JPEG's frame header."""
    pos = 2
    while pos + 9 < len(data):
        if data[pos] != 0xFF:
            pos += 1
            continue
        marker = data[pos + 1]
        if marker in (0xFF, 0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            pos += 1 if marker == 0xFF else 2
            continue
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            height, width, components = struct.unpack(">HHB", data[pos + 5 : pos + 10])
            return width, height, components
        pos += 2 + struct.unpack(">H", data[pos + 2 : pos + 4])[0]
    raise ValueError("This scan isn't a readable JPEG")


def from_jpegs(pages):
    """One PDF page per scan, at its real size. pages: (JPEG bytes, dpi)."""
    built = []
    for data, dpi in pages:
        wide, high, components = jpeg_size(data)
        width, height = wide * 72.0 / dpi, high * 72.0 / dpi
        content = ("q %.3f 0 0 %.3f 0 0 cm /Im0 Do Q" % (width, height)).encode()
        built.append((width, height, content, (data, wide, high, components)))
    return _write(built)


def from_text(text):
    lines = []
    for raw in text.replace("\r\n", "\n").replace("\t", "    ").split("\n"):
        lines += textwrap.wrap(raw, TEXT_COLUMNS, drop_whitespace=False) or [""]
    pages = []
    for start in range(0, max(len(lines), 1), TEXT_LINES):
        out = bytearray(b"BT /F3 %d Tf %d TL %d %d Td\n" % (TEXT_SIZE, TEXT_LEADING, MARGIN, A4[1] - MARGIN))
        for line in lines[start : start + TEXT_LINES]:
            out += b"(" + _escape(line) + b") '\n"
        out += b"ET"
        pages.append((A4[0], A4[1], bytes(out), None))
    return _write(pages)


def _label(x, y, size, font, text):
    return b"BT /%s %d Tf %d %d Td (" % (font.encode(), size, x, y) + _escape(text) + b") Tj ET\n"


def test_page(details):
    """A page that shows the colours, tones and fine lines work. details: (label, value) pairs."""
    width, height = A4
    out = bytearray(b"0.5 w 28 28 %d %d re S\n" % (width - 56, height - 56))
    out += _label(MARGIN, height - 90, 26, "F2", "LibrePrint test page")
    y = height - 125
    for label, value in details:
        out += _label(MARGIN, y, 11, "F2", label)
        out += _label(MARGIN + 90, y, 11, "F1", value)
        y -= 17

    y -= 30
    out += _label(MARGIN, y, 11, "F2", "Colours")
    y -= 75
    swatch = (width - 2 * MARGIN) // len(SWATCHES)
    for index, (name, (r, g, b)) in enumerate(SWATCHES):
        x = MARGIN + index * swatch
        out += b"%d %d %d rg %d %d %d 60 re f\n0 g\n" % (r, g, b, x, y, swatch - 6)
        out += _label(x, y - 13, 8, "F1", name)

    y -= 45
    out += _label(MARGIN, y, 11, "F2", "Greys")
    y -= 45
    step = (width - 2 * MARGIN) // 11
    for index in range(11):
        out += b"%.1f g %d %d %d 35 re f\n" % (index / 10.0, MARGIN + index * step, y, step)
    out += b"0 G 0 g\n"

    y -= 30
    out += _label(MARGIN, y, 11, "F2", "Fine lines")
    y -= 70
    for index, weight in enumerate((0.25, 0.5, 1, 2)):
        for line in range(5):
            x = MARGIN + index * 120 + line * 12
            out += b"%.2f w %d %d m %d %d l S\n" % (weight, x, y, x, y + 55)

    y -= 30
    out += _label(MARGIN, y, 11, "F2", "Text")
    for size in (6, 8, 10, 12):
        y -= size + 8
        out += _label(MARGIN, y, size, "F1", "The quick brown fox jumps over the lazy dog 0123456789")
    return _write([(width, height, bytes(out), None)])
