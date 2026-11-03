"""Bridge between the QML interface and the IPP client."""
import glob
import hashlib
import json
import math
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import uuid
from urllib.parse import unquote, urlsplit

import discovery
import ipp
import office
import pdfmake
import scan

try:
    import pyotherside
except ImportError:  # Only present inside the app.
    pyotherside = None

APP_ID = "libreprint.juujbyg"
HOME = os.path.expanduser("~")
CONFIG_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME", HOME + "/.config"), APP_ID)
CACHE_DIR = os.path.join(os.environ.get("XDG_CACHE_HOME", HOME + "/.cache"), APP_ID)
SAVED = os.path.join(CONFIG_DIR, "printers.json")
DATA_DIR = os.path.join(os.environ.get("XDG_DATA_HOME", HOME + "/.local/share"), APP_ID)
SCANS = os.path.join(CACHE_DIR, "scans")
EXPORTS = os.path.join(CACHE_DIR, "exports")
DOCUMENTS = os.path.join(CACHE_DIR, "documents")
INCOMING = os.path.join(CACHE_DIR, "HubIncoming")
OFFICE_ROOT = os.path.join(DATA_DIR, "office")
OFFICE_WORK = os.path.join(CACHE_DIR, "office")
# Not the mimetypes module: it reads /etc/mime.types, which the sandbox denies.
MIME_TYPES = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".webp": "image/webp",
    ".pwg": "image/pwg-raster",
    ".urf": "image/urf",
    ".txt": "text/plain",
}
PROBE_PATHS = ["/ipp/print", "/ipp/printer", "/ipp", "/"]
PDF = "application/pdf"
RASTER = "image/pwg-raster"
JPEG = "image/jpeg"
# A printer listing this will happily take anything, then print it as garbage.
UNKNOWN = "application/octet-stream"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEDIA_SIZE = re.compile(r"_(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)(mm|in)$")
RENDER_ERRORS = {
    2: "This PDF couldn't be opened",
    3: "This PDF is password protected",
    4: "There isn't enough space to prepare this PDF",
    5: "Those pages aren't in this PDF",
}
REASONS = {
    "media-empty": "Out of paper",
    "media-needed": "Out of paper",
    "media-jam": "Paper jam",
    "door-open": "A door is open",
    "cover-open": "The cover is open",
    "input-tray-missing": "The paper tray is missing",
    "marker-supply-low": "Ink is running low",
    "marker-supply-empty": "Out of ink",
    "toner-low": "Toner is running low",
    "toner-empty": "Out of toner",
    "spool-area-full": "The printer's memory is full",
    "offline": "The printer is offline",
    "paused": "The printer is paused",
}
REASON_SUFFIX = re.compile(r"-(report|warning|error)$")
PAGE_RANGES = re.compile(r"\d+(-\d+)?(,\d+(-\d+)?)*")
TEXT = "text/plain"
RENDER_TIMEOUT = 600
# Limits on what a printer can ask for, matching those in pdf2pwg.
MAX_MEDIA = 200000
MAX_DPI = 2400
# A printer reads a raster job as it prints, so sending can stall between pages.
SEND_TIMEOUT = 300
CANCEL_TIMEOUT = 5
PREVIEW_WIDTH = 900
PREVIEW_TIMEOUT = 60
# How long to keep retrying while the printer finishes the previous document.
BUSY_WAIT = 600
BUSY_RETRY = 5
INCOMING_AGE = 86400
A4_INCHES = (8.27, 11.69)
PAGE_NUMBERS = 8  # Four corners, each an x and a y.
DOCSCAN_TIMEOUT = 120
PHOTO_MODES = ("colour", "grey", "text")


def _guard(function):
    """Turn exceptions into a message the interface can show."""

    def wrapper(*args):
        try:
            return {"ok": True, "result": function(*args)}
        except (ipp.IppError, scan.ScanError, office.OfficeError, OSError, ValueError) as error:
            return {"ok": False, "error": str(error)}

    return wrapper


def _notify(event, *values):
    if pyotherside is not None:
        pyotherside.send(event, *values)


def run(request, name, arguments):
    """Run a call on its own thread. The bridge handles one call at a time, so a long
    one left on it would freeze every other call, including the printer's status."""

    def work():
        try:
            reply = globals()[name](*arguments)
        except Exception as error:  # Nothing else would reach the interface from here.
            reply = {"ok": False, "error": "Internal error: %r" % error}
        _notify("reply", request, reply)

    threading.Thread(target=work, daemon=True).start()


class Cancelled(Exception):
    pass


class _Task:
    """What is printing right now, so it can be stopped from another thread."""

    def __init__(self):
        self.cancelled = False
        self.process = None
        self.printer = None

    def check(self):
        if self.cancelled:
            raise Cancelled()

    def cancel(self):
        self.cancelled = True
        if self.process is not None:
            self.process.kill()

    def abort(self):
        if self.printer is not None:
            self.printer.abort()


_task = None


def _printer(data, timeout=15):
    # QML numbers reach Python as floats, and sockets reject a port like 631.0.
    return ipp.Printer(data["host"], int(data["port"]), data["path"], data["secure"], timeout)


def _read_saved():
    try:
        with open(SAVED, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return []


def _write_saved(printers):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(SAVED, "w", encoding="utf-8") as handle:
        json.dump(printers, handle)


def saved():
    return _read_saved()


def remember(printer):
    printers = [p for p in _read_saved() if p["host"] != printer["host"]]
    printers.append(dict(printer, port=int(printer["port"])))
    _write_saved(printers)
    return printers


def forget(host):
    printers = [p for p in _read_saved() if p["host"] != host]
    _write_saved(printers)
    return printers


@_guard
def find():
    known = {p["host"] for p in _read_saved()}
    return [p for p in discovery.discover() if p["host"] not in known]


def _candidates(address):
    text = address.strip()
    if "://" in text:
        parts = urlsplit(text)
        secure = parts.scheme in ("ipps", "https")
        return [(parts.hostname, parts.port or 631, parts.path or "/ipp/print", secure)]
    host, _, port = text.partition(":")
    port = int(port) if port else 631
    return [(host, port, path, secure) for secure in (True, False) for path in PROBE_PATHS]


@_guard
def connect(address):
    if not address.strip():
        raise ValueError("Enter the printer's address")
    last = None
    for host, port, path, secure in _candidates(address):
        printer = ipp.Printer(host, port, path, secure, timeout=5)
        try:
            attributes = printer.attributes()
        except (ipp.IppError, OSError) as error:
            last = error
            continue
        data = {
            "name": _first(attributes, "printer-make-and-model") or _first(attributes, "printer-name") or host,
            "host": host,
            "port": port,
            "path": path,
            "secure": secure,
        }
        remember(data)
        return data
    raise ipp.IppError("No printer answered at that address (%s)" % last)


def _first(attributes, name, default=None):
    values = attributes.get(name) or [default]
    return values[0]


def _ink(attributes):
    names = attributes.get("marker-names") or []
    levels = attributes.get("marker-levels") or []
    colors = attributes.get("marker-colors") or []
    lows = attributes.get("marker-low-levels") or []
    ink = []
    for index, name in enumerate(names):
        if index >= len(levels):
            break
        level = levels[index]
        low = lows[index] if index < len(lows) else 10
        known = isinstance(level, int) and level >= 0
        ink.append(
            {
                "name": name,
                "level": level if known else -1,
                "colors": colors[index].strip("#").split("#") if index < len(colors) and colors[index] else [],
                "low": known and level <= low,
            }
        )
    return ink


def _reasons(values):
    """Turn keywords like media-empty-error into sentences, without repeats."""
    readable = []
    for value in values:
        if not value or value == "none":
            continue
        key = REASON_SUFFIX.sub("", value)
        text = REASONS.get(key, key.replace("-", " ").capitalize())
        if text not in readable:
            readable.append(text)
    return readable


def _media_size(media):
    """Hundredths of a millimetre, from a name like iso_a4_210x297mm."""
    match = MEDIA_SIZE.search(media or "")
    if match is None:
        return None
    scale = 100 if match.group(3) == "mm" else 2540
    size = round(float(match.group(1)) * scale), round(float(match.group(2)) * scale)
    return size if all(0 < side <= MAX_MEDIA for side in size) else None


def _paper_name(media):
    """A4 from iso_a4_210x297mm. Names like na_letter_8.5x11in read fine the same way."""
    parts = (media or "").split("_")
    return parts[1].capitalize() if parts[1:] and parts[1] != "a4" else "A4" if parts[1:] else media


def _raster(attributes):
    """What the printer needs to take a rendered PDF, or None if it can't."""
    dpis = sorted({r[0] for r in attributes.get("pwg-raster-document-resolution-supported", []) if 0 < r[0] <= MAX_DPI})
    types = attributes.get("pwg-raster-document-type-supported", [])
    media = _first(attributes, "media-ready") or _first(attributes, "media-default")
    if not dpis or _media_size(media) is None or not {"sgray_8", "srgb_8"} & set(types):
        return None
    return {
        "dpis": dpis,
        "types": types,
        "media": media,
        "sheetBack": _first(attributes, "pwg-raster-document-sheet-back", "normal"),
        "faceUp": _first(attributes, "output-bin-default") == "face-up",
    }


def _jobs(printer):
    jobs = []
    for job in printer.jobs():
        jobs.append(
            {
                "id": _first(job, "job-id"),
                "name": _first(job, "job-name", ""),
                "state": ipp.JOB_STATES.get(_first(job, "job-state"), "unknown"),
                # Cancelled, but the printer is still clearing the paper path.
                "stopping": "processing-to-stop-point" in job.get("job-state-reasons", []),
            }
        )
    return jobs


@_guard
def quick_status(data):
    """State and queue only, which is what changes from one check to the next."""
    printer = _printer(data)
    attributes = printer.state()
    return {
        "state": ipp.PRINTER_STATES.get(_first(attributes, "printer-state"), "unknown"),
        "reasons": _reasons(attributes.get("printer-state-reasons", [])),
        "jobs": _jobs(printer),
    }


@_guard
def status(data):
    printer = _printer(data)
    attributes = printer.attributes()
    reasons = _reasons(attributes.get("printer-state-reasons", []))
    jobs = _jobs(printer)
    media = attributes.get("media-ready", [])
    return {
        "uri": printer.uri,
        "firmware": _first(attributes, "printer-firmware-string-version", ""),
        "paper": ", ".join(_paper_name(name) for name in media),
        "model": _first(attributes, "printer-make-and-model", data["name"]),
        "state": ipp.PRINTER_STATES.get(_first(attributes, "printer-state"), "unknown"),
        "reasons": reasons,
        "ink": _ink(attributes),
        "formats": attributes.get("document-format-supported", []),
        "sides": attributes.get("sides-supported", []),
        "colorModes": attributes.get("print-color-mode-supported", []),
        "qualities": attributes.get("print-quality-supported", []),
        "raster": _raster(attributes),
        "pageRanges": _first(attributes, "page-ranges-supported", False) is True,
        "jobs": jobs,
    }


def _path(url):
    return unquote(urlsplit(url).path) if url.startswith("file:") else url


def _mime(path):
    mime = MIME_TYPES.get(os.path.splitext(path)[1].lower())
    if mime is not None:
        return mime
    # Other apps can hand over a PDF with no extension, or a misleading one.
    try:
        with open(path, "rb") as handle:
            return PDF if handle.read(5) == b"%PDF-" else UNKNOWN
    except OSError:
        return UNKNOWN


def _helper(name):
    found = glob.glob(os.path.join(ROOT, "lib", "*", "bin", name))
    return found[0] if found else None


def _renderer():
    return _helper("pdf2pwg")


def plan(url, formats, raster=None):
    """Decide how a file reaches the printer: as is, rendered, via JPEG, or not at all."""
    path = _path(url)
    mime = _mime(path)
    route = {"path": path, "mime": mime, "name": os.path.basename(path)}
    reads_pdf = PDF in formats or bool(raster and _renderer())
    if mime in formats and mime != UNKNOWN:
        return dict(route, action="send")
    if mime in (PDF, TEXT) and reads_pdf:
        return dict(route, action="render", mime=PDF)
    if mime.startswith("image/") and JPEG in formats:
        return dict(route, action="convert", mime=JPEG)
    if mime == UNKNOWN and office.handles(path) and reads_pdf:
        return dict(route, action="office", mime=PDF)
    return dict(route, action="refuse")


def _helper_environment(helper):
    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = os.pathsep.join(
        filter(None, [os.path.dirname(os.path.dirname(helper)), env.get("LD_LIBRARY_PATH")])
    )
    return env


def _preview(path, page, target):
    """Draw one page of a PDF to an image and return how many pages the PDF has."""
    helper = _renderer()
    if helper is None:
        return None
    result = subprocess.run(
        [helper, "--preview", path, target, str(page), str(PREVIEW_WIDTH)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=PREVIEW_TIMEOUT,
        env=_helper_environment(helper),
    )
    if result.returncode != 0:
        raise ValueError(RENDER_ERRORS.get(result.returncode, "This PDF couldn't be read"))
    return int(result.stdout)


def _preview_name(path, page):
    return os.path.join(DOCUMENTS, "%s-%d.png" % (hashlib.sha256(path.encode()).hexdigest()[:16], page))


def _to_jpeg(path, target):
    """Printers that take photos mostly take only JPEG, and photos can be sideways."""
    helper = _renderer()
    if helper is None:
        raise ValueError("This version can't convert images")
    result = subprocess.run(
        [helper, "--jpeg", path, target],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=PREVIEW_TIMEOUT,
        env=_helper_environment(helper),
    )
    if result.returncode != 0:
        raise ValueError("This image couldn't be opened")


@_guard
def prepare(url, info):
    """Get a document ready to print, so problems show before anything is sent."""
    route = plan(url, info["formats"], info.get("raster"))
    action, path = route["action"], route["path"]
    if action == "refuse":
        return route
    if action == "office" and not office.installed(OFFICE_ROOT):
        return dict(route, action="office-missing")
    os.makedirs(DOCUMENTS, exist_ok=True)
    base = os.path.join(DOCUMENTS, uuid.uuid4().hex)
    if action == "office":
        path = office.to_pdf(OFFICE_ROOT, _helper("office2pdf"), path, OFFICE_WORK, base + ".pdf")
    elif action == "convert":
        path = base + ".jpg"
        _to_jpeg(route["path"], path)
    elif _mime(path) == TEXT and action == "render":
        with open(path, encoding="utf-8", errors="replace") as handle:
            path = base + ".pdf"
            _write(path, pdfmake.from_text(handle.read()))
    if route["mime"] == PDF:
        thumbnail = _preview_name(path, 1)
        pages = _preview(path, 1, thumbnail)
        return dict(route, action="pdf", path=path, pages=pages, thumbnail=thumbnail if pages else "")
    image = route["mime"].startswith("image/")
    return dict(route, action="send", path=path, pages=1 if image else None, thumbnail=path if image else "")


@_guard
def preview(path, page):
    """One page of a prepared PDF, as an image."""
    page = int(page)
    target = _preview_name(path, page)
    os.makedirs(DOCUMENTS, exist_ok=True)
    pages = _preview(path, page, target)
    return {"image": target, "pages": pages}


def _remove_old(folder, age):
    if not os.path.isdir(folder):
        return
    oldest = time.time() - age
    for name in os.listdir(folder):
        path = os.path.join(folder, name)
        if os.path.getmtime(path) < oldest:
            shutil.rmtree(path, ignore_errors=True)


def startup():
    """Clear what an earlier run left behind. Content Hub's folder keeps recent files,
    because a document shared with the app can arrive before this runs."""
    shutil.rmtree(DOCUMENTS, ignore_errors=True)
    _remove_old(INCOMING, INCOMING_AGE)
    return _read_saved()


def _send(data, path, mime, name, options, task=None):
    if mime == UNKNOWN:
        raise ValueError("This printer can't read that file type")
    with open(_path(path), "rb") as handle:
        document = handle.read()
    pages = _page_ranges(options.get("pages")) if mime == PDF else None
    printer = _printer(data, SEND_TIMEOUT)
    if task is not None:
        task.printer = printer
    return printer.print_document(
        document,
        mime,
        name,
        copies=int(options.get("copies", 1)),
        sides=options.get("sides") or None,
        color=options.get("color") or None,
        quality=options.get("quality") or None,
        pages=[[first, last] for first, last in pages] if pages else None,
    )


def _page_ranges(text):
    """Turn 1-3,5 into [(1, 3), (5, 5)], or None for every page."""
    text = (text or "").replace(" ", "")
    if not text:
        return None
    if not PAGE_RANGES.fullmatch(text):
        raise ValueError("Enter pages like 1-3,5")
    ranges = []
    for part in text.split(","):
        ends = [int(number) for number in part.split("-")]
        if ends[0] < 1 or ends[-1] < ends[0]:
            raise ValueError("Enter pages like 1-3,5")
        ranges.append((ends[0], ends[-1]))
    return ranges


def _render(raster, path, options, task=None, report=lambda page, pages: None):
    task = task or _Task()
    helper = _renderer()
    if helper is None:
        raise ValueError("This version can't print PDFs on this printer")
    dpis = [int(dpi) for dpi in raster["dpis"]]
    usable = [dpi for dpi in dpis if dpi >= 300] or dpis
    dpi = usable[-1] if int(options.get("quality") or 0) == 5 else usable[0]
    gray = options.get("color") == "monochrome" and "sgray_8" in raster["types"]
    kind = "sgray_8" if gray or "srgb_8" not in raster["types"] else "srgb_8"
    sides = options.get("sides") or "one-sided"
    reverse = raster.get("faceUp") and sides == "one-sided"
    width, height = _media_size(raster["media"])
    os.makedirs(CACHE_DIR, exist_ok=True)
    target = os.path.join(CACHE_DIR, "converted-%s.pwg" % uuid.uuid4().hex)
    arguments = [
        helper, _path(path), target, dpi, width, height, kind, sides, raster["sheetBack"],
        int(options.get("copies", 1)), int(options.get("quality") or 0), raster["media"],
        "reverse" if reverse else "forward",
        (options.get("pages") or "").replace(" ", ""),
    ]
    process = task.process = subprocess.Popen(
        [str(argument) for argument in arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env=_helper_environment(helper),
    )
    timer = threading.Timer(RENDER_TIMEOUT, process.kill)
    timer.start()
    try:
        for line in process.stdout:
            words = line.split()
            if len(words) == 3 and words[0] == "page":
                report(int(words[1]), int(words[2]))
        code = process.wait()
        expired = not timer.is_alive()
    finally:
        timer.cancel()
        task.process = None
    if (code != 0 or task.cancelled) and os.path.exists(target):
        os.remove(target)
    task.check()
    if code != 0:
        if expired:
            raise ValueError("This PDF took too long to prepare")
        raise ValueError(RENDER_ERRORS.get(code, "This PDF couldn't be prepared for printing"))
    return target


def _print_document(data, info, source, name, options, task=None, report=lambda page, pages: None, retry=lambda send: send()):
    """Print a PDF on disk, rendering it first if the printer can't read PDFs."""
    if PDF in info["formats"]:
        return retry(lambda: _send(data, source, PDF, name, options, task))
    target = _render(info["raster"], source, options, task, report)
    try:
        with open(target, "rb") as handle:
            document = handle.read()
    finally:
        if os.path.exists(target):
            os.remove(target)
    printer = _printer(data, SEND_TIMEOUT)
    if task is not None:
        task.printer = printer
    return retry(lambda: printer.print_document(
        document,
        RASTER,
        name,
        sides=options.get("sides") or None,
        color=options.get("color") or None,
        quality=options.get("quality") or None,
        media=info["raster"]["media"],
    ))


def _write(path, data):
    with open(path, "wb") as handle:
        handle.write(data)


def _while_busy(task, report, send):
    """A printer stays busy with one job until it has printed it, so the next one waits."""
    deadline = time.monotonic() + BUSY_WAIT
    while True:
        try:
            return send()
        except ipp.IppError as error:
            if error.status != ipp.STATUS_BUSY or time.monotonic() > deadline:
                raise
        report("waiting")
        for _ in range(BUSY_RETRY):
            task.check()
            time.sleep(1)


@_guard
def print_documents(data, info, documents, options):
    """Print prepared documents one after another, reporting how far it has got."""
    global _task
    _page_ranges(options.get("pages"))
    task = _task = _Task()
    sent = 0
    try:
        for index, document in enumerate(documents):
            task.check()
            name = document["name"]

            def report(stage, page=0, pages=0):
                _notify("progress", {
                    "name": name, "index": index, "total": len(documents),
                    "stage": stage, "page": page, "pages": pages,
                })

            def rendered(page, pages):
                report("preparing", page, pages)

            def retry(send):
                report("sending")
                return _while_busy(task, report, send)

            try:
                if document["action"] == "pdf":
                    _print_document(data, info, document["path"], name, options, task, rendered, retry)
                else:
                    retry(lambda: _send(data, document["path"], document["mime"], name, options, task))
            except ipp.IppError as error:
                if len(documents) == 1:
                    raise
                raise ipp.IppError("%s: %s" % (name, error))
            sent += 1
    except Cancelled:
        return {"sent": sent, "cancelled": True}
    except (ipp.IppError, OSError) as error:
        # Cancelling a job drops the connection that was still sending it.
        if task.cancelled:
            return {"sent": sent, "cancelled": True}
        if isinstance(error, TimeoutError):
            raise ipp.IppError("The printer stopped responding. Check it, then print again")
        raise
    finally:
        _task = None
    return {"sent": sent, "cancelled": False}


@_guard
def print_test_page(data, info):
    """Print a page that shows colours, tones and fine lines."""
    if PDF not in info["formats"] and not (info.get("raster") and _renderer()):
        raise ValueError("This printer can't print the test page")
    details = [
        ("Printer", info.get("model") or data["name"]),
        ("Address", data["host"]),
        ("Printed", time.strftime("%Y-%m-%d %H:%M")),
    ]
    for ink in info["ink"]:
        level = "unknown" if ink["level"] < 0 else "%d%%" % ink["level"]
        details.append(("Ink", "%s: %s" % (ink["name"], level)))
    os.makedirs(CACHE_DIR, exist_ok=True)
    source = os.path.join(CACHE_DIR, "testpage.pdf")
    _write(source, pdfmake.test_page(details))
    return _print_document(data, info, source, "LibrePrint test page", {"copies": 1})


def _scan_path(name):
    return os.path.join(SCANS, os.path.basename(name))


def _photo_dpi(wide, high):
    """The resolution at which a photo just fits an A4 page, so it comes out like a scan."""
    short, long = sorted((wide, high))
    return max(1, math.ceil(max(short / A4_INCHES[0], long / A4_INCHES[1])))


def _scan_dpi(name):
    return int(name.rsplit(".", 1)[0].rsplit("-", 1)[1])


@_guard
def scans():
    """Pages scanned so far, oldest first."""
    if not os.path.isdir(SCANS):
        return []
    return [_scan_path(name) for name in sorted(os.listdir(SCANS))]


@_guard
def scanner(host):
    return scan.find(host)


@_guard
def scan_page(found, dpi, color):
    data = scan.scan_page(found, int(dpi), color)
    os.makedirs(SCANS, exist_ok=True)
    with open(_scan_path("page-%d-%d.jpg" % (time.time() * 1000, int(dpi))), "wb") as handle:
        handle.write(data)
    return scans()["result"]


def _keep_page(work):
    """Move a finished picture in with the scanned pages."""
    os.makedirs(SCANS, exist_ok=True)
    try:
        with open(work, "rb") as handle:
            wide, high, _ = pdfmake.jpeg_size(handle.read())
        dpi = _photo_dpi(wide, high)
        os.replace(work, _scan_path("page-%d-%d.jpg" % (time.time() * 1000, dpi)))
    finally:
        if os.path.exists(work):
            os.remove(work)
    return scans()["result"]


def _photo_path():
    return os.path.join(CACHE_DIR, "photo-%s.jpg" % uuid.uuid4().hex)


@_guard
def add_photo(url):
    """Add a photo as a scanned page, upright, since a PDF ignores the turn a camera records."""
    work = _photo_path()
    try:
        _to_jpeg(_path(url), work)
    except ValueError:
        if os.path.exists(work):
            os.remove(work)
        raise
    return _keep_page(work)


def _run_docscan(*arguments):
    helper = _helper("docscan")
    if helper is None:
        raise ValueError("This version can't clean up photos")
    try:
        return subprocess.run(
            [helper, *arguments],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            universal_newlines=True,
            timeout=DOCSCAN_TIMEOUT,
            env=_helper_environment(helper),
        )
    except subprocess.TimeoutExpired as error:
        raise ValueError("This photo took too long to clean up") from error


def _held_photo(path):
    """Only ever touch photos this app put in its own cache."""
    name = os.path.basename(path)
    if not name.startswith("photo-"):
        raise ValueError("That isn't a photo waiting to be added")
    return os.path.join(CACHE_DIR, name)


@_guard
def prepare_photo(url):
    """Make a photo upright and find the page in it, ready for the crop screen."""
    for old in glob.glob(os.path.join(CACHE_DIR, "photo-*.jpg")):
        os.remove(old)
    work = _photo_path()
    try:
        _to_jpeg(_path(url), work)
        result = _run_docscan("--detect", work)
        values = [float(value) for value in result.stdout.split()]
        if result.returncode != 0 or len(values) != PAGE_NUMBERS + 1:
            raise ValueError("This image couldn't be opened")
    except ValueError:
        if os.path.exists(work):
            os.remove(work)
        raise
    return {"path": work, "corners": values[:PAGE_NUMBERS], "found": values[PAGE_NUMBERS] == 1}


@_guard
def finish_photo(path, corners, mode):
    """Add the page from a prepared photo, straightened and cleaned unless kept as it is."""
    source = _held_photo(path)
    if mode == "original":
        return _keep_page(source)
    if mode not in PHOTO_MODES:
        raise ValueError("Unknown picture mode")
    numbers = [float(value) for value in corners]
    if len(numbers) != PAGE_NUMBERS or not all(0 <= value <= 1 for value in numbers):
        raise ValueError("The page corners aren't valid")
    work = _photo_path()
    try:
        result = _run_docscan("--warp", source, work, mode, *["%.5f" % value for value in numbers])
        if result.returncode != 0:
            raise ValueError("This photo couldn't be cleaned up. Try moving the corners")
    except ValueError:
        if os.path.exists(work):
            os.remove(work)
        raise
    pages = _keep_page(work)
    os.remove(source)
    return pages


@_guard
def discard_photo(path):
    """Forget a prepared photo that wasn't added. It's already gone if it was."""
    source = _held_photo(path)
    if os.path.exists(source):
        os.remove(source)


@_guard
def remove_scan(path):
    os.remove(_scan_path(path))
    return scans()["result"]


@_guard
def clear_scans():
    for path in scans()["result"]:
        os.remove(path)
    return []


@_guard
def export_scans(kind):
    """Write the scans for handing to another app: one PDF, or one JPEG per page."""
    paths = scans()["result"]
    if not paths:
        raise ValueError("There are no scanned pages yet")
    os.makedirs(EXPORTS, exist_ok=True)
    for old in os.listdir(EXPORTS):
        os.remove(os.path.join(EXPORTS, old))
    stamp = time.strftime("scan-%Y%m%d-%H%M%S")
    pages = []
    for path in paths:
        with open(path, "rb") as handle:
            pages.append((handle.read(), _scan_dpi(os.path.basename(path))))
    if kind == "pdf":
        target = os.path.join(EXPORTS, stamp + ".pdf")
        with open(target, "wb") as handle:
            handle.write(pdfmake.from_jpegs(pages))
        return [target]
    targets = []
    for index, (data, _) in enumerate(pages, 1):
        target = os.path.join(EXPORTS, "%s-%d.jpg" % (stamp, index))
        with open(target, "wb") as handle:
            handle.write(data)
        targets.append(target)
    return targets


@_guard
def office_state():
    return {
        "available": office.available(),
        "installed": office.installed(OFFICE_ROOT),
        "version": office.VERSION,
        "size": office.SIZE_MB,
    }


@_guard
def office_install():
    office.install(OFFICE_ROOT, lambda stage, fraction: _notify("office", stage, fraction))
    return office_state()["result"]


@_guard
def office_remove():
    office.remove(OFFICE_ROOT)
    shutil.rmtree(OFFICE_WORK, ignore_errors=True)
    return office_state()["result"]


@_guard
def cancel(data, job_id):
    job_id = int(job_id)
    _printer(data).cancel(job_id)
    return job_id


@_guard
def cancel_all(data):
    """Stop everything. The printer hears first, because dropping the connection alone
    would leave it holding half a job. The drop still has to happen, or a printer that
    stopped answering would keep the job sending until it timed out."""
    task = _task
    if task is not None:
        task.cancel()
    printer = _printer(data, CANCEL_TIMEOUT)
    try:
        jobs = [_first(job, "job-id") for job in printer.jobs()]
        for job_id in jobs:
            printer.cancel(job_id)
        return len(jobs)
    except (ipp.IppError, OSError):
        if task is None:
            raise
        return 0
    finally:
        if task is not None:
            task.abort()


def _check(title, state, detail):
    return {"title": title, "state": state, "detail": detail}


def _local_address(host):
    """The phone's address on the way to the printer. UDP connect sends nothing."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.connect((host, 631))
        return probe.getsockname()[0]


def _check_helper():
    helper = _renderer()
    if helper is None:
        return _check("PDF rendering", "fail", "The PDF renderer is missing from this install")
    env = dict(os.environ, LD_LIBRARY_PATH=os.path.dirname(os.path.dirname(helper)))
    code = subprocess.run([helper], stderr=subprocess.DEVNULL, timeout=10, env=env).returncode
    if code == 1:
        return _check("PDF rendering", "ok", "The PDF renderer runs")
    return _check("PDF rendering", "fail", "The PDF renderer failed to start (%d)" % code)


@_guard
def diagnose(data):
    """Work through what printing needs, from the network up to the printer."""
    checks = []
    try:
        checks.append(_check("Network", "ok", "This phone is %s" % _local_address(data["host"])))
    except OSError as error:
        checks.append(_check("Network", "fail", "No route to the printer: %s" % error))

    found = [p["host"] for p in discovery.discover(2.0)]
    if data["host"] in found:
        checks.append(_check("Finding printers", "ok", "The printer answers network searches"))
    else:
        checks.append(_check("Finding printers", "warn", "The printer didn't answer a network search. Printing by address can still work"))

    try:
        attributes = _printer(data).attributes()
    except (ipp.IppError, OSError) as error:
        checks.append(_check("Printer", "fail", "No answer at %s: %s" % (data["host"], error)))
        return checks
    state = ipp.PRINTER_STATES.get(_first(attributes, "printer-state"), "unknown")
    checks.append(_check("Printer", "ok" if state != "stopped" else "fail", "%s is %s" % (_first(attributes, "printer-make-and-model", data["name"]), state)))
    problems = _reasons(attributes.get("printer-state-reasons", []))
    checks.append(_check("Printer messages", "warn" if problems else "ok", ", ".join(problems) or "No problems reported"))

    formats = attributes.get("document-format-supported", [])
    if PDF in formats:
        checks.append(_check("PDF printing", "ok", "The printer reads PDFs itself"))
    elif _raster(attributes):
        checks.append(_check("PDF printing", "ok", "PDFs are rendered on the phone first"))
        checks.append(_check_helper())
    else:
        checks.append(_check("PDF printing", "fail", "This printer accepts neither PDF nor PWG Raster"))

    if office.installed(OFFICE_ROOT):
        checks.append(_check("Office documents", "ok", "LibreOffice is ready to prepare Word, Excel and PowerPoint files"))
    else:
        checks.append(_check("Office documents", "warn", "LibreOffice isn't installed, so Office files can't print yet"))

    found_scanner, note = scan.survey(data["host"])
    if found_scanner:
        how = "encrypted" if found_scanner["secure"] else "unencrypted"
        kind = "document feeder" if found_scanner["source"] == "Feeder" else "flatbed scanner"
        checks.append(_check("Scanner", "ok", "Found a %s, %s connection, on port %d" % (kind, how, found_scanner["port"])))
    else:
        checks.append(_check("Scanner", "warn", note))
    return checks
