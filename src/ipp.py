"""Minimal IPP/2.0 client. Standard library only, so it runs in a confined click."""
import http.client
import socket
import ssl
import struct

OP_PRINT_JOB = 0x0002
OP_CANCEL_JOB = 0x0008
OP_GET_JOBS = 0x000A
OP_GET_PRINTER_ATTRIBUTES = 0x000B

STATUS_MESSAGES = {
    0x040A: "The printer can't read this file type",
    0x0506: "The printer isn't accepting jobs right now",
    0x0507: "The printer is busy. Try again in a moment",
}
STATUS_BUSY = 0x0507
# A printer that has gone should fail fast, however long a send may need once connected.
CONNECT_TIMEOUT = 15

TAG_OPERATION = 0x01
TAG_JOB = 0x02
TAG_END = 0x03
TAG_PRINTER = 0x04
TAG_UNSUPPORTED = 0x05

TAG_INTEGER = 0x21
TAG_BOOLEAN = 0x22
TAG_ENUM = 0x23
TAG_OCTET = 0x30
TAG_DATETIME = 0x31
TAG_RESOLUTION = 0x32
TAG_RANGE = 0x33
TAG_BEG_COLLECTION = 0x34
TAG_TEXT_LANG = 0x35
TAG_NAME_LANG = 0x36
TAG_END_COLLECTION = 0x37
TAG_TEXT = 0x41
TAG_NAME = 0x42
TAG_KEYWORD = 0x44
TAG_URI = 0x45
TAG_CHARSET = 0x47
TAG_LANGUAGE = 0x48
TAG_MIME = 0x49
TAG_MEMBER_NAME = 0x4A

JOB_STATES = {
    3: "pending",
    4: "held",
    5: "processing",
    6: "stopped",
    7: "canceled",
    8: "aborted",
    9: "completed",
}
PRINTER_STATES = {3: "idle", 4: "printing", 5: "stopped"}


class IppError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def _value(tag, value):
    if tag in (TAG_INTEGER, TAG_ENUM):
        return struct.pack(">i", value)
    if tag == TAG_BOOLEAN:
        return b"\x01" if value else b"\x00"
    if tag == TAG_RANGE:
        return struct.pack(">ii", value[0], value[1])
    return value.encode("utf-8")


def _attribute(tag, name, value):
    encoded_name = name.encode("ascii")
    encoded = _value(tag, value)
    return (
        struct.pack(">BH", tag, len(encoded_name))
        + encoded_name
        + struct.pack(">H", len(encoded))
        + encoded
    )


def _additional(tag, value):
    encoded = _value(tag, value)
    return struct.pack(">BH", tag, 0) + struct.pack(">H", len(encoded)) + encoded


def _attributes(tag, name, value):
    """Encode one value, or several. A single range is itself a pair of numbers."""
    several = isinstance(value, (list, tuple)) and (tag != TAG_RANGE or isinstance(value[0], (list, tuple)))
    if not several:
        return _attribute(tag, name, value)
    return _attribute(tag, name, value[0]) + b"".join(_additional(tag, extra) for extra in value[1:])


def encode_request(operation, uri, operation_attrs=(), job_attrs=(), request_id=1):
    out = bytearray(struct.pack(">BBHI", 2, 0, operation, request_id))
    out.append(TAG_OPERATION)
    out += _attribute(TAG_CHARSET, "attributes-charset", "utf-8")
    out += _attribute(TAG_LANGUAGE, "attributes-natural-language", "en")
    out += _attribute(TAG_URI, "printer-uri", uri)
    for tag, name, value in operation_attrs:
        out += _attributes(tag, name, value)
    if job_attrs:
        out.append(TAG_JOB)
        for tag, name, value in job_attrs:
            out += _attributes(tag, name, value)
    out.append(TAG_END)
    return bytes(out)


def _decode(tag, raw):
    if tag in (TAG_INTEGER, TAG_ENUM) and len(raw) == 4:
        return struct.unpack(">i", raw)[0]
    if tag == TAG_BOOLEAN and len(raw) == 1:
        return raw != b"\x00"
    if tag == TAG_RANGE and len(raw) == 8:
        return list(struct.unpack(">ii", raw))
    if tag == TAG_RESOLUTION and len(raw) == 9:
        return list(struct.unpack(">iiB", raw))
    if tag in (TAG_TEXT_LANG, TAG_NAME_LANG) and len(raw) >= 4:
        lang_len = struct.unpack(">H", raw[:2])[0]
        text = raw[2 + lang_len + 2 :]
        return text.decode("utf-8", "replace")
    if tag == TAG_OCTET or tag == TAG_DATETIME:
        return raw.hex()
    if 0x10 <= tag <= 0x1F:
        return None
    return raw.decode("utf-8", "replace")


class _Reader:
    def __init__(self, data):
        self.data = data
        self.pos = 0

    def take(self, count):
        end = self.pos + count
        if end > len(self.data):
            raise IppError("Truncated response from printer")
        chunk = self.data[self.pos : end]
        self.pos = end
        return chunk

    def byte(self):
        return self.take(1)[0]

    def short(self):
        return struct.unpack(">H", self.take(2))[0]


def _read_collection(reader):
    members = {}
    key = None
    while True:
        tag = reader.byte()
        reader.take(reader.short())
        raw = reader.take(reader.short())
        if tag == TAG_END_COLLECTION:
            return members
        if tag == TAG_MEMBER_NAME:
            key = raw.decode("utf-8", "replace")
            continue
        value = _read_collection(reader) if tag == TAG_BEG_COLLECTION else _decode(tag, raw)
        if key is not None:
            members.setdefault(key, []).append(value)


def decode_response(data):
    """Return (status, printer_attrs, jobs, unsupported)."""
    if len(data) < 8:
        raise IppError("Printer sent an invalid response")
    reader = _Reader(data)
    reader.take(2)
    status = struct.unpack(">H", reader.take(2))[0]
    reader.take(4)

    groups = {TAG_OPERATION: [{}], TAG_PRINTER: [{}], TAG_JOB: [], TAG_UNSUPPORTED: [{}]}
    current = {}
    name = None
    while True:
        tag = reader.byte()
        if tag == TAG_END:
            break
        if tag < 0x10:
            current = {}
            groups.setdefault(tag, []).append(current)
            name = None
            continue
        attr = reader.take(reader.short()).decode("utf-8", "replace")
        raw = reader.take(reader.short())
        value = _read_collection(reader) if tag == TAG_BEG_COLLECTION else _decode(tag, raw)
        if attr:
            name = attr
            current[name] = [value]
        elif name is not None:
            current[name].append(value)

    printer = {}
    for group in groups[TAG_PRINTER]:
        printer.update(group)
    unsupported = {}
    for group in groups[TAG_UNSUPPORTED]:
        unsupported.update(group)
    return status, printer, groups[TAG_JOB], unsupported


def _insecure_context():
    # Printers ship self-signed certificates, so they can't be verified.
    # TLS here protects against passive listeners on the local network only.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


class Printer:
    def __init__(self, host, port=631, path="/ipp/print", secure=True, timeout=15):
        self.host = host
        self.port = port
        self.path = path
        self.secure = secure
        self.timeout = timeout
        self._connection = None
        self._aborted = False

    @property
    def uri(self):
        scheme = "ipps" if self.secure else "ipp"
        return "%s://%s:%d%s" % (scheme, self.host, self.port, self.path)

    def abort(self):
        """Stop a request running on another thread. Shutting the socket down wakes a
        send that is stalled on a printer that stopped reading."""
        self._aborted = True
        connection = self._connection
        if connection is not None and connection.sock is not None:
            try:
                socket.socket.shutdown(connection.sock, socket.SHUT_RDWR)
            except OSError:
                pass

    def _post(self, body):
        wait = min(self.timeout, CONNECT_TIMEOUT)
        if self.secure:
            conn = http.client.HTTPSConnection(self.host, self.port, timeout=wait, context=_insecure_context())
        else:
            conn = http.client.HTTPConnection(self.host, self.port, timeout=wait)
        self._connection = conn
        try:
            conn.connect()
            conn.sock.settimeout(self.timeout)
            if self._aborted:
                raise IppError("Cancelled")
            conn.request("POST", self.path, body, {"Content-Type": "application/ipp"})
            response = conn.getresponse()
            data = response.read()
            if response.status != 200:
                raise IppError("Printer returned HTTP %d" % response.status)
            return data
        finally:
            self._connection = None
            conn.close()

    def call(self, operation, operation_attrs=(), job_attrs=(), payload=b""):
        body = encode_request(operation, self.uri, operation_attrs, job_attrs) + payload
        result = decode_response(self._post(body))
        if result[0] >= 0x0100:
            default = "Printer refused the request (status 0x%04x)" % result[0]
            raise IppError(STATUS_MESSAGES.get(result[0], default), result[0])
        return result

    def attributes(self):
        wanted = (TAG_KEYWORD, "requested-attributes", ["all", "media-col-database"])
        return self.call(OP_GET_PRINTER_ATTRIBUTES, [wanted])[1]

    def state(self):
        """Just the state, for checking often: asking for everything is a lot of work
        for a printer, especially while it's printing."""
        wanted = (TAG_KEYWORD, "requested-attributes", ["printer-state", "printer-state-reasons"])
        return self.call(OP_GET_PRINTER_ATTRIBUTES, [wanted])[1]

    def print_document(self, data, mime, name, copies=1, sides=None, color=None, quality=None, media=None, pages=None):
        operation = [
            (TAG_NAME, "requesting-user-name", "phablet"),
            (TAG_NAME, "job-name", name[:255]),
            (TAG_MIME, "document-format", mime),
        ]
        job = []
        if copies > 1:
            job.append((TAG_INTEGER, "copies", copies))
        if sides:
            job.append((TAG_KEYWORD, "sides", sides))
        if color:
            job.append((TAG_KEYWORD, "print-color-mode", color))
        if quality:
            job.append((TAG_ENUM, "print-quality", int(quality)))
        if media:
            job.append((TAG_KEYWORD, "media", media))
        if pages:
            job.append((TAG_RANGE, "page-ranges", pages))
        _, _, jobs, _ = self.call(OP_PRINT_JOB, operation, job, data)
        return jobs[0].get("job-id", [None])[0] if jobs else None

    def jobs(self):
        operation = [
            (TAG_NAME, "requesting-user-name", "phablet"),
            (TAG_KEYWORD, "requested-attributes", ["job-id", "job-name", "job-state", "job-state-reasons"]),
        ]
        return self.call(OP_GET_JOBS, operation)[2]

    def cancel(self, job_id):
        operation = [
            (TAG_INTEGER, "job-id", job_id),
            (TAG_NAME, "requesting-user-name", "phablet"),
        ]
        self.call(OP_CANCEL_JOB, operation)
