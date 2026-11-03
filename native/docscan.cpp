// Turns a photo of a document into a scan: finds the page, straightens it and evens out
// the lighting. It has no OpenCV, so that the click stays small.
//
// Usage: docscan --detect IN
//            Prints 8 numbers from 0 to 1, the page's corners in the order top left, top
//            right, bottom right, bottom left, then 1 if a page was found or 0 if not.
//        docscan --warp IN OUT MODE X1 Y1 X2 Y2 X3 Y3 X4 Y4
//            MODE is colour, grey or text. Corners are in the same order.
#include <QCoreApplication>
#include <QImage>
#include <QImageReader>
#include <QPainter>
#include <QPolygonF>
#include <QTransform>

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <vector>

enum Status { Done = 0, BadArguments = 2, Unreadable = 3, WriteFailed = 4, BadCorners = 5 };

static const qint64 MAX_SOURCE_PIXELS = 200000000;
static const int MAX_PHOTO = 8000;
static const int DETECT_SIZE = 640;
static const int MAX_OUTPUT = 3508;  // The long side of A4 at 300 dpi.
static const int MAX_HULL = 64;
static const int JPEG_QUALITY = 85;
static const double A4_RATIO = 1.414;
static const double A4_MARGIN = 0.1;
static const double MIN_PAGE_AREA = 0.15;  // Of the photo. Less is probably not a page.
static const double MAX_PAGE_AREA = 0.985;
static const double DEFAULT_INSET = 0.05;

static QImage loadUpright(const char *path)
{
    QImageReader reader(QString::fromUtf8(path));
    reader.setAutoTransform(true);
    // Check before decoding: a tiny file can unpack to more memory than the phone has.
    const QSize size = reader.size();
    if (!size.isValid() || qint64(size.width()) * size.height() > MAX_SOURCE_PIXELS) {
        return QImage();
    }
    if (size.width() > MAX_PHOTO || size.height() > MAX_PHOTO) {
        reader.setScaledSize(size.scaled(MAX_PHOTO, MAX_PHOTO, Qt::KeepAspectRatio));
    }
    return reader.read().convertToFormat(QImage::Format_RGB32);
}

static int median(std::vector<int> &values)
{
    std::nth_element(values.begin(), values.begin() + values.size() / 2, values.end());
    return values[values.size() / 2];
}

// The threshold that best splits an image's values into two groups.
static int otsu(const std::vector<int> &values, int top)
{
    std::vector<qint64> counts(top + 1, 0);
    qint64 total = 0;
    qint64 sum = 0;
    for (int value : values) {
        ++counts[std::min(value, top)];
    }
    for (int value = 0; value <= top; ++value) {
        total += counts[value];
        sum += value * counts[value];
    }
    qint64 lowCount = 0;
    qint64 lowSum = 0;
    double best = -1;
    int threshold = 0;
    for (int value = 0; value <= top; ++value) {
        lowCount += counts[value];
        lowSum += value * counts[value];
        const qint64 highCount = total - lowCount;
        if (lowCount == 0 || highCount == 0) {
            continue;
        }
        const double lowMean = double(lowSum) / lowCount;
        const double highMean = double(sum - lowSum) / highCount;
        const double spread = double(lowCount) * highCount * (lowMean - highMean) * (lowMean - highMean);
        if (spread > best) {
            best = spread;
            threshold = value;
        }
    }
    return threshold;
}

// A running average of one edge of the photo, so a mark there doesn't count as the surface.
static std::vector<double> smoothed(const std::vector<int> &edge)
{
    const int reach = std::max(4, int(edge.size()) / 12);
    std::vector<double> result(edge.size());
    for (int i = 0; i < int(edge.size()); ++i) {
        std::vector<int> nearby;
        for (int j = std::max(0, i - reach); j <= std::min(int(edge.size()) - 1, i + reach); ++j) {
            nearby.push_back(edge[j]);
        }
        result[i] = median(nearby);
    }
    return result;
}

// How unlike its surroundings each pixel is. The surface is estimated from the photo's four
// edges, blended across the picture. That finds a page on a light table as well as a dark
// one, and a shadow across the table doesn't look like an object.
static std::vector<int> differenceFromSurroundings(const QImage &image)
{
    const int width = image.width();
    const int height = image.height();
    typedef int (*Channel)(QRgb);
    const Channel channels[3] = {qRed, qGreen, qBlue};
    std::vector<int> raw(size_t(width) * height, 0);
    for (const Channel channel : channels) {
        std::vector<int> top(width), bottom(width), left(height), right(height);
        for (int x = 0; x < width; ++x) {
            top[x] = channel(image.pixel(x, 0));
            bottom[x] = channel(image.pixel(x, height - 1));
        }
        for (int y = 0; y < height; ++y) {
            left[y] = channel(image.pixel(0, y));
            right[y] = channel(image.pixel(width - 1, y));
        }
        const std::vector<double> smoothTop = smoothed(top);
        const std::vector<double> smoothBottom = smoothed(bottom);
        const std::vector<double> smoothLeft = smoothed(left);
        const std::vector<double> smoothRight = smoothed(right);
        for (int y = 0; y < height; ++y) {
            const QRgb *line = reinterpret_cast<const QRgb *>(image.constScanLine(y));
            const double down = double(y) / (height - 1);
            for (int x = 0; x < width; ++x) {
                const double across = double(x) / (width - 1);
                const double expected = (smoothTop[x] * (1 - down) + smoothBottom[x] * down
                                         + smoothLeft[y] * (1 - across) + smoothRight[y] * across) / 2;
                raw[size_t(y) * width + x] += int(std::abs(channel(line[x]) - expected));
            }
        }
    }
    // A small blur keeps the grain of a table from looking like the page.
    std::vector<int> smooth(raw.size());
    for (int y = 0; y < height; ++y) {
        for (int x = 0; x < width; ++x) {
            int sum = 0;
            int count = 0;
            for (int dy = -2; dy <= 2; ++dy) {
                for (int dx = -2; dx <= 2; ++dx) {
                    const int nx = x + dx;
                    const int ny = y + dy;
                    if (nx >= 0 && ny >= 0 && nx < width && ny < height) {
                        sum += raw[size_t(ny) * width + nx];
                        ++count;
                    }
                }
            }
            smooth[size_t(y) * width + x] = sum / count;
        }
    }
    return smooth;
}

// Marks everything reachable from the given starting points through allowed pixels.
static void flood(std::vector<char> &marks, const std::vector<char> &allowed,
                  std::vector<int> &queue, int width, int height)
{
    while (!queue.empty()) {
        const int at = queue.back();
        queue.pop_back();
        const int x = at % width;
        const int y = at / width;
        const int next[4] = {x > 0 ? at - 1 : -1, x + 1 < width ? at + 1 : -1,
                             y > 0 ? at - width : -1, y + 1 < height ? at + width : -1};
        for (int neighbour : next) {
            if (neighbour >= 0 && allowed[neighbour] && !marks[neighbour]) {
                marks[neighbour] = 1;
                queue.push_back(neighbour);
            }
        }
    }
}

// The largest area that the surroundings can't reach. Text is ignored this way, because
// the writing on a page is enclosed by the paper.
static std::vector<char> largestRegion(const std::vector<int> &difference, int width, int height)
{
    const int threshold = otsu(difference, 765);
    std::vector<char> outsideAllowed(difference.size());
    for (size_t i = 0; i < difference.size(); ++i) {
        outsideAllowed[i] = difference[i] <= threshold;
    }
    std::vector<char> outside(difference.size(), 0);
    std::vector<int> queue;
    const auto seed = [&](int at) {
        if (outsideAllowed[at] && !outside[at]) {
            outside[at] = 1;
            queue.push_back(at);
        }
    };
    for (int x = 0; x < width; ++x) {
        seed(x);
        seed((height - 1) * width + x);
    }
    for (int y = 0; y < height; ++y) {
        seed(y * width);
        seed(y * width + width - 1);
    }
    flood(outside, outsideAllowed, queue, width, height);

    std::vector<char> inside(difference.size());
    for (size_t i = 0; i < difference.size(); ++i) {
        inside[i] = !outside[i];
    }
    std::vector<char> seen(difference.size(), 0);
    std::vector<char> best;
    size_t bestSize = 0;
    for (size_t start = 0; start < difference.size(); ++start) {
        if (!inside[start] || seen[start]) {
            continue;
        }
        std::vector<char> region(difference.size(), 0);
        region[start] = 1;
        queue.assign(1, int(start));
        flood(region, inside, queue, width, height);
        size_t count = 0;
        for (size_t i = 0; i < region.size(); ++i) {
            if (region[i]) {
                seen[i] = 1;
                ++count;
            }
        }
        if (count > bestSize) {
            bestSize = count;
            best.swap(region);
        }
    }
    return best;
}

static double cross(const QPointF &a, const QPointF &b, const QPointF &c)
{
    return (b.x() - a.x()) * (c.y() - a.y()) - (b.y() - a.y()) * (c.x() - a.x());
}

static std::vector<QPointF> convexHull(std::vector<QPointF> points)
{
    std::sort(points.begin(), points.end(), [](const QPointF &a, const QPointF &b) {
        return a.x() < b.x() || (a.x() == b.x() && a.y() < b.y());
    });
    std::vector<QPointF> hull(points.size() * 2);
    size_t size = 0;
    for (const QPointF &point : points) {
        while (size >= 2 && cross(hull[size - 2], hull[size - 1], point) <= 0) {
            --size;
        }
        hull[size++] = point;
    }
    const size_t lower = size + 1;
    for (size_t i = points.size() - 1; i-- > 0;) {
        while (size >= lower && cross(hull[size - 2], hull[size - 1], points[i]) <= 0) {
            --size;
        }
        hull[size++] = points[i];
    }
    hull.resize(size > 0 ? size - 1 : 0);
    return hull;
}

// The four hull points enclosing the most area, which are the corners of a page, even
// with its corners slightly rounded or curled.
static bool bestQuad(std::vector<QPointF> hull, QPointF quad[4])
{
    if (hull.size() > size_t(MAX_HULL)) {
        std::vector<QPointF> fewer;
        for (int i = 0; i < MAX_HULL; ++i) {
            fewer.push_back(hull[size_t(i) * hull.size() / MAX_HULL]);
        }
        hull.swap(fewer);
    }
    const int count = int(hull.size());
    if (count < 4) {
        return false;
    }
    double best = -1;
    for (int a = 0; a < count; ++a) {
        for (int c = a + 2; c < count; ++c) {
            double firstArea = 0;
            double secondArea = 0;
            int first = -1;
            int second = -1;
            for (int b = 0; b < count; ++b) {
                if (b == a || b == c) {
                    continue;
                }
                const double area = cross(hull[a], hull[c], hull[b]);
                if (b > a && b < c && std::abs(area) > firstArea) {
                    firstArea = std::abs(area);
                    first = b;
                } else if ((b < a || b > c) && std::abs(area) > secondArea) {
                    secondArea = std::abs(area);
                    second = b;
                }
            }
            if (first >= 0 && second >= 0 && firstArea + secondArea > best) {
                best = firstArea + secondArea;
                quad[0] = hull[a];
                quad[1] = hull[first];
                quad[2] = hull[c];
                quad[3] = hull[second];
            }
        }
    }
    return best > 0;
}

// Puts corners in reading order: top left first, then clockwise as seen on screen.
static void order(QPointF quad[4])
{
    QPointF middle;
    for (int i = 0; i < 4; ++i) {
        middle += quad[i] / 4;
    }
    std::sort(quad, quad + 4, [&](const QPointF &a, const QPointF &b) {
        return std::atan2(a.y() - middle.y(), a.x() - middle.x())
               < std::atan2(b.y() - middle.y(), b.x() - middle.x());
    });
    int first = 0;
    for (int i = 1; i < 4; ++i) {
        if (quad[i].x() + quad[i].y() < quad[first].x() + quad[first].y()) {
            first = i;
        }
    }
    std::rotate(quad, quad + first, quad + 4);
}

static double quadArea(const QPointF quad[4])
{
    double doubled = 0;
    for (int i = 0; i < 4; ++i) {
        const QPointF &a = quad[i];
        const QPointF &b = quad[(i + 1) % 4];
        doubled += a.x() * b.y() - b.x() * a.y();
    }
    return std::abs(doubled) / 2;
}

static bool findPage(const QImage &photo, QPointF quad[4])
{
    const QImage small = photo.scaled(DETECT_SIZE, DETECT_SIZE, Qt::KeepAspectRatio,
                                      Qt::SmoothTransformation);
    const int width = small.width();
    const int height = small.height();
    if (width < 8 || height < 8) {
        return false;
    }
    const std::vector<char> region = largestRegion(differenceFromSurroundings(small), width, height);
    if (region.empty()) {
        return false;
    }
    // The ends of each row are enough to outline the shape.
    std::vector<QPointF> edge;
    for (int y = 0; y < height; ++y) {
        int left = -1;
        int right = -1;
        for (int x = 0; x < width; ++x) {
            if (region[size_t(y) * width + x]) {
                left = left < 0 ? x : left;
                right = x;
            }
        }
        if (left >= 0) {
            edge.push_back(QPointF(left, y));
            edge.push_back(QPointF(right, y));
        }
    }
    if (edge.size() < 4 || !bestQuad(convexHull(edge), quad)) {
        return false;
    }
    for (int i = 0; i < 4; ++i) {
        quad[i] = QPointF((quad[i].x() + 0.5) / width, (quad[i].y() + 0.5) / height);
    }
    order(quad);
    const double area = quadArea(quad);
    return area >= MIN_PAGE_AREA && area <= MAX_PAGE_AREA;
}

static int detect(const char *path)
{
    const QImage photo = loadUpright(path);
    if (photo.isNull()) {
        return Unreadable;
    }
    QPointF quad[4];
    const bool found = findPage(photo, quad);
    if (!found) {
        quad[0] = QPointF(DEFAULT_INSET, DEFAULT_INSET);
        quad[1] = QPointF(1 - DEFAULT_INSET, DEFAULT_INSET);
        quad[2] = QPointF(1 - DEFAULT_INSET, 1 - DEFAULT_INSET);
        quad[3] = QPointF(DEFAULT_INSET, 1 - DEFAULT_INSET);
    }
    for (int i = 0; i < 4; ++i) {
        std::printf("%.5f %.5f ", quad[i].x(), quad[i].y());
    }
    std::printf("%d\n", found ? 1 : 0);
    return Done;
}

static double distance(const QPointF &a, const QPointF &b)
{
    return std::hypot(a.x() - b.x(), a.y() - b.y());
}

static QImage straighten(const QImage &photo, const QPointF corners[4])
{
    QPolygonF source;
    for (int i = 0; i < 4; ++i) {
        source << QPointF(corners[i].x() * photo.width(), corners[i].y() * photo.height());
    }
    double wide = (distance(source[0], source[1]) + distance(source[3], source[2])) / 2;
    double high = (distance(source[0], source[3]) + distance(source[1], source[2])) / 2;
    if (wide < 8 || high < 8) {
        return QImage();
    }
    // A sheet photographed at an angle looks shorter than it is, so close to A4 means A4.
    const double ratio = std::max(wide, high) / std::min(wide, high);
    if (std::abs(ratio - A4_RATIO) < A4_MARGIN) {
        if (high >= wide) {
            high = std::max(high, wide * A4_RATIO);
            wide = high / A4_RATIO;
        } else {
            wide = std::max(wide, high * A4_RATIO);
            high = wide / A4_RATIO;
        }
    }
    const double scale = std::min(1.0, MAX_OUTPUT / std::max(wide, high));
    const int width = std::max(1, int(wide * scale));
    const int height = std::max(1, int(high * scale));
    QPolygonF target;
    target << QPointF(0, 0) << QPointF(width, 0) << QPointF(width, height) << QPointF(0, height);
    QTransform transform;
    if (!QTransform::quadToQuad(source, target, transform)) {
        return QImage();
    }
    QImage result(width, height, QImage::Format_RGB32);
    result.fill(Qt::white);
    QPainter painter(&result);
    painter.setRenderHint(QPainter::SmoothPixmapTransform);
    painter.setTransform(transform);
    painter.drawImage(0, 0, photo);
    painter.end();
    return result;
}

// An estimate of the paper's brightness everywhere, without the writing on it. Shadows
// and uneven light vary slowly, so a much smaller image can hold them.
static QImage paperLight(const QImage &image)
{
    const int shrink = 16;
    const QImage small = image.convertToFormat(QImage::Format_Grayscale8)
                             .scaled(std::max(8, image.width() / shrink),
                                     std::max(8, image.height() / shrink),
                                     Qt::IgnoreAspectRatio, Qt::SmoothTransformation)
                             .convertToFormat(QImage::Format_Grayscale8);  // Scaling changes it.
    const int width = small.width();
    const int height = small.height();
    QImage brightest(width, height, QImage::Format_Grayscale8);
    const int reach = 6;
    for (int y = 0; y < height; ++y) {
        uchar *out = brightest.scanLine(y);
        for (int x = 0; x < width; ++x) {
            int best = 0;
            for (int dy = std::max(0, y - reach); dy <= std::min(height - 1, y + reach); ++dy) {
                const uchar *line = small.constScanLine(dy);
                for (int dx = std::max(0, x - reach); dx <= std::min(width - 1, x + reach); ++dx) {
                    best = std::max(best, int(line[dx]));
                }
            }
            out[x] = uchar(best);
        }
    }
    return brightest.scaled(image.size(), Qt::IgnoreAspectRatio, Qt::SmoothTransformation)
        .convertToFormat(QImage::Format_Grayscale8);
}

static int level(double value)
{
    // Pushes paper to white and ink to black, the way a scanner's light does.
    const double black = 45;
    const double white = 225;
    return std::max(0, std::min(255, int((value - black) * 255 / (white - black))));
}

static QImage cleaned(const QImage &image, const QByteArray &mode)
{
    const QImage light = paperLight(image);
    const bool colour = mode == "colour";
    const bool text = mode == "text";
    QImage result(image.size(), colour ? QImage::Format_RGB32 : QImage::Format_Grayscale8);
    for (int y = 0; y < image.height(); ++y) {
        const QRgb *in = reinterpret_cast<const QRgb *>(image.constScanLine(y));
        const uchar *paper = light.constScanLine(y);
        uchar *out = result.scanLine(y);
        for (int x = 0; x < image.width(); ++x) {
            const double gain = 255.0 / std::max(40, int(paper[x]));
            if (colour) {
                reinterpret_cast<QRgb *>(out)[x] = qRgb(level(qRed(in[x]) * gain),
                                                        level(qGreen(in[x]) * gain),
                                                        level(qBlue(in[x]) * gain));
            } else {
                const int shade = level(qGray(in[x]) * gain);
                out[x] = uchar(text ? (shade < 165 ? 0 : 255) : shade);
            }
        }
    }
    return result;
}

static int warp(int argc, char **argv)
{
    const QByteArray mode = argv[4];
    if (argc != 13 || (mode != "colour" && mode != "grey" && mode != "text")) {
        return BadArguments;
    }
    QPointF corners[4];
    for (int i = 0; i < 4; ++i) {
        corners[i] = QPointF(std::atof(argv[5 + i * 2]), std::atof(argv[6 + i * 2]));
    }
    const QImage photo = loadUpright(argv[2]);
    if (photo.isNull()) {
        return Unreadable;
    }
    const QImage flat = straighten(photo, corners);
    if (flat.isNull()) {
        return BadCorners;
    }
    return cleaned(flat, mode).save(QString::fromUtf8(argv[3]), "JPEG", JPEG_QUALITY) ? Done : WriteFailed;
}

int main(int argc, char **argv)
{
    QCoreApplication application(argc, argv);  // Image format plugins need it.
    if (argc == 3 && QByteArray(argv[1]) == "--detect") {
        return detect(argv[2]);
    }
    if (argc >= 5 && QByteArray(argv[1]) == "--warp") {
        return warp(argc, argv);
    }
    std::fprintf(stderr, "docscan: unexpected arguments\n");
    return BadArguments;
}
