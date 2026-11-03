# LibrePrint for Ubuntu Touch

Print and scan from your phone with almost any modern network printer, with no drivers, and see its ink levels and print queue.

LibrePrint talks to the printer directly, over IPP for printing and eSCL for scanning, the standards behind AirPrint. It doesn't need CUPS, SANE, snaps or extra setup, and it runs fully confined.

<p>
  <img src="docs/screenshots/printer.png" width="24%" alt="Printer tab with status, queue and ink levels">
  <img src="docs/screenshots/print.png" width="24%" alt="Print tab with documents and options">
  <img src="docs/screenshots/scan.png" width="24%" alt="Scan tab with a page preview">
  <img src="docs/screenshots/diagnostics.png" width="24%" alt="Diagnostics tab with passing checks">
</p>

## Features
- Finds printers on your Wi-Fi network, or connects by address
- Shows printer status and ink levels, with a low-ink warning
- Shows the print queue and lets you cancel one job or all of them
- Four tabs, like LibrePrint on the desktop: Printer, Print, Scan and Diagnostics
- Prints PDFs, photos, text and Office files, several at a time, with copies, page ranges, colour, two-sided and quality options where the printer supports them
- Shows a thumbnail, page count and full preview of each document before printing
- Prints from Document Viewer and other apps: use Share and choose LibrePrint
- Installs its own copy of LibreOffice on request, to prepare Word, Excel and PowerPoint files
- Scans from the flatbed in colour or greyscale, several pages to one PDF or to JPEGs, with a preview of every page
- Scans with the camera, with or without a scanner: finds the page, lets you drag its corners, straightens it, removes shadows, and adds it in colour, greyscale or black-and-white text
- Prints a test page showing colours, tones, fine lines and small text
- Diagnostics that check the network, printer, PDF rendering, Office support and scanner
- Opens the printer's own web page
- Renders PDFs on the phone for printers that can't read them, such as many home inkjets
- Converts images to JPEG when the printer can't read the original format

## Limits
- The printer must support driverless printing (IPP Everywhere or AirPrint)
- PDFs can only be rendered for printers that accept PWG Raster
- A printer reads a rendered PDF as it prints, so keep LibrePrint open until the last page has gone. The screen stays on for this, but Ubuntu Touch suspends apps you leave
- Office support is optional. Installing it downloads about 200 MB from The Document Foundation, checked against a fixed hash, and takes about 660 MB on the phone. It prepares documents only: it doesn't add the LibreOffice apps for editing
- Scanning uses the flatbed only, and needs an eSCL scanner. Document feeders aren't supported yet
- Scans save as PDF or JPEG, not PNG
- No USB printers
- Printers use self-signed certificates, so the connection is encrypted but the printer's identity isn't verified

## Permissions
- `networking`, to reach the printer and to download LibreOffice if you ask for it
- `content_exchange`, to receive files from other apps
- `content_exchange_source`, to hand scans to other apps
- `keep-display-on`, so the phone doesn't lock and suspend the app during a job

## Build
```
clickable build --arch arm64
```
The `pdf2pwg` and `office2pdf` helpers in `native/` are compiled, and Poppler is bundled with the app, so each architecture needs its own build.

## Test
```
python3 -m unittest discover -s tests
```

`tests/pwg_check.py` reads a raster file with libcups and compares its pages with `pdftoppm`.

`tests/fake_printer.py` is a small simulated printer for testing without paper or ink:
```
python3 tests/fake_printer.py --port 8631
```

## License
GPL-3.0. Ubuntu Touch edition of [LibrePrint](https://github.com/juujbyg/libreprint).
