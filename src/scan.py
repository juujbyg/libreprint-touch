"""Scan over eSCL, the driverless protocol most network scanners speak."""
import http.client
import re
import time
from urllib.parse import urlsplit

import ipp

# Encrypted first, because scans are often private. Plain HTTP is only tried for a
# printer that has never shown this app a certificate (see survey).
ENDPOINTS = [(True, 443), (True, 9096), (True, 631), (False, 8080), (False, 80), (False, 9095), (False, 631)]
PREFIXES = ["/eSCL", "/escl"]
PROBE_TIMEOUT = 4
REQUEST_TIMEOUT = 30
SCAN_TIMEOUT = 180
BUSY = 503
COMMON_DPIS = (75, 150, 300, 600, 1200)
NOTHING_ANSWERED = "No driverless scanner answered at this address"
UNREADABLE = "A scanner answered, but it offers no flatbed or feeder that sends JPEG"
SETTINGS = """<?xml version="1.0" encoding="UTF-8"?>
<scan:ScanSettings xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03" xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm">
<pwg:Version>2.0</pwg:Version>
<pwg:ScanRegions><pwg:ScanRegion>
<pwg:ContentRegionUnits>escl:ThreeHundredthsOfInches</pwg:ContentRegionUnits>
<pwg:Width>{width}</pwg:Width><pwg:Height>{height}</pwg:Height>
<pwg:XOffset>0</pwg:XOffset><pwg:YOffset>0</pwg:YOffset>
</pwg:ScanRegion></pwg:ScanRegions>
<pwg:InputSource>{source}</pwg:InputSource>
<pwg:DocumentFormat>image/jpeg</pwg:DocumentFormat>
<scan:DocumentFormatExt>image/jpeg</scan:DocumentFormatExt>
<scan:ColorMode>{mode}</scan:ColorMode>
<scan:XResolution>{dpi}</scan:XResolution><scan:YResolution>{dpi}</scan:YResolution>
</scan:ScanSettings>
"""


class ScanError(Exception):
    pass


def _request(scanner, method, path, body=None, timeout=REQUEST_TIMEOUT, trust=None):
    """One request. Over HTTPS the certificate must match scanner["pin"] when there is
    one; otherwise trust(port, fingerprint) decides, and the pin is kept in scanner."""
    host, port, secure = scanner["host"], int(scanner["port"]), scanner["secure"]
    if secure:
        conn = ipp.open_tls(lambda context: http.client.HTTPSConnection(host, port, timeout=timeout, context=context))
    else:
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        if secure:
            seen = ipp.check_pin(conn.sock, scanner.get("pin"))
            if not scanner.get("pin") and trust is not None:
                trust(port, seen)
            scanner["pin"] = seen
            scanner["tls"] = conn.sock.version()
        headers = {"Content-Type": "text/xml"} if body else {}
        conn.request(method, path, body, headers)
        response = conn.getresponse()
        return response.status, response.getheader("Location"), response.read()
    finally:
        conn.close()


def _numbers(tag, text):
    return [int(value) for value in re.findall(r"<(?:\w+:)?%s>(\d+)<" % tag, text)]


def _resolutions(section):
    """Scanners list their resolutions, or give a range to choose from."""
    listed = sorted(set(_numbers("XResolution", section)))
    if listed:
        return listed
    span = re.search(r"XResolutionRange>(.*?)</(?:\w+:)?XResolutionRange", section, re.S)
    if span is None:
        return []
    lowest, highest = _numbers("Min", span.group(1)), _numbers("Max", span.group(1))
    if not lowest or not highest:
        return []
    return [dpi for dpi in COMMON_DPIS if lowest[0] <= dpi <= highest[0]]


def parse_capabilities(text):
    """Read what the flatbed can do, or the document feeder if there's no flatbed."""
    for tag, source in (("Platen", "Platen"), ("Adf", "Feeder")):
        found = re.search(r"<(?:\w+:)?%s>(.*?)</(?:\w+:)?%s>" % (tag, tag), text, re.S)
        if found is None:
            continue
        section = found.group(1)
        dpis = _resolutions(section)
        widths, heights = _numbers("MaxWidth", section), _numbers("MaxHeight", section)
        modes = sorted(set(re.findall(r"<(?:\w+:)?ColorMode>(\w+)<", section)))
        # Some scanners list formats once, in shared profiles outside the input source.
        if dpis and widths and heights and modes and "image/jpeg" in text:
            return {"dpis": dpis, "modes": modes, "width": widths[0], "height": heights[0], "source": source}
    return None


def survey(host, pin_for=lambda port: None, trust=None, plain=True):
    """Look for an eSCL scanner on the printer's address. Returns it, or None and the reason.
    pin_for gives the pinned fingerprint for a port. Pinned ports go first, so a device on a
    port the printer leaves unused can't come before the printer itself. A certificate that
    differs from its pin raises ipp.CertificateChanged rather than trying the next port.
    plain=False skips plain HTTP, for a printer known to speak TLS: otherwise blocking its
    HTTPS port would quietly move the scans to an unencrypted one."""
    note = NOTHING_ANSWERED
    endpoints = [(secure, port) for secure, port in ENDPOINTS if secure or plain]
    endpoints.sort(key=lambda endpoint: not (endpoint[0] and pin_for(endpoint[1])))
    for secure, port in endpoints:
        for prefix in PREFIXES:
            scanner = {"host": host, "port": port, "secure": secure, "prefix": prefix}
            if secure:
                scanner["pin"] = pin_for(port)
            try:
                status, _, body = _request(
                    scanner, "GET", prefix + "/ScannerCapabilities", timeout=PROBE_TIMEOUT, trust=trust
                )
            except (OSError, http.client.HTTPException):
                break
            if status == 200:
                capabilities = parse_capabilities(body.decode("utf-8", "replace"))
                if capabilities:
                    scanner.update(capabilities)
                    return scanner, ""
                note = UNREADABLE
    return None, note


def find(host, pin_for=lambda port: None, trust=None, plain=True):
    return survey(host, pin_for, trust, plain)[0]


def _start(scanner, settings, trust=None):
    deadline = time.monotonic() + SCAN_TIMEOUT
    path = scanner["prefix"] + "/ScanJobs"
    while True:
        status, location, _ = _request(scanner, "POST", path, settings, trust=trust)
        if status in (200, 201) and location:
            return urlsplit(location).path.rstrip("/")
        if status != BUSY or time.monotonic() > deadline:
            raise ScanError("The scanner is busy" if status == BUSY else "The scanner refused the job (%d)" % status)
        time.sleep(1)


def scan_page(scanner, dpi, color, trust=None):
    """Scan one page and return the JPEG. Over HTTPS every request of the job must show
    the certificate pinned in scanner["pin"], or the first one pins it."""
    mode = "RGB24" if color else "Grayscale8"
    if mode not in scanner["modes"]:
        raise ScanError("The scanner can't scan in that mode")
    settings = SETTINGS.format(
        width=int(scanner["width"]),
        height=int(scanner["height"]),
        mode=mode,
        dpi=int(dpi),
        source=scanner.get("source", "Platen"),
    )
    job = _start(scanner, settings, trust)
    deadline = time.monotonic() + SCAN_TIMEOUT
    while True:
        status, _, body = _request(scanner, "GET", job + "/NextDocument", timeout=SCAN_TIMEOUT)
        if status == 200:
            break
        if status != BUSY or time.monotonic() > deadline:
            raise ScanError("The scanner didn't return a page (%d)" % status)
        time.sleep(1)
    if not body.startswith(b"\xff\xd8"):
        raise ScanError("The scanner sent something that isn't a JPEG")
    try:
        # Some scanners stay busy until the job is read to its end.
        _request(scanner, "GET", job + "/NextDocument", timeout=PROBE_TIMEOUT)
    except (OSError, http.client.HTTPException):
        pass
    return body
