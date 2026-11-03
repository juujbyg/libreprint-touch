import http.server
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "src"))
sys.path.insert(0, HERE)

import backend  # noqa: E402
import discovery  # noqa: E402
import fake_printer  # noqa: E402
import ipp  # noqa: E402
import pdfmake  # noqa: E402
import scan  # noqa: E402


def name(text):
    return discovery._encode_name(text)


def record(owner, rtype, rdata):
    return name(owner) + struct.pack(">HHIH", rtype, 1, 120, len(rdata)) + rdata


def packet(*records):
    return struct.pack(">HHHHHH", 0, 0x8400, 0, len(records), 0, 0) + b"".join(records)


HP_ATTRIBUTES = {
    "pwg-raster-document-resolution-supported": [[300, 300, 3], [600, 600, 3]],
    "pwg-raster-document-type-supported": ["sgray_8", "srgb_8", "adobe-rgb_16"],
    "pwg-raster-document-sheet-back": ["rotated"],
    "media-ready": ["iso_a4_210x297mm"],
    "output-bin-default": ["face-up"],
}


class PdfRendering(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.raster = backend._raster(HP_ATTRIBUTES)
        self.log = os.path.join(self.folder.name, "args")
        helper = os.path.join(self.folder.name, "pdf2pwg")
        with open(helper, "w") as handle:
            handle.write('#!/bin/sh\necho "$@" > %s\necho raster > "$2"\nexit ${HELPER_EXIT:-0}\n' % self.log)
        os.chmod(helper, 0o755)
        real = backend._renderer, backend.CACHE_DIR
        backend._renderer, backend.CACHE_DIR = (lambda: helper), self.folder.name
        self.addCleanup(lambda: (setattr(backend, "_renderer", real[0]), setattr(backend, "CACHE_DIR", real[1])))

    def arguments(self, **options):
        backend._render(self.raster, "/t/a.pdf", options)
        with open(self.log) as handle:
            return handle.read().split()[2:]

    def test_media_size(self):
        self.assertEqual(backend._media_size("iso_a4_210x297mm"), (21000, 29700))
        self.assertEqual(backend._media_size("na_letter_8.5x11in"), (21590, 27940))
        self.assertIsNone(backend._media_size("custom"))

    def test_absurd_page_sizes_and_resolutions_are_refused(self):
        self.assertIsNone(backend._media_size("custom_x_99999999x99999999mm"))
        self.assertIsNone(backend._media_size("custom_x_0x297mm"))
        attributes = dict(HP_ATTRIBUTES, **{"pwg-raster-document-resolution-supported": [(2147483647, 2147483647)]})
        self.assertIsNone(backend._raster(attributes))

    def test_printer_without_raster_details_gets_none(self):
        self.assertIsNone(backend._raster({"media-ready": ["iso_a4_210x297mm"]}))

    def test_plan_renders_pdf_only_when_it_has_to(self):
        hp = ["image/jpeg", "image/pwg-raster", "application/octet-stream"]
        self.assertEqual(backend.plan("/t/a.pdf", hp, self.raster)["action"], "render")
        self.assertEqual(backend.plan("/t/a.pdf", hp + ["application/pdf"], self.raster)["action"], "send")
        self.assertEqual(backend.plan("/t/a.pdf", hp, None)["action"], "refuse")

    def test_one_sided_goes_last_page_first_on_a_face_up_tray(self):
        self.assertEqual(
            self.arguments(copies=1.0),
            "300 21000 29700 srgb_8 one-sided rotated 1 0 iso_a4_210x297mm reverse".split(),
        )

    def test_two_sided_keeps_page_order(self):
        arguments = self.arguments(copies=2.0, sides="two-sided-long-edge", color="monochrome", quality=5.0)
        self.assertEqual(
            arguments,
            "600 21000 29700 sgray_8 two-sided-long-edge rotated 2 5 iso_a4_210x297mm forward".split(),
        )

    def test_helper_failure_becomes_a_message(self):
        os.environ["HELPER_EXIT"] = "3"
        self.addCleanup(os.environ.pop, "HELPER_EXIT")
        with self.assertRaisesRegex(ValueError, "password"):
            backend._render(self.raster, "/t/a.pdf", {})


class PrinterErrors(unittest.TestCase):
    def test_busy_printer_gets_a_readable_message(self):
        printer = ipp.Printer("127.0.0.1")
        printer._post = lambda body: bytes([2, 0, 0x05, 0x07, 0, 0, 0, 1, ipp.TAG_END])
        with self.assertRaisesRegex(ipp.IppError, "busy"):
            printer.attributes()


JPEG = bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffc0000b08000a000a01011100ffd9")
CAPABILITIES = """<scan:ScannerCapabilities><scan:Platen><scan:PlatenInputCaps>
<scan:MaxWidth>2550</scan:MaxWidth><scan:MaxHeight>3508</scan:MaxHeight>
<scan:ColorMode>Grayscale8</scan:ColorMode><scan:ColorMode>RGB24</scan:ColorMode>
<pwg:DocumentFormat>image/jpeg</pwg:DocumentFormat>
<scan:XResolution>150</scan:XResolution><scan:XResolution>300</scan:XResolution>
</scan:PlatenInputCaps></scan:Platen></scan:ScannerCapabilities>"""


class FakeScanner:
    def __init__(self):
        scanner = self
        self.settings = []
        self.busy_reads = 1
        self.pages_left = 1

        class Handler(http.server.BaseHTTPRequestHandler):
            def reply(self, status, body=b"", location=None):
                self.send_response(status)
                if location:
                    self.send_header("Location", location)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                scanner.settings.append(self.rfile.read(int(self.headers["Content-Length"])).decode())
                self.reply(201, location="http://127.0.0.1/eSCL/ScanJobs/1")

            def do_GET(self):
                if self.path.endswith("ScannerCapabilities"):
                    self.reply(200, CAPABILITIES.encode())
                elif scanner.busy_reads:
                    scanner.busy_reads -= 1
                    self.reply(503)
                elif scanner.pages_left:
                    scanner.pages_left -= 1
                    self.reply(200, JPEG)
                else:
                    self.reply(404)

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.port = self.server.server_address[1]


class Scanning(unittest.TestCase):
    def setUp(self):
        self.fake = FakeScanner()
        self.addCleanup(self.fake.server.shutdown)
        self.addCleanup(self.fake.server.server_close)
        real = scan.ENDPOINTS
        scan.ENDPOINTS = [(False, self.fake.port)]
        self.addCleanup(setattr, scan, "ENDPOINTS", real)
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        before = backend.SCANS, backend.EXPORTS
        backend.SCANS = os.path.join(self.folder.name, "scans")
        backend.EXPORTS = os.path.join(self.folder.name, "exports")
        self.addCleanup(lambda: (setattr(backend, "SCANS", before[0]), setattr(backend, "EXPORTS", before[1])))

    def test_find_reads_capabilities(self):
        found = scan.find("127.0.0.1")
        self.assertEqual((found["dpis"], found["width"], found["height"]), ([150, 300], 2550, 3508))

    def test_scanners_that_describe_themselves_differently_are_understood(self):
        region = "<scan:MaxWidth>2550</scan:MaxWidth><scan:MaxHeight>3508</scan:MaxHeight>"
        modes = "<scan:ColorMode>RGB24</scan:ColorMode>"
        ranged = "<scan:XResolutionRange><scan:Min>100</scan:Min><scan:Max>600</scan:Max></scan:XResolutionRange>"
        shared = "<scan:SettingProfiles><pwg:DocumentFormat>image/jpeg</pwg:DocumentFormat></scan:SettingProfiles>"
        found = scan.parse_capabilities("<scan:Platen>%s%s%s</scan:Platen>%s" % (region, modes, ranged, shared))
        self.assertEqual(found["dpis"], [150, 300, 600])
        self.assertEqual(found["source"], "Platen")

    def test_a_scanner_with_only_a_feeder_is_used(self):
        feeder = (
            "<scan:Adf><scan:MaxWidth>2550</scan:MaxWidth><scan:MaxHeight>3508</scan:MaxHeight>"
            "<scan:ColorMode>Grayscale8</scan:ColorMode><scan:XResolution>200</scan:XResolution>"
            "<pwg:DocumentFormat>image/jpeg</pwg:DocumentFormat></scan:Adf>"
        )
        self.assertEqual(scan.parse_capabilities(feeder)["source"], "Feeder")

    def test_older_tls_is_allowed_for_printers(self):
        self.assertEqual(scan._old_printer_tls().minimum_version, scan.ssl.TLSVersion.MINIMUM_SUPPORTED)

    def test_no_flatbed_means_no_scanner(self):
        self.assertIsNone(scan.parse_capabilities("<scan:ScannerCapabilities/>"))

    def test_scan_waits_for_a_busy_scanner_and_uses_the_settings(self):
        page = scan.scan_page(scan.find("127.0.0.1"), 300.0, False)
        self.assertEqual(page, JPEG)
        self.assertIn("<scan:ColorMode>Grayscale8<", self.fake.settings[0])
        self.assertIn("<scan:XResolution>300<", self.fake.settings[0])

    def test_scans_save_as_one_pdf_or_one_jpeg_per_page(self):
        found = backend.scanner("127.0.0.1")["result"]
        for _ in range(2):
            self.fake.busy_reads, self.fake.pages_left = 0, 1
            self.assertTrue(backend.scan_page(found, 150.0, True)["ok"])
        self.assertEqual(len(backend.scans()["result"]), 2)
        self.assertEqual(len(backend.export_scans("jpeg")["result"]), 2)
        pdf = backend.export_scans("pdf")["result"]
        self.assertEqual(len(pdf), 1)
        self.assertTrue(pdf[0].endswith(".pdf"))

    def test_remove_stays_inside_the_scans_folder(self):
        outside = os.path.join(self.folder.name, "keep.txt")
        open(outside, "w").close()
        os.makedirs(backend.SCANS)
        self.assertFalse(backend.remove_scan("../keep.txt")["ok"])
        self.assertTrue(os.path.exists(outside))


@unittest.skipUnless(shutil.which("pdftotext"), "needs poppler-utils")
class MadePdfs(unittest.TestCase):
    def text_of(self, data):
        with tempfile.NamedTemporaryFile(suffix=".pdf") as handle:
            handle.write(data)
            handle.flush()
            return subprocess.run(
                ["pdftotext", "-layout", handle.name, "-"], capture_output=True, check=True, text=True
            ).stdout

    def test_text_keeps_awkward_characters_and_runs_over_pages(self):
        text = self.text_of(pdfmake.from_text("a (b) \\ caf\u00e9\n" + "line\n" * 70))
        self.assertIn("a (b) \\ caf\u00e9", text)
        self.assertEqual(text.count("\f"), 2)

    def test_test_page_shows_the_details(self):
        text = self.text_of(pdfmake.test_page([("Printer", "HP (test)")]))
        self.assertIn("HP (test)", text)
        self.assertIn("LibrePrint test page", text)

    def test_scan_page_is_the_real_size(self):
        data = pdfmake.from_jpegs([(JPEG, 72)])
        self.assertIn(b"/MediaBox [0 0 10 10]", data)


def sized_jpeg(wide, high):
    """Enough of a JPEG for jpeg_size: a frame header and nothing else."""
    return b"\xff\xd8\xff\xc0\x00\x11\x08" + struct.pack(">HHB", high, wide, 3) + bytes(12) + b"\xff\xd9"


class Photos(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.work = os.path.join(self.folder.name, "work")
        os.mkdir(self.work)
        helper = os.path.join(self.folder.name, "pdf2pwg")
        with open(helper, "w") as handle:
            handle.write('#!/bin/sh\n[ "$1" = --jpeg ] || exit 9\ncp "$2" "$3"\nexit ${HELPER_EXIT:-0}\n')
        os.chmod(helper, 0o755)
        before = backend._renderer, backend.CACHE_DIR, backend.SCANS, backend.EXPORTS
        backend._renderer, backend.CACHE_DIR = (lambda: helper), self.work
        backend.SCANS = os.path.join(self.folder.name, "scans")
        backend.EXPORTS = os.path.join(self.folder.name, "exports")
        self.addCleanup(
            lambda: (
                setattr(backend, "_renderer", before[0]), setattr(backend, "CACHE_DIR", before[1]),
                setattr(backend, "SCANS", before[2]), setattr(backend, "EXPORTS", before[3]),
            )
        )

    def photo(self, data, filename="my photo #1.jpg"):
        path = os.path.join(self.folder.name, filename)
        with open(path, "wb") as handle:
            handle.write(data)
        return "file://" + path.replace(" ", "%20").replace("#", "%23")

    def fake_docscan(self, corners="0.1 0.1 0.9 0.1 0.9 0.9 0.1 0.9 1", fails=False):
        helper = os.path.join(self.folder.name, "docscan")
        with open(helper, "w") as handle:
            handle.write(
                '#!/bin/sh\n[ -n "%s" ] && exit 4\ncase "$1" in\n--detect) echo "%s";;\n'
                '--warp) printf "%%s" "$*" > %s/call; cp "$2" "$3";;\nesac\n'
                % ("1" if fails else "", corners, self.folder.name)
            )
        os.chmod(helper, 0o755)
        if not hasattr(self, "real_helper"):
            self.real_helper = backend._helper
            self.addCleanup(setattr, backend, "_helper", self.real_helper)
        backend._helper = lambda name: helper if name == "docscan" else self.real_helper(name)

    def test_a_photo_is_prepared_with_the_corners_of_its_page(self):
        self.fake_docscan()
        reply = backend.prepare_photo(self.photo(sized_jpeg(2250, 3000)))
        self.assertTrue(reply["ok"], reply)
        self.assertTrue(reply["result"]["found"])
        self.assertEqual(reply["result"]["corners"], [0.1, 0.1, 0.9, 0.1, 0.9, 0.9, 0.1, 0.9])
        self.assertTrue(os.path.exists(reply["result"]["path"]))

    def test_no_page_found_still_offers_the_crop_screen(self):
        self.fake_docscan("0.05 0.05 0.95 0.05 0.95 0.95 0.05 0.95 0")
        self.assertFalse(backend.prepare_photo(self.photo(sized_jpeg(2250, 3000)))["result"]["found"])

    def test_finishing_cleans_the_photo_and_adds_one_page(self):
        self.fake_docscan()
        ready = backend.prepare_photo(self.photo(sized_jpeg(2250, 3000)))["result"]
        reply = backend.finish_photo(ready["path"], ready["corners"], "text")
        self.assertEqual(len(reply["result"]), 1)
        self.assertEqual(os.listdir(self.work), [])
        with open(os.path.join(self.folder.name, "call")) as handle:
            self.assertIn(" text 0.10000 0.10000 ", handle.read())

    def test_keeping_the_photo_as_it_is_skips_the_clean_up(self):
        self.fake_docscan()
        ready = backend.prepare_photo(self.photo(sized_jpeg(2250, 3000)))["result"]
        reply = backend.finish_photo(ready["path"], ready["corners"], "original")
        self.assertEqual(len(reply["result"]), 1)
        self.assertFalse(os.path.exists(os.path.join(self.folder.name, "call")))

    def test_bad_corners_and_modes_are_refused_and_the_photo_survives(self):
        self.fake_docscan()
        ready = backend.prepare_photo(self.photo(sized_jpeg(2250, 3000)))["result"]
        self.assertFalse(backend.finish_photo(ready["path"], [0.5] * 7, "grey")["ok"])
        self.assertFalse(backend.finish_photo(ready["path"], [2.0] * 8, "grey")["ok"])
        self.assertFalse(backend.finish_photo(ready["path"], ready["corners"], "sepia")["ok"])
        self.assertTrue(os.path.exists(ready["path"]))

    def test_a_failed_clean_up_keeps_the_photo_to_try_again(self):
        self.fake_docscan()
        ready = backend.prepare_photo(self.photo(sized_jpeg(2250, 3000)))["result"]
        self.fake_docscan(fails=True)
        self.assertFalse(backend.finish_photo(ready["path"], ready["corners"], "grey")["ok"])
        self.assertTrue(os.path.exists(ready["path"]))
        self.assertEqual(len(os.listdir(self.work)), 1)

    def test_only_the_apps_own_photos_can_be_touched(self):
        outside = self.photo(sized_jpeg(10, 10), "page.jpg")[len("file://"):]
        self.assertFalse(backend.discard_photo(outside)["ok"])
        self.assertTrue(os.path.exists(outside))
        self.assertFalse(backend.finish_photo("/etc/passwd", [0.1] * 8, "original")["ok"])

    def test_discarding_removes_a_prepared_photo(self):
        self.fake_docscan()
        ready = backend.prepare_photo(self.photo(sized_jpeg(2250, 3000)))["result"]
        self.assertTrue(backend.discard_photo(ready["path"])["ok"])
        self.assertEqual(os.listdir(self.work), [])

    def test_a_portrait_photo_becomes_a_page_that_fits_a4(self):
        reply = backend.add_photo(self.photo(sized_jpeg(2250, 3000)))
        self.assertTrue(reply["ok"], reply)
        (page,) = reply["result"]
        with open(page, "rb") as handle:
            pdf = pdfmake.from_jpegs([(handle.read(), backend._scan_dpi(os.path.basename(page)))])
        box = [float(v) for v in pdf.split(b"/MediaBox [0 0 ")[1].split(b"]")[0].split()]
        self.assertLessEqual(box[0], 595.5)
        self.assertGreater(box[0], 590)  # a 4:3 photo is wider than A4, so its width sets the size
        self.assertLessEqual(box[1], 842)

    def test_a_landscape_photo_fits_too(self):
        self.assertEqual(backend._photo_dpi(3000, 2000), backend._photo_dpi(2000, 3000))
        self.assertEqual(backend._photo_dpi(3000, 2000), 257)

    def test_a_small_photo_still_gets_a_usable_resolution(self):
        self.assertEqual(backend._photo_dpi(1, 1), 1)

    def test_photos_join_scans_in_order_and_export(self):
        first = backend.add_photo(self.photo(sized_jpeg(2250, 3000)))["result"]
        second = backend.add_photo(self.photo(sized_jpeg(3000, 2250)))["result"]
        self.assertEqual(second[0], first[0])
        self.assertEqual(len(second), 2)
        (target,) = backend.export_scans("pdf")["result"]
        with open(target, "rb") as handle:
            self.assertIn(b"/Count 2", handle.read())
        self.assertEqual(backend.remove_scan(second[0])["result"], second[1:])

    def test_a_broken_picture_leaves_nothing_behind(self):
        os.environ["HELPER_EXIT"] = "1"
        self.addCleanup(os.environ.pop, "HELPER_EXIT")
        reply = backend.add_photo(self.photo(sized_jpeg(100, 100)))
        self.assertFalse(reply["ok"])
        self.assertEqual(reply["error"], "This image couldn't be opened")
        self.assertEqual(backend.scans()["result"], [])
        self.assertEqual(os.listdir(self.work), [])

    def test_a_result_that_isnt_a_jpeg_is_refused(self):
        reply = backend.add_photo(self.photo(b"not a picture at all"))
        self.assertFalse(reply["ok"])
        self.assertEqual(backend.scans()["result"], [])
        self.assertEqual(os.listdir(self.work), [])


class PageRanges(unittest.TestCase):
    def test_ranges(self):
        self.assertEqual(backend._page_ranges(" 1-3, 5 "), [(1, 3), (5, 5)])
        self.assertIsNone(backend._page_ranges(""))

    def test_bad_ranges_are_refused(self):
        for text in ("a", "3-1", "0", "1,,2"):
            with self.assertRaises(ValueError):
                backend._page_ranges(text)

    def test_several_ranges_encode_as_additional_values(self):
        data = ipp._attributes(ipp.TAG_RANGE, "page-ranges", [[1, 3], [5, 5]])
        self.assertEqual(data.count(struct.pack(">ii", 1, 3)), 1)
        self.assertEqual(data.count(struct.pack(">ii", 5, 5)), 1)


class Reasons(unittest.TestCase):
    def test_reasons_read_as_sentences_without_repeats(self):
        values = ["media-empty-error", "media-needed-error", "none", "something-odd-report"]
        self.assertEqual(backend._reasons(values), ["Out of paper", "Something odd"])


class QmlNumbers(unittest.TestCase):
    """pyotherside hands every QML number to Python as a float."""

    def setUp(self):
        self.server, _ = fake_printer.serve()
        self.addCleanup(self.server.shutdown)
        self.data = {"name": "t", "host": "127.0.0.1", "port": float(self.server.server_address[1]),
                     "path": "/ipp/print", "secure": False}

    def test_status_send_cancel(self):
        self.assertTrue(backend.status(self.data)["ok"])
        options = {"copies": 2.0, "sides": "", "color": "", "quality": 5.0}
        with tempfile.NamedTemporaryFile(suffix=".pdf") as handle:
            handle.write(b"%PDF-1.4")
            handle.flush()
            job = backend._send(self.data, handle.name, "application/pdf", "t.pdf", options)
        cancelled = backend.cancel(self.data, float(job))
        self.assertTrue(cancelled["ok"], cancelled)

    def test_saved_port_is_an_integer(self):
        with tempfile.TemporaryDirectory() as folder:
            real = backend.CONFIG_DIR, backend.SAVED
            backend.CONFIG_DIR, backend.SAVED = folder, os.path.join(folder, "p.json")
            self.addCleanup(lambda: (setattr(backend, "CONFIG_DIR", real[0]), setattr(backend, "SAVED", real[1])))
            backend.remember(self.data)
            self.assertIs(type(backend.saved()[0]["port"]), int)


class StalledPrinter(unittest.TestCase):
    """A printer that accepts a connection, then stops reading, as the HP did when it dropped."""

    def setUp(self):
        self.listener = socket.create_server(("127.0.0.1", 0))
        self.addCleanup(self.listener.close)
        self.accepted = []
        threading.Thread(target=self.accept, daemon=True).start()
        self.port = self.listener.getsockname()[1]
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)

    def accept(self):
        while True:
            try:
                self.accepted.append(self.listener.accept()[0])
            except OSError:
                return

    def test_abort_wakes_a_stalled_send(self):
        printer = ipp.Printer("127.0.0.1", self.port, "/ipp/print", False, 300)
        failed = []

        def send():
            try:
                printer.print_document(b"x" * (64 << 20), "image/pwg-raster", "big")
            except (ipp.IppError, OSError) as error:
                failed.append(error)

        worker = threading.Thread(target=send, daemon=True)
        worker.start()
        worker.join(1)
        self.assertTrue(worker.is_alive(), "the send should be stalled")
        printer.abort()
        worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertTrue(failed)

    def test_cancel_all_ends_a_stalled_job(self):
        real = backend.CANCEL_TIMEOUT
        backend.CANCEL_TIMEOUT = 1
        self.addCleanup(setattr, backend, "CANCEL_TIMEOUT", real)
        path = os.path.join(self.folder.name, "a.jpg")
        with open(path, "wb") as handle:
            handle.write(b"x" * (64 << 20))
        data = {"name": "t", "host": "127.0.0.1", "port": self.port, "path": "/ipp/print", "secure": False}
        documents = [{"action": "send", "path": path, "mime": "image/jpeg", "name": "a.jpg"}]
        replies = []
        worker = threading.Thread(
            target=lambda: replies.append(backend.print_documents(data, {"formats": []}, documents, {})), daemon=True
        )
        worker.start()
        worker.join(1)
        self.assertTrue(worker.is_alive(), "the send should be stalled")
        self.assertTrue(backend.cancel_all(data)["ok"])
        worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(replies[0]["result"], {"sent": 0, "cancelled": True})

    def test_silent_printer_gets_a_readable_message(self):
        real = backend.SEND_TIMEOUT
        backend.SEND_TIMEOUT = 1
        self.addCleanup(setattr, backend, "SEND_TIMEOUT", real)
        path = os.path.join(self.folder.name, "a.jpg")
        with open(path, "wb") as handle:
            handle.write(b"x")
        data = {"name": "t", "host": "127.0.0.1", "port": self.port, "path": "/ipp/print", "secure": False}
        documents = [{"action": "send", "path": path, "mime": "image/jpeg", "name": "a.jpg"}]
        reply = backend.print_documents(data, {"formats": []}, documents, {})
        self.assertIn("stopped responding", reply["error"])

    def test_cancel_all_without_a_job_still_reports_the_failure(self):
        real = backend.CANCEL_TIMEOUT
        backend.CANCEL_TIMEOUT = 1
        self.addCleanup(setattr, backend, "CANCEL_TIMEOUT", real)
        data = {"name": "t", "host": "127.0.0.1", "port": self.port, "path": "/ipp/print", "secure": False}
        self.assertFalse(backend.cancel_all(data)["ok"])


class Codec(unittest.TestCase):
    def test_multi_value_and_collection(self):
        data = struct.pack(">BBHI", 2, 0, 0, 1) + bytes([ipp.TAG_PRINTER])
        data += ipp._attribute(ipp.TAG_KEYWORD, "sides-supported", "one-sided")
        data += ipp._additional(ipp.TAG_KEYWORD, "two-sided-long-edge")
        data += struct.pack(">BH", ipp.TAG_BEG_COLLECTION, 9) + b"media-col" + struct.pack(">H", 0)
        member = b"media-type"
        data += struct.pack(">BHH", ipp.TAG_MEMBER_NAME, 0, len(member)) + member
        data += struct.pack(">BHH", ipp.TAG_KEYWORD, 0, 5) + b"plain"
        data += struct.pack(">BHH", ipp.TAG_END_COLLECTION, 0, 0)
        data += bytes([ipp.TAG_END])
        _, printer, _, _ = ipp.decode_response(data)
        self.assertEqual(printer["sides-supported"], ["one-sided", "two-sided-long-edge"])
        self.assertEqual(printer["media-col"][0]["media-type"], ["plain"])

    def test_truncated_response_raises(self):
        with self.assertRaises(ipp.IppError):
            ipp.decode_response(struct.pack(">BBHI", 2, 0, 0, 1) + b"\x04\x44\x00\xff")


class Discovery(unittest.TestCase):
    def test_truncated_address_record_is_ignored(self):
        name = b"\x01a\x00"
        record = name + struct.pack(">HHIH", discovery.TYPE_A, 1, 120, 4) + b"\x7f"
        packet = struct.pack(">HHHHHH", 0, 0x8400, 0, 1, 0, 0) + record
        self.assertEqual(discovery.parse_records(packet), [])

    def test_prefers_ipps_and_reads_txt(self):
        instance = "HP._ipps._tcp.local"
        plain = "HP._ipp._tcp.local"
        data = packet(
            record("_ipps._tcp.local", 12, name(instance)),
            record("_ipp._tcp.local", 12, name(plain)),
            record(instance, 33, struct.pack(">HHH", 0, 0, 631) + name("hp.local")),
            record(plain, 33, struct.pack(">HHH", 0, 0, 631) + name("hp.local")),
            record(instance, 16, b"\x08rp=ipp/p\x06ty=ENV\x06uuid=1"),
            record(plain, 16, b"\x08rp=ipp/p\x06ty=ENV\x06uuid=1"),
            record("hp.local", 1, bytes([192, 168, 1, 20])),
        )
        found = discovery.assemble(discovery.parse_records(data))
        self.assertEqual(
            found,
            [{"name": "ENV", "host": "192.168.1.20", "port": 631, "path": "/ipp/p", "secure": True}],
        )

    def test_compressed_name(self):
        data = b"\x00" * 12 + b"\x03abc\x00" + b"\xc0\x0c"
        self.assertEqual(discovery._read_name(data, 17), ("abc", 19))


class Backend(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        backend.CONFIG_DIR = self.tmp.name
        backend.CACHE_DIR = self.tmp.name
        backend.SAVED = os.path.join(self.tmp.name, "printers.json")
        self.server, self.state = fake_printer.serve()
        self.addCleanup(self.server.shutdown)
        port = self.server.server_address[1]
        self.data = {"name": "Sim", "host": "127.0.0.1", "port": port, "path": "/ipp/print", "secure": False}

    def test_connect_probes_and_saves(self):
        reply = backend.connect("127.0.0.1:%d" % self.data["port"])
        self.assertTrue(reply["ok"], reply)
        self.assertFalse(reply["result"]["secure"])
        self.assertEqual(reply["result"]["name"], "Simulated Printer")
        self.assertEqual(len(backend.saved()), 1)

    def test_connect_reports_failure(self):
        self.assertFalse(backend.connect("127.0.0.1:1")["ok"])
        self.assertFalse(backend.connect("  ")["ok"])

    def test_status_reports_ink(self):
        reply = backend.status(self.data)
        self.assertTrue(reply["ok"], reply)
        ink = reply["result"]["ink"]
        self.assertEqual(ink[0]["level"], 64)
        self.assertFalse(ink[0]["low"])
        self.assertTrue(ink[1]["low"])
        self.assertEqual(reply["result"]["state"], "idle")

    def test_print_sends_document_and_options(self):
        path = os.path.join(self.tmp.name, "a.pdf")
        with open(path, "wb") as handle:
            handle.write(b"%PDF-1.4 test")
        options = {"copies": 2, "sides": "two-sided-long-edge", "color": "monochrome", "quality": 5}
        attrs, payload = self.state.documents[backend._send(self.data, path, "application/pdf", "a.pdf", options)]
        self.assertEqual(payload, b"%PDF-1.4 test")
        self.assertEqual(attrs["copies"], [2])
        self.assertEqual(attrs["sides"], ["two-sided-long-edge"])
        self.assertEqual(attrs["print-color-mode"], ["monochrome"])
        self.assertEqual(attrs["print-quality"], [5])

    def test_jobs_and_cancel(self):
        path = os.path.join(self.tmp.name, "a.pdf")
        with open(path, "wb") as handle:
            handle.write(b"x")
        job = backend._send(self.data, path, "application/pdf", "a.pdf", {})
        self.assertEqual(backend.status(self.data)["result"]["jobs"][0]["id"], job)
        self.assertTrue(backend.cancel(self.data, job)["ok"])
        self.assertEqual(backend.status(self.data)["result"]["jobs"], [])

    def test_plan_refuses_pdf_when_printer_only_lists_octet_stream(self):
        # HP ENVY 6000: answers Validate-Job for application/pdf with 0x040a.
        formats = ["application/vnd.hp-PCL", "image/jpeg", "image/urf", "image/pwg-raster",
                   "application/PCLm", "application/octet-stream"]
        self.assertEqual(backend.plan("/t/a.pdf", formats)["action"], "refuse")
        self.assertEqual(backend.plan("/t/a.jpg", formats)["action"], "send")
        self.assertEqual(backend.plan("/t/a.png", formats)["action"], "convert")

    def test_plan(self):
        formats = ["application/pdf", "image/jpeg"]
        self.assertEqual(backend.plan("file:///t/a%20b.pdf", formats)["path"], "/t/a b.pdf")
        self.assertEqual(backend.plan("/t/a.pdf", formats)["action"], "send")
        self.assertEqual(backend.plan("/t/a.png", formats)["action"], "convert")
        self.assertEqual(backend.plan("/t/a.docx", formats)["action"], "office")
        self.assertEqual(backend.plan("/t/a.xyz", formats)["action"], "refuse")

    def test_unknown_files_are_never_sent_raw(self):
        # The HP lists octet-stream, and printed garbage for sheets on end when sent a file that way.
        formats = ["image/jpeg", "image/pwg-raster", "application/octet-stream"]
        self.assertEqual(backend.plan("/t/a.docx", formats)["action"], "refuse")
        self.assertEqual(backend.plan("/t/a.xyz", formats)["action"], "refuse")
        with self.assertRaises(ValueError):
            backend._send(self.data, "/t/a.xyz", "application/octet-stream", "a", {})

    def test_pdf_without_an_extension_is_recognised(self):
        path = os.path.join(self.tmp.name, "shared-file")
        with open(path, "wb") as handle:
            handle.write(b"%PDF-1.4 test")
        self.assertEqual(backend.plan(path, ["application/pdf", "application/octet-stream"])["mime"], "application/pdf")

    def test_office_file_waits_for_libreoffice(self):
        real = backend.OFFICE_ROOT
        backend.OFFICE_ROOT = os.path.join(self.tmp.name, "none")
        self.addCleanup(setattr, backend, "OFFICE_ROOT", real)
        reply = backend.prepare("/t/a.docx", {"formats": ["application/pdf"], "raster": None})
        self.assertEqual(reply["result"]["action"], "office-missing")

    def test_several_documents_print_in_order_and_report_progress(self):
        path = os.path.join(self.tmp.name, "a.pdf")
        with open(path, "wb") as handle:
            handle.write(b"%PDF-1.4 test")
        events = []
        real = backend._notify
        backend._notify = lambda event, *values: events.append(values[0])
        self.addCleanup(setattr, backend, "_notify", real)
        documents = [{"action": "send", "path": path, "mime": "application/pdf", "name": name} for name in ("one", "two")]
        reply = backend.print_documents(self.data, {"formats": ["application/pdf"]}, documents, {})
        self.assertEqual(reply["result"], {"sent": 2, "cancelled": False})
        self.assertEqual([(e["name"], e["index"], e["total"]) for e in events], [("one", 0, 2), ("two", 1, 2)])

    def test_bad_pages_stop_before_anything_prints(self):
        reply = backend.print_documents(self.data, {"formats": []}, [], {"pages": "3-1"})
        self.assertFalse(reply["ok"])

    def test_busy_printer_is_retried_until_it_takes_the_job(self):
        replies = [ipp.IppError("busy", ipp.STATUS_BUSY), 7]

        def send():
            reply = replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply

        stages = []
        real = backend.BUSY_RETRY
        backend.BUSY_RETRY = 0
        self.addCleanup(setattr, backend, "BUSY_RETRY", real)
        self.assertEqual(backend._while_busy(backend._Task(), stages.append, send), 7)
        self.assertEqual(stages, ["waiting"])

    def test_other_refusals_are_not_retried(self):
        def send():
            raise ipp.IppError("no", 0x040A)

        with self.assertRaises(ipp.IppError):
            backend._while_busy(backend._Task(), lambda stage: None, send)

    def test_cancelling_stops_the_next_document(self):
        task = backend._Task()
        task.cancel()
        with self.assertRaises(backend.Cancelled):
            task.check()

    def test_run_always_replies(self):
        replies = []
        done = threading.Event()

        def notify(event, request, reply):
            replies.append((request, reply))
            done.set()

        real = backend._notify
        backend._notify = notify
        self.addCleanup(setattr, backend, "_notify", real)
        backend.run(5, "cancel", [])
        self.assertTrue(done.wait(5))
        self.assertEqual(replies[0][0], 5)
        self.assertFalse(replies[0][1]["ok"])

    def test_paper_name(self):
        self.assertEqual(backend._paper_name("iso_a4_210x297mm"), "A4")
        self.assertEqual(backend._paper_name("na_letter_8.5x11in"), "Letter")

    def test_mime_lookup_needs_no_system_files(self):
        self.assertNotIn("mimetypes", sys.modules.get("backend").__dict__)
        self.assertEqual(backend._mime("/t/PHOTO.JPG"), "image/jpeg")


if __name__ == "__main__":
    unittest.main()
