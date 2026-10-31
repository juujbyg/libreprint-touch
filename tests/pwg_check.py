#!/usr/bin/env python3
"""Check a PWG Raster file with libcups instead of LibrePrint's own code.

Usage: pwg_check.py FILE.pwg [--pdf SOURCE.pdf --pages 3,2,1] [--dump DIR]
With --pdf, each page is compared with pdftoppm's render of that PDF page.
"""
import argparse
import ctypes
import ctypes.util
import os
import struct
import subprocess
import sys
import tempfile

HEADER_SIZE = 1796
FIELDS = {
    "MediaClass": (0, "s"), "Duplex": (272, "I"), "HWResolution": (276, "II"), "PageSize": (352, "II"),
    "Tumble": (368, "I"), "Width": (372, "I"), "Height": (376, "I"), "BitsPerColor": (384, "I"),
    "BitsPerPixel": (388, "I"), "BytesPerLine": (392, "I"), "ColorSpace": (400, "I"), "NumColors": (420, "I"),
    "TotalPageCount": (452, "I"), "CrossFeed": (456, "I"), "Feed": (460, "I"), "Quality": (484, "I"),
    "PageSizeName": (1732, "s"),
}


def fields(raw):
    found = {}
    for name, (offset, kind) in FIELDS.items():
        if kind == "s":
            found[name] = raw[offset : offset + 64].split(b"\0")[0].decode()
        else:
            values = struct.unpack_from("<" + kind, raw, offset)
            found[name] = values if len(values) > 1 else values[0]
    return found


def read_pages(path):
    cups = ctypes.CDLL(ctypes.util.find_library("cups"))
    cups.cupsRasterOpen.restype = ctypes.c_void_p
    cups.cupsRasterOpen.argtypes = [ctypes.c_int, ctypes.c_int]
    cups.cupsRasterReadHeader2.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    cups.cupsRasterReadHeader2.restype = ctypes.c_uint
    cups.cupsRasterReadPixels.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
    cups.cupsRasterReadPixels.restype = ctypes.c_uint
    descriptor = os.open(path, os.O_RDONLY)
    raster = cups.cupsRasterOpen(descriptor, 0)
    assert raster, "libcups refused the file"
    header = ctypes.create_string_buffer(HEADER_SIZE)
    pages = []
    while cups.cupsRasterReadHeader2(raster, header):
        info = fields(header.raw)
        size = info["BytesPerLine"] * info["Height"]
        pixels = ctypes.create_string_buffer(size)
        got = cups.cupsRasterReadPixels(raster, pixels, size)
        assert got == size, "page %d: %d of %d bytes" % (len(pages) + 1, got, size)
        pages.append((info, pixels.raw))
    os.close(descriptor)
    return pages


def pnm_pixels(data):
    parts, position = [], 0
    while len(parts) < 4:
        end = position
        while data[end] not in b" \n\t":
            end += 1
        parts.append(data[position:end])
        position = end + 1
    return int(parts[1]), int(parts[2]), data[position:]


def compare(pixels, info, pdf, page):
    """Mean absolute difference, 0-255, against pdftoppm at the same resolution."""
    bpp = info["BitsPerPixel"] // 8
    with tempfile.TemporaryDirectory() as folder:
        stem = os.path.join(folder, "p")
        flag = ["-gray"] if bpp == 1 else []
        subprocess.run(
            ["pdftoppm", "-singlefile", "-f", str(page), "-l", str(page), "-r", str(info["HWResolution"][0])] + flag + [pdf, stem],
            check=True,
        )
        width, height, other = pnm_pixels(open(stem + (".pgm" if bpp == 1 else ".ppm"), "rb").read())
    if info["CrossFeed"] != 1 and info["Feed"] != 1:
        size = info["BytesPerLine"] * info["Height"]
        pixels = b"".join(pixels[i : i + bpp] for i in range(size - bpp, -1, -bpp))
    left = (info["Width"] - width) // 2
    top = (info["Height"] - height) // 2
    total = count = 0
    for y in range(0, min(height, info["Height"]), 7):
        for x in range(0, min(width, info["Width"]) * bpp, 11):
            a = pixels[(top + y) * info["BytesPerLine"] + max(left, 0) * bpp + x]
            b = other[y * width * bpp + x]
            total += abs(a - b)
            count += 1
    return total / count


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("file")
    parser.add_argument("--pdf")
    parser.add_argument("--pages", help="source page of each raster page, 0 for blank")
    parser.add_argument("--dump")
    options = parser.parse_args()
    pages = read_pages(options.file)
    expected = [int(p) for p in options.pages.split(",")] if options.pages else None
    failed = False
    if expected and len(expected) != len(pages):
        print("page count %d, expected %d" % (len(pages), len(expected)))
        failed = True
    for index, (info, pixels) in enumerate(pages):
        ink = sum(1 for byte in pixels[:: 97] if byte < 200)
        line = "page %d: %dx%d %dbpp space=%d dup=%d tot=%d xf=%x/%x q=%d %s ink=%d" % (
            index + 1, info["Width"], info["Height"], info["BitsPerPixel"], info["ColorSpace"], info["Duplex"],
            info["TotalPageCount"], info["CrossFeed"], info["Feed"], info["Quality"], info["PageSizeName"], ink)
        if options.pdf and expected and index < len(expected) and expected[index]:
            difference = compare(pixels, info, options.pdf, expected[index])
            line += " diff=%.2f" % difference
            failed = failed or difference > 6
        print(line)
        if options.dump:
            os.makedirs(options.dump, exist_ok=True)
            bpp = info["BitsPerPixel"] // 8
            with open(os.path.join(options.dump, "page%d.%s" % (index + 1, "pgm" if bpp == 1 else "ppm")), "wb") as out:
                out.write(b"P%d %d %d 255\n" % (5 if bpp == 1 else 6, info["Width"], info["Height"]) + pixels)
    print("FAILED" if failed else "OK")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
