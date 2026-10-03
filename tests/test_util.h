#ifndef TEST_UTIL_H
#define TEST_UTIL_H

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/*
 * A deliberately tiny assertion harness. The firmware has no other test
 * dependency and pulling a framework in would mean vendoring one, so this is
 * all the machinery the native tests need.
 */

static int tu_checks = 0;
static int tu_failures = 0;
static const char* tu_suite = "";

#define SUITE(name) (tu_suite = (name))

#define CHECK(cond)                                                          \
    do {                                                                     \
        tu_checks++;                                                         \
        if (!(cond)) {                                                       \
            tu_failures++;                                                   \
            printf("  FAIL [%s] %s:%d: %s\n", tu_suite, __FILE__, __LINE__,  \
                   #cond);                                                   \
        }                                                                    \
    } while (0)

#define CHECK_EQ_INT(actual, expected)                                       \
    do {                                                                     \
        long long a_ = (long long)(actual);                                  \
        long long e_ = (long long)(expected);                                \
        tu_checks++;                                                         \
        if (a_ != e_) {                                                      \
            tu_failures++;                                                   \
            printf("  FAIL [%s] %s:%d: %s == %lld, expected %lld\n",          \
                   tu_suite, __FILE__, __LINE__, #actual, a_, e_);           \
        }                                                                    \
    } while (0)

#define CHECK_EQ_MEM(actual, expected, n)                                    \
    do {                                                                     \
        tu_checks++;                                                         \
        if (memcmp((actual), (expected), (n)) != 0) {                        \
            tu_failures++;                                                   \
            printf("  FAIL [%s] %s:%d: %s != expected\n    actual  : ",      \
                   tu_suite, __FILE__, __LINE__, #actual);                   \
            tu_dump_hex((const unsigned char*)(actual), (n));                \
            printf("    expected: ");                                        \
            tu_dump_hex((const unsigned char*)(expected), (n));              \
        }                                                                    \
    } while (0)

// Only referenced by CHECK_EQ_MEM, which not every suite uses.
__attribute__((unused))
static void tu_dump_hex(const unsigned char* p, size_t n)
{
    for (size_t i = 0; i < n; i++) {
        printf("%02x", p[i]);
    }
    printf("\n");
}

// Prints each test as it runs, so a crash identifies the culprit.
#define RUN(fn)                                                              \
    do {                                                                     \
        const int before_ = tu_failures;                                    \
        printf("  %-44s", #fn);                                             \
        fflush(stdout);                                                      \
        fn();                                                                \
        printf("%s\n", tu_failures == before_ ? "ok" : "FAILED");             \
        fflush(stdout);                                                      \
    } while (0)

static int tu_report(const char* what)
{
    if (tu_failures == 0) {
        printf("%s: %d checks passed\n", what, tu_checks);
        return 0;
    }

    printf("%s: %d of %d checks FAILED\n", what, tu_failures, tu_checks);
    return 1;
}

#endif // TEST_UTIL_H
