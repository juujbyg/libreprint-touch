"""Install LibreOffice inside the app's own folder and use it to turn office files into PDF.

A confined app can run programs from its data folder, so this needs no root and leaves
the system alone. The download is LibreOffice's official build, checked against a fixed hash
that was taken after verifying The Document Foundation's signature.

Documents come from anywhere, so a conversion runs with the network blocked by the kernel
(see native/office2pdf.c) and with macros and links from documents turned off.
"""
import hashlib
import io
import os
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
import threading
import urllib.request

VERSION = "26.8.3"
BUILD = "26.8.3.1"
# Copies installed before the version was recorded are all this one.
UNRECORDED = "26.8.2"
VERSION_FILE = "libreprint-version"
# The archive, because the stable folder drops each release when the next one arrives.
BASE = "https://downloadarchive.documentfoundation.org/libreoffice/old/%s/deb/" % BUILD
BUILDS = {
    "aarch64": ("aarch64/LibreOffice_%s_Linux_aarch64_deb.tar.gz" % BUILD, "378a0766e4a815a5a96fb7473f346d072e23e3cc28606bdc8cd6f834cfaa14db"),
    "x86_64": ("x86_64/LibreOffice_%s_Linux_x86-64_deb.tar.gz" % BUILD, "e7cd3538e85818ff9dd9bb65386769fde927011242aace5a144989f4000dcabf"),
}
NO_SANDBOX = 6
# Written into the conversion profile before every run. LibreOffice never saves its own
# settings there, because the helper leaves without shutting it down.
SETTINGS = (
    ("/org.openoffice.Office.Common/Security/Scripting", "DisableMacrosExecution", "true"),
    ("/org.openoffice.Office.Common/Security/Scripting", "MacroSecurityLevel", "3"),
    ("/org.openoffice.Office.Common/Security/Scripting", "BlockUntrustedRefererLinks", "true"),
)
PROFILE_ITEMS = re.compile(r'<item oor:path="([^"]*)"><prop oor:name="([^"]*)"')
EMPTY_PROFILE = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<oor:items xmlns:oor="http://openoffice.org/2001/registry" xmlns:xs="http://www.w3.org/2001/XMLSchema" '
    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">\n</oor:items>\n'
)
SIZE_MB = 200
CHUNK = 1 << 20
# Not needed to convert documents, and they save a lot of space.
SKIP = ("gnome-integration", "kde-integration", "onlineupdate", "librelogo", "nlpsolver", "report-builder", "pyuno", "python-script")
CONVERT_TIMEOUT = 300
EXTENSIONS = {
    ".doc", ".docx", ".odt", ".rtf", ".wpd", ".pages",
    ".xls", ".xlsx", ".ods", ".csv", ".tsv", ".numbers",
    ".ppt", ".pptx", ".odp", ".key",
    ".odg", ".vsd", ".svg", ".webp",
    ".html", ".htm", ".epub",
}
# LibreOffice locks its profile, so two conversions can't overlap.
_converting = threading.Lock()


class OfficeError(Exception):
    pass


def available():
    """True if there is a LibreOffice build for this phone."""
    return platform.machine() in BUILDS


def _program(root):
    """The soffice binary. Its folder is what LibreOfficeKit needs."""
    folder = os.path.join(root, "opt")
    names = os.listdir(folder) if os.path.isdir(folder) else []
    found = [os.path.join(folder, name, "program", "soffice.bin") for name in names]
    return next((path for path in found if os.path.exists(path)), None)


def installed(root):
    return _program(root) is not None


def installed_version(root):
    """The LibreOffice version in root, or None if there is none."""
    if not installed(root):
        return None
    try:
        with open(os.path.join(root, VERSION_FILE), encoding="utf-8") as handle:
            return handle.read().strip() or UNRECORDED
    except OSError:
        return UNRECORDED


def outdated(root):
    """True if an older LibreOffice than this app ships is installed. Bug and security
    fixes reach the phone only by installing again."""
    version = installed_version(root)
    return version is not None and version != VERSION


def _download(target, progress):
    path, digest = BUILDS[platform.machine()]
    sha = hashlib.sha256()
    with urllib.request.urlopen(BASE + path, timeout=30) as response, open(target, "wb") as out:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = response.read(CHUNK)
            if not chunk:
                break
            out.write(chunk)
            sha.update(chunk)
            done += len(chunk)
            if total:
                progress("download", done / total)
    if sha.hexdigest() != digest:
        raise OfficeError("The LibreOffice download failed its security check")


def _deb_members(stream):
    """Yield (name, bytes) for each member of an ar archive, which is what a .deb is."""
    if stream.read(8) != b"!<arch>\n":
        raise OfficeError("The LibreOffice download is damaged")
    while True:
        header = stream.read(60)
        if len(header) < 60:
            return
        name = header[:16].decode().strip().rstrip("/")
        size = int(header[48:58])
        yield name, stream.read(size)
        if size % 2:
            stream.read(1)


def _unpack_deb(stream, root):
    for name, data in _deb_members(stream):
        if name.startswith("data.tar"):
            with tarfile.open(fileobj=io.BytesIO(data)) as archive:
                # Menu entries and the like sit outside opt/ and aren't needed here.
                members = (member for member in archive if member.name.startswith("./opt/"))
                archive.extractall(root, members=members, filter="data")


def install(root, progress=lambda stage, fraction: None):
    """Download and unpack LibreOffice into root. A failed install leaves nothing behind."""
    if not available():
        raise OfficeError("LibreOffice has no build for this phone")
    parent = os.path.dirname(root)
    os.makedirs(parent, exist_ok=True)
    work = tempfile.mkdtemp(prefix="office-", dir=parent)
    try:
        download = os.path.join(work, "office.tar.gz")
        _download(download, progress)
        unpacked = os.path.join(work, "unpacked")
        with tarfile.open(download) as archive:
            debs = [m for m in archive.getmembers() if m.name.endswith(".deb") and not any(s in m.name for s in SKIP)]
            for index, member in enumerate(debs):
                _unpack_deb(archive.extractfile(member), unpacked)
                progress("unpack", (index + 1) / len(debs))
        if not installed(unpacked):
            raise OfficeError("The LibreOffice download is missing its program")
        with open(os.path.join(unpacked, VERSION_FILE), "w", encoding="utf-8") as handle:
            handle.write(VERSION)
        shutil.rmtree(root, ignore_errors=True)
        os.rename(unpacked, root)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def remove(root):
    shutil.rmtree(root, ignore_errors=True)


def handles(path):
    return os.path.splitext(path)[1].lower() in EXTENSIONS


def _xml_text(value):
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def harden_profile(profile):
    """Put the safe settings into the profile's registrymodifications.xcu, replacing any
    earlier value of the same settings and keeping everything else."""
    folder = os.path.join(profile, "user")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "registrymodifications.xcu")
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except OSError:
        text = EMPTY_PROFILE
    if "</oor:items>" not in text:
        text = EMPTY_PROFILE
    ours = {(item_path, name) for item_path, name, _ in SETTINGS}
    kept = [line for line in text.splitlines(True)
            if not any(match in ours for match in PROFILE_ITEMS.findall(line))]
    text = "".join(kept)
    items = "".join(
        '<item oor:path="%s"><prop oor:name="%s" oor:op="fuse"><value>%s</value></prop></item>\n'
        % (_xml_text(item_path), _xml_text(name), _xml_text(value))
        for item_path, name, value in SETTINGS
    )
    end = text.rindex("</oor:items>")
    work = path + ".new"
    with open(work, "w", encoding="utf-8") as handle:
        handle.write(text[:end] + items + text[end:])
    os.replace(work, path)


def to_pdf(root, helper, path, work, target):
    """Make a PDF from an office file, using a folder that is the app's to write in."""
    with _converting:
        return _convert(root, helper, path, work, target)


def _convert(root, helper, path, work, target):
    program = _program(root)
    if program is None:
        raise OfficeError("LibreOffice isn't installed")
    os.makedirs(work, exist_ok=True)
    folder = tempfile.mkdtemp(prefix="convert-", dir=work)
    try:
        # A plain name keeps LibreOffice from reading # or % in the original as URL syntax.
        source = os.path.join(folder, "document" + os.path.splitext(path)[1].lower())
        shutil.copyfile(path, source)
        made = os.path.join(folder, "document.pdf")
        harden_profile(os.path.join(work, "profile"))
        environment = dict(os.environ, HOME=work, TMPDIR=folder, SAL_USE_VCLPLUGIN="svp")
        environment.pop("LD_LIBRARY_PATH", None)
        arguments = [
            helper, os.path.dirname(program),
            "file://" + os.path.join(work, "profile"), "file://" + source, "file://" + made,
        ]
        try:
            result = subprocess.run(
                arguments, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=CONVERT_TIMEOUT, env=environment
            )
        except subprocess.TimeoutExpired:
            raise OfficeError("This document took too long to prepare")
        if result.returncode == NO_SANDBOX:
            raise OfficeError("Office files can't be prepared safely on this phone: its kernel can't block the network for LibreOffice")
        if result.returncode != 0 or not os.path.isfile(made):
            raise OfficeError("LibreOffice could not open this file")
        os.replace(made, target)
        return target
    finally:
        shutil.rmtree(folder, ignore_errors=True)
