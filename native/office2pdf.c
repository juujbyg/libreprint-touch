/*
 * Converts one document to PDF with LibreOfficeKit, then exits.
 * Usage: office2pdf PROGRAM_DIR PROFILE_URL SOURCE_URL TARGET_URL
 *        office2pdf --check-network   (exit 0 if the network block works)
 *
 * It uses LibreOfficeKit rather than soffice, which needs a socket in /tmp that confined
 * apps can't create. Document Viewer works the same way. The structs below mirror the
 * start of LibreOffice's stable C API in LibreOfficeKit.h, which only grows at the end.
 *
 * Documents come from anywhere, so before LibreOffice is loaded a seccomp filter stops
 * this process and anything it starts from opening internet sockets. A document then
 * can't fetch remote images or stylesheets, call a web service or reach devices on the
 * local network while it's converted, whatever LibreOffice's settings or bugs allow.
 * Local (Unix) sockets still work. The filter can't be removed once installed.
 */
#include <dlfcn.h>
#include <errno.h>
#include <limits.h>
#include <linux/audit.h>
#include <linux/filter.h>
#include <linux/seccomp.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/socket.h>
#include <sys/syscall.h>
#include <unistd.h>

enum { DONE = 0, BAD_USAGE = 1, BAD_DOCUMENT = 3, SAVE_FAILED = 4, NO_LIBRARY = 5, NO_SANDBOX = 6 };

#if defined(__aarch64__)
#define NATIVE_ARCH AUDIT_ARCH_AARCH64
#elif defined(__x86_64__)
#define NATIVE_ARCH AUDIT_ARCH_X86_64
#else
#error "office2pdf: add this architecture's AUDIT_ARCH"
#endif

#define DENY(error) (SECCOMP_RET_ERRNO | ((error) & SECCOMP_RET_DATA))

/* Refuse socket() for IPv4, IPv6 and raw packet families. A system call made with another
 * architecture's numbering is refused too, so it can't be used to slip past the check. */
static int block_network(void)
{
    struct sock_filter filter[] = {
        BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, arch)),
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, NATIVE_ARCH, 1, 0),
        BPF_STMT(BPF_RET | BPF_K, DENY(EPERM)),
        BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, nr)),
#ifdef __NR_io_uring_setup
        /* io_uring can open sockets without calling socket(). */
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, __NR_io_uring_setup, 0, 1),
        BPF_STMT(BPF_RET | BPF_K, DENY(ENOSYS)),
#endif
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, __NR_socket, 0, 5),
        BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, args[0])),
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, AF_INET, 2, 0),
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, AF_INET6, 1, 0),
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, AF_PACKET, 0, 1),
        BPF_STMT(BPF_RET | BPF_K, DENY(EACCES)),
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ALLOW),
    };
    struct sock_fprog program = {.len = sizeof filter / sizeof filter[0], .filter = filter};
    if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0) {
        return -1;
    }
    return prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, &program, 0, 0);
}

/* 0 if internet sockets are refused and local ones still work. */
static int check_network(void)
{
    int inet = socket(AF_INET, SOCK_STREAM, 0);
    int inet6 = socket(AF_INET6, SOCK_DGRAM, 0);
    int local = socket(AF_UNIX, SOCK_STREAM, 0);
    int ok = inet < 0 && inet6 < 0 && local >= 0;
    printf("inet %s, inet6 %s, unix %s\n", inet < 0 ? "blocked" : "open", inet6 < 0 ? "blocked" : "open",
           local >= 0 ? "open" : "blocked");
    return ok ? 0 : 1;
}

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
    if (argc == 2 && strcmp(argv[1], "--check-network") == 0) {
        if (block_network() != 0) {
            perror("office2pdf: seccomp");
            return NO_SANDBOX;
        }
        return check_network();
    }
    if (argc != 5) {
        fprintf(stderr, "office2pdf: expected 4 arguments, got %d\n", argc - 1);
        return BAD_USAGE;
    }
    /* Before LibreOffice is loaded, so nothing of it ever runs with the network. */
    if (block_network() != 0) {
        perror("office2pdf: can't block the network for the conversion");
        return NO_SANDBOX;
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
