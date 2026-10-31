/*
 * Converts one document to PDF with LibreOfficeKit, then exits.
 * Usage: office2pdf PROGRAM_DIR PROFILE_URL SOURCE_URL TARGET_URL
 *
 * It uses LibreOfficeKit rather than soffice, which needs a socket in /tmp that confined
 * apps can't create. Document Viewer works the same way. The structs below mirror the
 * start of LibreOffice's stable C API in LibreOfficeKit.h, which only grows at the end.
 */
#include <dlfcn.h>
#include <limits.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

enum { DONE = 0, BAD_USAGE = 1, BAD_DOCUMENT = 3, SAVE_FAILED = 4, NO_LIBRARY = 5 };

struct Kit;
struct Document;

struct KitClass {
    size_t size;
    void (*destroy)(struct Kit *kit);
    struct Document *(*documentLoad)(struct Kit *kit, const char *url);
    char *(*getError)(struct Kit *kit);
};

struct Kit {
    struct KitClass *cls;
};

struct DocumentClass {
    size_t size;
    void (*destroy)(struct Document *document);
    int (*saveAs)(struct Document *document, const char *url, const char *format, const char *options);
};

struct Document {
    struct DocumentClass *cls;
};

typedef struct Kit *(*Hook)(const char *program, const char *profile);

static Hook find_hook(const char *program)
{
    /* Official builds merge most of LibreOffice into one library. */
    static const char *const names[] = {"libsofficeapp.so", "libmergedlo.so"};
    for (size_t i = 0; i < sizeof names / sizeof names[0]; ++i) {
        char path[PATH_MAX];
        snprintf(path, sizeof path, "%s/%s", program, names[i]);
        void *library = dlopen(path, RTLD_LAZY | RTLD_GLOBAL);
        if (library != NULL) {
            Hook hook = (Hook)dlsym(library, "libreofficekit_hook_2");
            if (hook != NULL) {
                return hook;
            }
        }
    }
    fprintf(stderr, "office2pdf: %s\n", dlerror());
    return NULL;
}

int main(int argc, char **argv)
{
    if (argc != 5) {
        fprintf(stderr, "office2pdf: expected 4 arguments, got %d\n", argc - 1);
        return BAD_USAGE;
    }
    Hook hook = find_hook(argv[1]);
    struct Kit *kit = hook != NULL ? hook(argv[1], argv[2]) : NULL;
    if (kit == NULL) {
        return NO_LIBRARY;
    }
    struct Document *document = kit->cls->documentLoad(kit, argv[3]);
    if (document == NULL) {
        fprintf(stderr, "office2pdf: %s\n", kit->cls->getError(kit));
        fflush(stderr);
        _exit(BAD_DOCUMENT);
    }
    int saved = document->cls->saveAs(document, argv[4], "pdf", NULL);
    /* LibreOffice's shutdown is slow and the work is done, so leave without it. */
    _exit(saved ? DONE : SAVE_FAILED);
}
