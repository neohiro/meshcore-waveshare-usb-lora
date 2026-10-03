/*
 * KISS server mode: runs the modem's real command handling over stdin/stdout so
 * another implementation can be pointed at it. This is the same src/kiss.c and
 * src/kiss_frame.c that go into the firmware, with only the USART and the
 * SX1262 replaced by stubs.
 *
 * Bytes arriving on stdin are fed through the KISS parser; every complete frame
 * goes to kiss_frame_received. Whatever the modem emits is written to stdout as
 * properly framed KISS.
 *
 * The harness also needs to make the fake radio do things a real radio would,
 * such as receiving a packet. Those requests arrive as frames whose type byte
 * is 0xFE, which is not a KISS command, so they can never collide with host
 * traffic.
 *
 *   airtime:<ms>   complete the next transmission with that time on air,
 *                  where 0 means failure
 *   failtx         shorthand for airtime:0
 *   busy:<0|1>     make the radio report busy or idle
 *   rx:<hex>@<snr>,<rssi>
 *                  deliver a received packet, with SNR in quarter-dB steps
 *
 * Usage:
 *   kiss-server [--tcp <port>] [--trace]
 *
 *   --tcp <port>  serve the first connection on 127.0.0.1:<port> instead of
 *                 using stdin/stdout. A port of 0 asks the OS for a free one and
 *                 the bound port is printed, so a caller never has to reserve a
 *                 port and race for it.
 *   --trace       log every frame in both directions to stderr
 *
 * Build: see tools/test.ps1.
 */

#include "kiss.h"
#include "kiss_frame.h"
#include "radio.h"
#include "radio_stub.h"
#include "serial_stub.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef _WIN32
#include <winsock2.h>
#include <ws2tcpip.h>
#else
#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>
#endif

#define CTRL_TYPE 0xFE

#define CTRL_AIRPORT     "airtime:"
#define CTRL_AIRPORT_LEN 8
#define CTRL_FAIL_TX     "failtx"
#define CTRL_FAIL_TX_LEN 6
#define CTRL_BUSY        "busy:"
#define CTRL_BUSY_LEN    5
#define CTRL_RX          "rx:"
#define CTRL_RX_LEN      3

static bool trace_enabled = false;

#ifdef _WIN32
static SOCKET g_sock = INVALID_SOCKET;
#else
static int g_sock = -1;
#endif

// The byte source and sink, so the same protocol loop serves stdio or a socket.
static int (*read_byte_fn)(void);
static void (*write_bytes_fn)(const unsigned char*, size_t);

static void sink(const unsigned char* bytes, size_t len, void* ctx)
{
    (void)ctx;

    if (trace_enabled) {
        fprintf(stderr, "tx %u bytes:", (unsigned)len);
        for (size_t i = 0; i < len; i++) {
            fprintf(stderr, " %02x", bytes[i]);
        }
        fprintf(stderr, "\n");
    }

    write_bytes_fn(bytes, len);
}

static int stdin_byte(void)
{
    return getchar();
}

static void stdout_bytes(const unsigned char* bytes, size_t len)
{
    if (fwrite(bytes, 1, len, stdout) != len) {
        exit(2);
    }

    fflush(stdout);
}

static int socket_byte(void)
{
    unsigned char b;
    const int n = (int)recv(g_sock, (char*)&b, 1, 0);

    return (n == 1) ? (int)b : -1;
}

static void socket_bytes(const unsigned char* bytes, size_t len)
{
    send(g_sock, (const char*)bytes, (int)len, 0);
}

static bool starts_with(const uint8_t* data, size_t len,
                        const char* prefix, size_t prefix_len)
{
    return len >= prefix_len && memcmp(data, prefix, prefix_len) == 0;
}

static int hex_value(uint8_t c)
{
    if (c >= '0' && c <= '9') {
        return c - '0';
    }
    if (c >= 'a' && c <= 'f') {
        return c - 'a' + 10;
    }
    if (c >= 'A' && c <= 'F') {
        return c - 'A' + 10;
    }
    return -1;
}

/*
 * Parses a decimal number from a byte range that is not NUL terminated. The
 * parser's payload buffer has no terminator, so handing it to strtol directly
 * would read past the frame and pick up whatever follows in memory.
 */
static long parse_int_bounded(const uint8_t* data, size_t len)
{
    char buf[24];
    size_t n = 0;

    for (size_t i = 0; i < len && n + 1 < sizeof(buf); i++) {
        const uint8_t c = data[i];

        if (!((c >= '0' && c <= '9') || (c == '-' && n == 0))) {
            break;
        }

        buf[n++] = (char)c;
    }

    buf[n] = '\0';

    return strtol(buf, NULL, 10);
}

// "rx:<hex>@<snrQuarterDb>,<rssi>" -> deliver a received packet.
static void deliver_rx(const uint8_t* data, size_t len)
{
    uint8_t buf[255];
    size_t n = 0;
    size_t i = CTRL_RX_LEN;
    int snr_quarter_db = 0;
    int rssi_dbm = -100;

    for (; i < len && n < sizeof(buf); i++) {
        if (data[i] == '@') {
            break;
        }

        if (i + 1 >= len) {
            return; // odd number of hex digits
        }

        const int hi = hex_value(data[i]);
        const int lo = hex_value(data[i + 1]);

        if (hi < 0 || lo < 0) {
            return;
        }

        buf[n++] = (uint8_t)((hi << 4) | lo);
        i++;
    }

    if (i < len && data[i] == '@') {
        const uint8_t* comma = memchr(data + i + 1, ',', len - i - 1);
        const size_t snr_len = (comma != NULL) ? (size_t)(comma - (data + i + 1))
                                              : len - i - 1;

        snr_quarter_db = (int)parse_int_bounded(data + i + 1, snr_len);

        if (comma != NULL) {
            rssi_dbm = (int)parse_int_bounded(comma + 1, len - (size_t)(comma + 1 - data));
        }
    }

    kiss_packet_received((int8_t)rssi_dbm, (int8_t)snr_quarter_db, buf, n);
}

static void serve(void)
{
    kiss_parser_t parser;
    kiss_parser_reset(&parser);

    for (;;) {
        const int ch = read_byte_fn();

        if (ch < 0) {
            return;
        }

        if (!kiss_parser_feed(&parser, (uint8_t)ch)) {
            continue;
        }

        if (trace_enabled) {
            fprintf(stderr, "rx type=%02x len=%u", parser.type,
                    (unsigned)parser.len);
            if (parser.type == KISS_CMD_SETHARDWARE && parser.len > 0) {
                fprintf(stderr, " sub=%02x", parser.data[0]);
            }
            fprintf(stderr, "\n");
        }

        if (parser.type == CTRL_TYPE) {
            if (starts_with(parser.data, parser.len,
                            CTRL_AIRPORT, CTRL_AIRPORT_LEN)) {
                radio_stub_set_tx_airtime((uint32_t)parse_int_bounded(
                    parser.data + CTRL_AIRPORT_LEN,
                    parser.len - CTRL_AIRPORT_LEN));
            } else if (starts_with(parser.data, parser.len,
                                   CTRL_FAIL_TX, CTRL_FAIL_TX_LEN)) {
                radio_stub_set_tx_airtime(0);
            } else if (starts_with(parser.data, parser.len,
                                   CTRL_BUSY, CTRL_BUSY_LEN)) {
                radio_stub_set_tx_active(parser.data[CTRL_BUSY_LEN] != '0');
            } else if (starts_with(parser.data, parser.len,
                                   CTRL_RX, CTRL_RX_LEN)) {
                deliver_rx(parser.data, parser.len);
            }

            continue;
        }

        kiss_frame_received(parser.type, parser.data, parser.len);
    }
}

static bool sock_invalid(void)
{
#ifdef _WIN32
    return g_sock == INVALID_SOCKET;
#else
    return g_sock < 0;
#endif
}

// Serves the first TCP connection on 127.0.0.1:<port>.
static void serve_tcp(int port)
{
#ifdef _WIN32
    WSADATA wsa;
    if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) {
        fprintf(stderr, "WSAStartup failed\n");
        exit(2);
    }
#endif

    g_sock = socket(AF_INET, SOCK_STREAM, 0);
    if (sock_invalid()) {
        fprintf(stderr, "socket failed\n");
        exit(2);
    }

    int yes = 1;
    setsockopt(g_sock, SOL_SOCKET, SO_REUSEADDR, (const char*)&yes, sizeof(yes));

    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_port = htons((unsigned short)port);
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);

    if (bind(g_sock, (struct sockaddr*)&addr, sizeof(addr)) != 0 ||
        listen(g_sock, 1) != 0) {
        fprintf(stderr, "bind/listen failed on port %d\n", port);
        exit(2);
    }

    // Report the port actually bound, not the one requested. Asking for 0 lets
    // the OS pick a free port, which is the only way to avoid the race where a
    // caller reserves a port, closes it, and loses it before the server binds.
    struct sockaddr_in bound;
    socklen_t bound_len = sizeof(bound);

    if (getsockname(g_sock, (struct sockaddr*)&bound, &bound_len) != 0) {
        fprintf(stderr, "getsockname failed\n");
        exit(2);
    }

    const int actual_port = ntohs(bound.sin_port);

    fprintf(stderr, "listening on 127.0.0.1:%d\n", actual_port);

    g_sock = accept(g_sock, NULL, NULL);
    if (sock_invalid()) {
        fprintf(stderr, "accept failed\n");
        exit(2);
    }

    fprintf(stderr, "host connected\n");

    read_byte_fn = socket_byte;
    write_bytes_fn = socket_bytes;

    serve();

#ifdef _WIN32
    closesocket(g_sock);
    WSACleanup();
#else
    close(g_sock);
#endif
}

int main(int argc, char** argv)
{
    setvbuf(stdout, NULL, _IONBF, 0);

    // -1 means "no --tcp given". It cannot be 0, because 0 is a valid request:
    // it asks the OS to pick a free port. Using 0 as the sentinel silently
    // degraded --tcp 0 to stdio mode, which exits immediately and looks like a
    // crash.
    int tcp_port = -1;

    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--trace") == 0) {
            trace_enabled = true;
        } else if (strcmp(argv[i], "--tcp") == 0 && i + 1 < argc) {
            tcp_port = atoi(argv[++i]);
        } else {
            fprintf(stderr, "unknown argument %s\n", argv[i]);
            return 2;
        }
    }

    serial_stub_set_sink(sink, NULL);

    radio_stub_reset();
    radio_stub_set_auto_tx_done(true);

    kiss_init();

    if (tcp_port >= 0) {
        serve_tcp(tcp_port);
        return 0;
    }

    read_byte_fn = stdin_byte;
    write_bytes_fn = stdout_bytes;

    serve();

    return 0;
}
