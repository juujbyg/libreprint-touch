// Renders a PDF to PWG Raster, the page format driverless printers accept.
// Usage: pdf2pwg IN OUT DPI WIDTH HEIGHT TYPE SIDES SHEET_BACK COPIES QUALITY MEDIA ORDER PAGES
// WIDTH and HEIGHT are in hundredths of a millimetre, as IPP reports them.
// PAGES looks like 1-3,5, or is empty for the whole document.

#include <poppler-qt5.h>

#include <QByteArray>
#include <QCoreApplication>
#include <QFile>
#include <QImage>
#include <QImageReader>
#include <QPainter>
#include <QScopedPointer>
#include <QVector>
#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>

namespace {

const int HEADER_SIZE = 1796;
const int BLANK = -1;
const int MAX_PHOTO = 3000;
const int JPEG_QUALITY = 92;

enum ExitCode { Done = 0, BadUsage = 1, Unreadable = 2, Locked = 3, WriteFailed = 4, NoPages = 5 };

struct Sheet {
    int width;
    int height;
    int pointsWide;
    int pointsHigh;
    int bytesPerPixel;
    int dpi;
    bool duplex;
    bool tumble;
    bool flipBackX;
    bool flipBackY;
    unsigned quality;
    unsigned total;
    QByteArray media;
};

void put32(QByteArray &header, int offset, unsigned value)
{
    header[offset] = char(value >> 24);
    header[offset + 1] = char(value >> 16);
    header[offset + 2] = char(value >> 8);
    header[offset + 3] = char(value);
}

void putText(QByteArray &header, int offset, const QByteArray &text)
{
    std::memcpy(header.data() + offset, text.constData(), size_t(qMin(text.size(), 63)));
}

QByteArray makeHeader(const Sheet &sheet, bool back)
{
    const unsigned identity = 1;
    const unsigned reversed = 0xFFFFFFFFu;
    const unsigned bytesPerLine = unsigned(sheet.width * sheet.bytesPerPixel);

    QByteArray header(HEADER_SIZE, '\0');
    putText(header, 0, "PwgRaster");
    put32(header, 272, sheet.duplex);
    put32(header, 276, unsigned(sheet.dpi));
    put32(header, 280, unsigned(sheet.dpi));
    put32(header, 352, unsigned(sheet.pointsWide));
    put32(header, 356, unsigned(sheet.pointsHigh));
    put32(header, 368, sheet.tumble);
    put32(header, 372, unsigned(sheet.width));
    put32(header, 376, unsigned(sheet.height));
    put32(header, 384, 8);
    put32(header, 388, unsigned(sheet.bytesPerPixel * 8));
    put32(header, 392, bytesPerLine);
    put32(header, 400, sheet.bytesPerPixel == 1 ? 18 : 19);
    put32(header, 420, unsigned(sheet.bytesPerPixel));
    put32(header, 452, sheet.total);
    put32(header, 456, back && sheet.flipBackX ? reversed : identity);
    put32(header, 460, back && sheet.flipBackY ? reversed : identity);
    put32(header, 472, unsigned(sheet.width));
    put32(header, 476, unsigned(sheet.height));
    put32(header, 480, 0x00FFFFFFu);
    put32(header, 484, sheet.quality);
    putText(header, 1732, sheet.media);
    return header;
}

// PWG lines are PackBits-like: 0-127 repeats one pixel, 129-255 starts literal pixels.
void packLine(const uchar *line, int width, int bpp, QByteArray &out)
{
    const auto same = [&](int a, int b) {
        return std::memcmp(line + a * bpp, line + b * bpp, size_t(bpp)) == 0;
    };
    int x = 0;
    while (x < width) {
        int run = 1;
        while (x + run < width && run < 128 && same(x, x + run)) {
            ++run;
        }
        if (run > 1 || x + 1 == width) {
            out.append(char(run - 1));
            out.append(reinterpret_cast<const char *>(line + x * bpp), bpp);
            x += run;
            continue;
        }
        const int start = x;
        ++x;
        while (x < width && x - start < 128 && !(x + 1 < width && same(x, x + 1))) {
            ++x;
        }
        const int count = x - start;
        out.append(char(count == 1 ? 0 : 257 - count));
        out.append(reinterpret_cast<const char *>(line + start * bpp), count * bpp);
    }
}

QByteArray packPage(const QByteArray &pixels, const Sheet &sheet)
{
    const int stride = sheet.width * sheet.bytesPerPixel;
    const uchar *data = reinterpret_cast<const uchar *>(pixels.constData());
    QByteArray out;
    int y = 0;
    while (y < sheet.height) {
        int repeat = 1;
        while (y + repeat < sheet.height && repeat < 256 &&
               std::memcmp(data + y * stride, data + (y + repeat) * stride, size_t(stride)) == 0) {
            ++repeat;
        }
        out.append(char(repeat - 1));
        packLine(data + y * stride, sheet.width, sheet.bytesPerPixel, out);
        y += repeat;
    }
    return out;
}

// Fits the page on the sheet, centred, turning it sideways when that suits it better.
QByteArray renderPage(Poppler::Document &document, int index, const Sheet &sheet, bool back)
{
    QByteArray pixels(sheet.width * sheet.height * sheet.bytesPerPixel, char(0xFF));
    if (index == BLANK) {
        return pixels;
    }
    QScopedPointer<Poppler::Page> page(document.page(index));
    if (page.isNull()) {
        return pixels;
    }
    const QSizeF size = page->pageSizeF();
    const bool sideways = (size.width() > size.height()) != (sheet.width > sheet.height);
    const double wide = (sideways ? size.height() : size.width()) * sheet.dpi / 72.0;
    const double high = (sideways ? size.width() : size.height()) * sheet.dpi / 72.0;
    const double scale = qMin(1.0, qMin(sheet.width / wide, sheet.height / high));
    const double resolution = sheet.dpi * scale;

    QImage image = page->renderToImage(resolution, resolution, -1, -1, -1, -1,
                                       sideways ? Poppler::Page::Rotate90 : Poppler::Page::Rotate0);
    if (image.isNull()) {
        return pixels;
    }
    image = image.convertToFormat(sheet.bytesPerPixel == 1 ? QImage::Format_Grayscale8
                                                           : QImage::Format_RGB888);
    const int columns = qMin(image.width(), sheet.width);
    const int rows = qMin(image.height(), sheet.height);
    const int left = (sheet.width - columns) / 2;
    const int top = (sheet.height - rows) / 2;
    const int stride = sheet.width * sheet.bytesPerPixel;
    for (int y = 0; y < rows; ++y) {
        std::memcpy(pixels.data() + (top + y) * stride + left * sheet.bytesPerPixel,
                    image.constScanLine(y), size_t(columns * sheet.bytesPerPixel));
    }
    if (back && (sheet.flipBackX || sheet.flipBackY)) {
        QImage whole(reinterpret_cast<const uchar *>(pixels.constData()), sheet.width, sheet.height,
                     stride,
                     sheet.bytesPerPixel == 1 ? QImage::Format_Grayscale8 : QImage::Format_RGB888);
        const QImage flipped = whole.mirrored(sheet.flipBackX, sheet.flipBackY);
        for (int y = 0; y < sheet.height; ++y) {
            std::memcpy(pixels.data() + y * stride, flipped.constScanLine(y), size_t(stride));
        }
    }
    return pixels;
}

QVector<int> selectPages(const QByteArray &ranges, int count)
{
    QVector<int> pages;
    if (ranges.isEmpty()) {
        for (int i = 0; i < count; ++i) {
            pages.append(i);
        }
        return pages;
    }
    for (const QByteArray &part : ranges.split(',')) {
        const QList<QByteArray> ends = part.split('-');
        const int first = ends.first().toInt();
        const int last = ends.last().toInt();
        for (int number = first; number <= last && number <= count; ++number) {
            if (number >= 1) {
                pages.append(number - 1);
            }
        }
    }
    return pages;
}

// Face-up trays stack the first sheet at the bottom, so one-sided jobs go out last page first.
QVector<int> pageOrder(const QVector<int> &pages, bool duplex, bool reverse)
{
    QVector<int> order = pages;
    if (duplex && order.size() % 2 == 1) {
        order.append(BLANK);
    }
    if (reverse) {
        std::reverse(order.begin(), order.end());
    }
    return order;
}

}  // namespace

// Draws one page for the screen and prints how many pages the document has.
static int writePreview(const char *in, const char *out, int number, int width)
{
    QScopedPointer<Poppler::Document> document(Poppler::Document::load(QString::fromUtf8(in)));
    if (document.isNull()) {
        return Unreadable;
    }
    if (document->isLocked()) {
        return Locked;
    }
    if (number < 1 || number > document->numPages()) {
        return NoPages;
    }
    document->setRenderHint(Poppler::Document::Antialiasing);
    document->setRenderHint(Poppler::Document::TextAntialiasing);
    document->setPaperColor(Qt::white);
    QScopedPointer<Poppler::Page> page(document->page(number - 1));
    if (page.isNull()) {
        return Unreadable;
    }
    const double dpi = 72.0 * width / page->pageSizeF().width();
    if (!page->renderToImage(dpi, dpi).save(QString::fromUtf8(out), "PNG")) {
        return WriteFailed;
    }
    std::printf("%d\n", document->numPages());
    return Done;
}

// Most printers that take photos take only JPEG. Transparency prints as white paper.
static int writeJpeg(const char *in, const char *out)
{
    QImageReader reader(QString::fromUtf8(in));
    reader.setAutoTransform(true);
    QImage image = reader.read();
    if (image.isNull()) {
        return Unreadable;
    }
    if (image.width() > MAX_PHOTO || image.height() > MAX_PHOTO) {
        image = image.scaled(MAX_PHOTO, MAX_PHOTO, Qt::KeepAspectRatio, Qt::SmoothTransformation);
    }
    QImage flat(image.size(), QImage::Format_RGB32);
    flat.fill(Qt::white);
    QPainter painter(&flat);
    painter.drawImage(0, 0, image);
    painter.end();
    return flat.save(QString::fromUtf8(out), "JPEG", JPEG_QUALITY) ? Done : WriteFailed;
}

int main(int argc, char **argv)
{
    if (argc == 6 && QByteArray(argv[1]) == "--preview") {
        return writePreview(argv[2], argv[3], std::atoi(argv[4]), std::atoi(argv[5]));
    }
    if (argc == 4 && QByteArray(argv[1]) == "--jpeg") {
        QCoreApplication application(argc, argv);  // Image format plugins need it.
        return writeJpeg(argv[2], argv[3]);
    }
    if (argc != 14) {
        std::fprintf(stderr, "pdf2pwg: expected 13 arguments, got %d\n", argc - 1);
        return BadUsage;
    }
    const int dpi = std::atoi(argv[3]);
    const int hundredthsWide = std::atoi(argv[4]);
    const int hundredthsHigh = std::atoi(argv[5]);
    const QByteArray type = argv[6];
    const QByteArray sides = argv[7];
    const QByteArray sheetBack = argv[8];
    const int copies = std::atoi(argv[9]);
    const bool reverse = QByteArray(argv[12]) == "reverse";
    if (dpi <= 0 || hundredthsWide <= 0 || hundredthsHigh <= 0 || copies <= 0 ||
        (type != "sgray_8" && type != "srgb_8")) {
        std::fprintf(stderr, "pdf2pwg: invalid arguments\n");
        return BadUsage;
    }

    QScopedPointer<Poppler::Document> document(Poppler::Document::load(QString::fromUtf8(argv[1])));
    if (document.isNull()) {
        std::fprintf(stderr, "pdf2pwg: can't read %s\n", argv[1]);
        return Unreadable;
    }
    if (document->isLocked()) {
        return Locked;
    }
    document->setRenderHint(Poppler::Document::Antialiasing);
    document->setRenderHint(Poppler::Document::TextAntialiasing);
    document->setPaperColor(Qt::white);

    Sheet sheet;
    sheet.dpi = dpi;
    sheet.width = hundredthsWide * dpi / 2540;
    sheet.height = hundredthsHigh * dpi / 2540;
    sheet.pointsWide = hundredthsWide * 72 / 2540;
    sheet.pointsHigh = hundredthsHigh * 72 / 2540;
    sheet.bytesPerPixel = type == "sgray_8" ? 1 : 3;
    sheet.duplex = sides != "one-sided";
    sheet.tumble = sides == "two-sided-short-edge";
    // Same rules as CUPS' cupsRasterInitPWGHeader.
    const bool rotated = (sheetBack == "manual-tumble" && sheet.tumble) ||
                         (sheetBack == "rotated" && !sheet.tumble);
    sheet.flipBackX = rotated || (sheetBack == "flipped" && sheet.tumble);
    sheet.flipBackY = rotated || (sheetBack == "flipped" && !sheet.tumble);
    sheet.quality = unsigned(std::atoi(argv[10]));
    sheet.media = argv[11];

    const QVector<int> chosen = selectPages(QByteArray(argv[13]), document->numPages());
    if (chosen.isEmpty()) {
        return NoPages;
    }
    const QVector<int> order = pageOrder(chosen, sheet.duplex, reverse);
    sheet.total = unsigned(order.size() * copies);

    QFile out(QString::fromUtf8(argv[2]));
    if (!out.open(QIODevice::WriteOnly) || out.write("RaS2", 4) != 4) {
        return WriteFailed;
    }
    const qint64 start = out.pos();
    for (int position = 0; position < order.size(); ++position) {
        // Sheet sides follow the order pages leave the printer, not their numbers.
        const bool back = sheet.duplex && position % 2 == 1;
        const QByteArray pixels = renderPage(*document, order[position], sheet, back);
        if (out.write(makeHeader(sheet, back)) != HEADER_SIZE) {
            return WriteFailed;
        }
        const QByteArray packed = packPage(pixels, sheet);
        if (out.write(packed) != packed.size()) {
            return WriteFailed;
        }
        std::printf("page %d %d\n", position + 1, order.size());
        std::fflush(stdout);
    }
    const qint64 setSize = out.pos() - start;
    out.close();

    if (copies > 1) {
        // The printer can't repeat a raster job, so the file holds every copy.
        QFile set(QString::fromUtf8(argv[2]));
        if (!set.open(QIODevice::ReadWrite)) {
            return WriteFailed;
        }
        set.seek(start);
        const QByteArray body = set.read(setSize);
        set.seek(set.size());
        for (int copy = 1; copy < copies; ++copy) {
            if (set.write(body) != body.size()) {
                return WriteFailed;
            }
        }
    }
    return Done;
}
