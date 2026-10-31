"""Find network printers with a one-shot mDNS query. No Avahi needed."""
import random
import select
import socket
import struct
import time

MDNS = ("224.0.0.251", 5353)
SERVICES = ["_ipps._tcp.local", "_ipp._tcp.local"]
TYPE_A, TYPE_PTR, TYPE_TXT, TYPE_SRV = 1, 12, 16, 33


def _encode_name(name):
    out = bytearray()
    for label in name.split("."):
        out.append(len(label))
        out += label.encode("ascii")
    return bytes(out) + b"\x00"


def build_query(services=SERVICES):
    header = struct.pack(">HHHHHH", random.randint(1, 0xFFFF), 0, len(services), 0, 0, 0)
    body = b"".join(_encode_name(s) + struct.pack(">HH", TYPE_PTR, 1) for s in services)
    return header + body


def _read_name(data, pos):
    labels = []
    end = None
    seen = 0
    while True:
        if pos >= len(data) or seen > 64:
            raise ValueError("bad DNS name")
        seen += 1
        length = data[pos]
        if length == 0:
            pos += 1
            break
        if length & 0xC0 == 0xC0:
            if end is None:
                end = pos + 2
            pos = ((length & 0x3F) << 8) | data[pos + 1]
            continue
        labels.append(data[pos + 1 : pos + 1 + length].decode("utf-8", "replace"))
        pos += 1 + length
    return ".".join(labels), (end if end is not None else pos)


def parse_records(data):
    """Return a list of (name, type, value) from one mDNS packet."""
    if len(data) < 12:
        return []
    counts = struct.unpack(">HHHH", data[4:12])
    pos = 12
    for _ in range(counts[0]):
        _, pos = _read_name(data, pos)
        pos += 4
    records = []
    for _ in range(sum(counts[1:])):
        name, pos = _read_name(data, pos)
        rtype, _, _, length = struct.unpack(">HHIH", data[pos : pos + 10])
        pos += 10
        start = pos
        pos += length
        if pos > len(data):  # Cut short. Keep what was complete.
            break
        if rtype == TYPE_PTR:
            records.append((name, rtype, _read_name(data, start)[0]))
        elif rtype == TYPE_SRV:
            port = struct.unpack(">H", data[start + 4 : start + 6])[0]
            records.append((name, rtype, (port, _read_name(data, start + 6)[0])))
        elif rtype == TYPE_A and length == 4:
            records.append((name, rtype, socket.inet_ntoa(data[start:pos])))
        elif rtype == TYPE_TXT:
            txt = {}
            cursor = start
            while cursor < pos:
                size = data[cursor]
                key, _, value = data[cursor + 1 : cursor + 1 + size].partition(b"=")
                txt[key.decode("utf-8", "replace").lower()] = value.decode("utf-8", "replace")
                cursor += 1 + size
            records.append((name, rtype, txt))
    return records


def assemble(records):
    """Turn mDNS records into printers, preferring ipps over ipp for each device."""
    instances = {}
    services = {}
    texts = {}
    addresses = {}
    for name, rtype, value in records:
        if rtype == TYPE_PTR and name in SERVICES:
            instances[value] = name
        elif rtype == TYPE_SRV:
            services[name] = value
        elif rtype == TYPE_TXT:
            texts[name] = value
        elif rtype == TYPE_A:
            addresses[name] = value

    found = {}
    for instance, service in instances.items():
        if instance not in services:
            continue
        port, target = services[instance]
        host = addresses.get(target, target)
        txt = texts.get(instance, {})
        secure = service.startswith("_ipps")
        key = txt.get("uuid") or instance.split("._ipp")[0]
        if key in found and found[key]["secure"] and not secure:
            continue
        found[key] = {
            "name": txt.get("ty") or instance.split("._ipp")[0],
            "host": host,
            "port": port,
            "path": "/" + txt.get("rp", "ipp/print").lstrip("/"),
            "secure": secure,
        }
    return sorted(found.values(), key=lambda p: p["name"].lower())


def discover(timeout=3.0):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    records = []
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
        sock.setblocking(False)
        sock.sendto(build_query(), MDNS)
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if select.select([sock], [], [], remaining)[0]:
                try:
                    records += parse_records(sock.recv(9000))
                except (ValueError, struct.error, IndexError):
                    continue
    finally:
        sock.close()
    return assemble(records)
