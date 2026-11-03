"""Checks the page finder and clean-up on made-up photos, each with a known page position.

Needs Pillow on the PC, and a docscan built for the PC:
    python3 tests/docscan_check.py WORK_FOLDER 'COMMAND THAT RUNS DOCSCAN'
"""
import math
import os
import random
import shlex
import statistics
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFilter

WIDTH, HEIGHT = 1500, 2000
TOLERANCE = 0.02
PAPER_WHITE = 235
INK_DARK = 110


def solve(matrix, values):
    size = len(values)
    rows = [row[:] + [value] for row, value in zip(matrix, values)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(rows[row][column]))
        rows[column], rows[pivot] = rows[pivot], rows[column]
        for row in range(size):
            if row != column:
                factor = rows[row][column] / rows[column][column]
                rows[row] = [a - factor * b for a, b in zip(rows[row], rows[column])]
    return [rows[index][size] / rows[index][index] for index in range(size)]


def perspective(target, source):
    """Pillow's coefficients for drawing the source rectangle onto the target corners."""
    matrix, values = [], []
    for (x, y), (u, v) in zip(target, source):
        matrix.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        matrix.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        values += [u, v]
    return solve(matrix, values)


def page_image():
    width, height = 1240, 1754
    page = Image.new("RGB", (width, height), (238, 234, 226))
    draw = ImageDraw.Draw(page)
    draw.rectangle((110, 110, 700, 170), fill=(20, 20, 24))
    for line in range(34):
        y = 240 + line * 42
        draw.rectangle((110, y, 110 + random.randint(600, 1010), y + 16), fill=(30, 30, 36))
    return page


def photograph(background, corners):
    """Lays the page on a surface, in perspective, with a shadow across it."""
    page = page_image()
    source = [(0, 0), (page.width, 0), (page.width, page.height), (0, page.height)]
    target = [(x * WIDTH, y * HEIGHT) for x, y in corners]
    coefficients = perspective(target, source)
    sheet = page.transform((WIDTH, HEIGHT), Image.PERSPECTIVE, coefficients, Image.BICUBIC)
    mask = Image.new("L", page.size, 255).transform((WIDTH, HEIGHT), Image.PERSPECTIVE, coefficients)
    surface = Image.effect_noise((WIDTH, HEIGHT), 12).convert("RGB")
    surface = Image.blend(Image.new("RGB", (WIDTH, HEIGHT), background), surface, 0.12)
    surface.paste(sheet, (0, 0), mask)
    shadow = Image.new("L", (WIDTH, HEIGHT), 255)
    for y in range(HEIGHT):
        shade = int(255 - 95 * max(0, 1 - y / (HEIGHT * 0.6)))
        ImageDraw.Draw(shadow).line((0, y, WIDTH, y), fill=shade)
    dark = Image.new("RGB", (WIDTH, HEIGHT), (0, 0, 0))
    return Image.composite(surface, dark, shadow).filter(ImageFilter.GaussianBlur(0.8))


def rotated(angle):
    centre_x, centre_y, half_w, half_h = 0.5, 0.5, 0.28, 0.3
    turn = math.radians(angle)
    aspect = WIDTH / HEIGHT
    corners = []
    for x, y in ((-half_w, -half_h), (half_w, -half_h), (half_w, half_h), (-half_w, half_h)):
        wide = x * aspect
        turned_x = wide * math.cos(turn) - y * math.sin(turn)
        turned_y = wide * math.sin(turn) + y * math.cos(turn)
        corners.append((centre_x + turned_x / aspect, centre_y + turned_y))
    return corners


CASES = {
    "dark table, tilted": ((45, 32, 26), [(0.16, 0.12), (0.84, 0.17), (0.92, 0.9), (0.08, 0.84)]),
    "wooden table": ((150, 105, 62), [(0.2, 0.1), (0.8, 0.1), (0.9, 0.9), (0.1, 0.9)]),
    "grey desk": ((120, 124, 130), [(0.1, 0.14), (0.88, 0.08), (0.84, 0.88), (0.12, 0.92)]),
    "light table": ((172, 170, 165), [(0.12, 0.1), (0.88, 0.1), (0.88, 0.9), (0.12, 0.9)]),
    "turned 20 degrees": ((60, 70, 60), rotated(20)),
}


def main():
    work, command = sys.argv[1], shlex.split(sys.argv[2])
    random.seed(7)
    failures = 0
    for number, (name, (background, corners)) in enumerate(CASES.items()):
        path = f"case{number}.jpg"
        photograph(background, corners).save(os.path.join(work, path), quality=90)
        reply = subprocess.run([*command, "--detect", path], capture_output=True, text=True)
        values = [float(value) for value in reply.stdout.split()]
        found = [(values[i], values[i + 1]) for i in range(0, 8, 2)]
        worst = max(math.dist(a, b) for a, b in zip(found, corners))
        good = values[8] == 1 and worst <= TOLERANCE
        output = f"case{number}-out.jpg"
        subprocess.run([*command, "--warp", path, output, "grey", *reply.stdout.split()[:8]], check=True)
        result = Image.open(os.path.join(work, output)).convert("L")
        wide, high = result.size
        # Every band has to come out white, because a shadow only darkens part of the page.
        paper = [statistics.median(result.getpixel((x, int(high * y)))
                                   for x in range(int(wide * 0.86), int(wide * 0.92), 7))
                 for y in (0.03, 0.15, 0.3, 0.5, 0.75, 0.95)]
        ink = result.crop((int(wide * .1), int(high * .07), int(wide * .5), int(high * .09)))
        white = min(paper)
        dark = statistics.median(ink.tobytes())
        clean = white >= PAPER_WHITE and dark <= INK_DARK
        print(f"{'ok  ' if good and clean else 'FAIL'} {name:<20} corners off by {worst:.3f}, "
              f"size {wide}x{high}, paper {white:.0f}, ink {dark:.0f}")
        failures += not (good and clean)

    plain = Image.effect_noise((1200, 1600), 10).convert("RGB")
    plain.save(os.path.join(work, "plain.jpg"), quality=90)
    reply = subprocess.run([*command, "--detect", "plain.jpg"], capture_output=True, text=True)
    missed = reply.stdout.split()[8] == "0"
    print(f"{'ok  ' if missed else 'FAIL'} no page in the photo reports none: {reply.stdout.strip()}")
    failures += not missed
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
