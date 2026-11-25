"""A small IPP printer simulator, for testing without paper or ink. With tls=True it
serves IPP over TLS with a self-signed certificate, as printers do (needs the openssl
command)."""
import argparse
import atexit
import hashlib
import http.server
import os
import shutil
import ssl
import struct
import subprocess
import sys
import tempfile
import threading
import warnings

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import ipp  # noqa: E402

FORMATS = ["application/pdf", "image/jpeg", "image/urf", "application/octet-stream"]


def _attr(tag, name, value):
    return ipp._attribute(tag, name, value)


def _attrs(tag, name, values):
    out = _attr(tag, name, values[0])
    for value in values[1:]:
        out += ipp._additional(tag, value)
    return out


_FOLDER = None
_DEFAULT = None


def have_openssl():
    return shutil.which("openssl") is not None


def make_certificate(name="fake-printer"):
    """A new self-signed certificate and key: (certificate path, key path)."""
    global _FOLDER
    if _FOLDER is None:
        _FOLDER = tempfile.mkdtemp(prefix="fake-printer-")
        atexit.register(shutil.rmtree, _FOLDER, True)
    work = tempfile.mkdtemp(dir=_FOLDER)
    certificate, key = os.path.join(work, "cert.pem"), os.path.join(work, "key.pem")
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-sha256", "-days", "2",
         "-subj", "/CN=" + name, "-keyout", key, "-out", certificate],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True,
    )
    return certificate, key


def default_certificate():
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = make_certificate()
    return _DEFAULT


def server_context(certificate=None, maximum=None):
    """TLS for a fake server. maximum limits the version, to act as an old printer."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(*(certificate or default_certificate()))
    if maximum is not None:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            context.set_ciphers("DEFAULT:@SECLEVEL=0")
            context.minimum_version = ssl.TLSVersion.MINIMUM_SUPPORTED
            context.maximum_version = maximum
    return context


def certificate_fingerprint(certificate):
    with open(certificate, encoding="ascii") as handle:
        der = ssl.PEM_cert_to_DER_cert(handle.read())
    return hashlib.sha256(der).hexdigest()


def wrap(server, tls=False, certificate=None, maximum=None):
    """Make an HTTP server speak TLS. The handshake runs in the handler thread, so a
    client that hangs up mid-handshake doesn't stop the server."""
    if tls:
        context = server_context(certificate, maximum)
        server.socket = context.wrap_socket(server.socket, server_side=True, do_handshake_on_connect=False)
    return server


class QuietServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def finish_request(self, request, client_address):
        if isinstance(request, ssl.SSLSocket):
            try:
                request.do_handshake()
            except (OSError, ssl.SSLError):
                return
        super().finish_request(request, client_address)

    def handle_error(self, request, client_address):
        # The app hangs up right after the handshake when it refuses a certificate.
        if isinstance(sys.exc_info()[1], OSError):
            return
        super().handle_error(request, client_address)


class State:
    def __init__(self):
        self.jobs = {}
        self.documents = {}
        self.next_id = 100
        self.requests = 0


def _split_request(data):
    """Return (operation, attribute dict, document payload)."""
    reader = ipp._Reader(data)
    reader.take(2)
    operation = struct.unpack(">H", reader.take(2))[0]
    reader.take(4)
    attrs = {}
    name = None
    while True:
        tag = reader.byte()
        if tag == ipp.TAG_END:
            break
        if tag < 0x10:
            continue
        attr = reader.take(reader.short()).decode()
        raw = reader.take(reader.short())
        value = ipp._decode(tag, raw)
        if attr:
            name = attr
            attrs[name] = [value]
        else:
            attrs[name].append(value)
    return operation, attrs, data[reader.pos :]


def respond(state, data):
    operation, attrs, payload = _split_request(data)
    head = struct.pack(">BBHI", 2, 0, 0, 1)
    body = bytes([ipp.TAG_OPERATION])
    body += _attr(ipp.TAG_CHARSET, "attributes-charset", "utf-8")
    body += _attr(ipp.TAG_LANGUAGE, "attributes-natural-language", "en")

    if operation == ipp.OP_GET_PRINTER_ATTRIBUTES:
        body += bytes([ipp.TAG_PRINTER])
        body += _attr(ipp.TAG_TEXT, "printer-make-and-model", "Simulated Printer")
        body += _attr(ipp.TAG_ENUM, "printer-state", 3)
        body += _attr(ipp.TAG_KEYWORD, "printer-state-reasons", "none")
        body += _attrs(ipp.TAG_MIME, "document-format-supported", FORMATS)
        body += _attrs(ipp.TAG_KEYWORD, "sides-supported", ["one-sided", "two-sided-long-edge"])
        body += _attrs(ipp.TAG_KEYWORD, "print-color-mode-supported", ["color", "monochrome"])
        body += _attrs(ipp.TAG_ENUM, "print-quality-supported", [3, 4, 5])
        body += _attrs(ipp.TAG_NAME, "marker-names", ["Black", "Tri-color"])
        body += _attrs(ipp.TAG_INTEGER, "marker-levels", [64, 8])
        body += _attrs(ipp.TAG_NAME, "marker-colors", ["#000000", "#00FFFF#FF00FF#FFFF00"])
        body += _attrs(ipp.TAG_INTEGER, "marker-low-levels", [10, 10])
    elif operation == ipp.OP_PRINT_JOB:
        job_id = state.next_id
        state.next_id += 1
        state.jobs[job_id] = attrs.get("job-name", ["untitled"])[0]
        state.documents[job_id] = (attrs, payload)
        body += bytes([ipp.TAG_JOB])
        body += _attr(ipp.TAG_INTEGER, "job-id", job_id)
        body += _attr(ipp.TAG_ENUM, "job-state", 5)
    elif operation == ipp.OP_GET_JOBS:
        for job_id, name in state.jobs.items():
            body += bytes([ipp.TAG_JOB])
            body += _attr(ipp.TAG_INTEGER, "job-id", job_id)
            body += _attr(ipp.TAG_NAME, "job-name", name)
            body += _attr(ipp.TAG_ENUM, "job-state", 5)
    elif operation == ipp.OP_CANCEL_JOB:
        state.jobs.pop(attrs["job-id"][0], None)
    return head + body + bytes([ipp.TAG_END])


def serve(port=0, tls=False, certificate=None, maximum=None):
    state = State()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            state.requests += 1
            data = self.rfile.read(int(self.headers["Content-Length"]))
            reply = respond(state, data)
            self.send_response(200)
            self.send_header("Content-Type", "application/ipp")
            self.send_header("Content-Length", str(len(reply)))
            self.end_headers()
            self.wfile.write(reply)

        def log_message(self, *args):
            pass

    server = wrap(QuietServer(("127.0.0.1", port), Handler), tls, certificate, maximum)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, state


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8631)
    parser.add_argument("--tls", action="store_true", help="serve IPP over TLS (ipps)")
    arguments = parser.parse_args()
    server, state = serve(arguments.port, arguments.tls)
    scheme = "ipps" if arguments.tls else "ipp"
    print("Simulated printer on %s://127.0.0.1:%d/ipp/print" % (scheme, server.server_address[1]), flush=True)
    if arguments.tls:
        print("Certificate SHA-256 %s" % certificate_fingerprint(default_certificate()[0]), flush=True)
    threading.Event().wait()
