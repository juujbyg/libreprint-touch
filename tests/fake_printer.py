"""A small IPP printer simulator, for testing without paper or ink."""
import argparse
import http.server
import os
import struct
import sys
import threading

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


class State:
    def __init__(self):
        self.jobs = {}
        self.documents = {}
        self.next_id = 100


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


def serve(port=0):
    state = State()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            data = self.rfile.read(int(self.headers["Content-Length"]))
            reply = respond(state, data)
            self.send_response(200)
            self.send_header("Content-Type", "application/ipp")
            self.send_header("Content-Length", str(len(reply)))
            self.end_headers()
            self.wfile.write(reply)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, state


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8631)
    server, state = serve(parser.parse_args().port)
    print("Simulated printer on 127.0.0.1:%d" % server.server_address[1], flush=True)
    threading.Event().wait()
