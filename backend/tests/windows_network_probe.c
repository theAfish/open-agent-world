/* Native, CRT-free acceptance workload: same process connects before/after a
 * host checkpoint. No shell, credentials, privilege changes, or child process.
 * Build with existing clang/lld and the Windows SDK (documented in the report).
 */
typedef unsigned long DWORD;
typedef unsigned short WORD;
typedef unsigned long long SOCKET;
typedef void *HANDLE;
#define API __declspec(dllimport)
API char *GetCommandLineA(void);
API DWORD GetCurrentProcessId(void);
API DWORD GetTickCount(void);
API void Sleep(DWORD);
API void ExitProcess(unsigned int);
API DWORD GetFileAttributesA(const char *);
API HANDLE CreateFileA(const char *, DWORD, DWORD, void *, DWORD, DWORD, HANDLE);
API int WriteFile(HANDLE, const void *, DWORD, DWORD *, void *);
API int CloseHandle(HANDLE);
API int WSAStartup(WORD, void *);
API int WSACleanup(void);
API int WSAGetLastError(void);
API SOCKET socket(int, int, int);
API int closesocket(SOCKET);
API int ioctlsocket(SOCKET, long, unsigned long *);
API int connect(SOCKET, const void *, int);
API int getsockopt(SOCKET, int, int, char *, int *);
struct fdset { unsigned int count; SOCKET items[1]; };
struct timeval { long seconds, micros; };
API int select(int, void *, struct fdset *, struct fdset *, struct timeval *);
struct address { WORD family, port; DWORD ipv4; char zero[8]; };

static char *skip(char *p) {
    if (*p == '"') { ++p; while (*p && *p != '"') ++p; if (*p) ++p; }
    else while (*p && *p != ' ') ++p;
    while (*p == ' ') ++p;
    return p;
}
static unsigned int number(char **p) {
    unsigned int n = 0;
    while (**p >= '0' && **p <= '9') { n = n * 10 + (unsigned int)(*(*p)++ - '0'); }
    return n;
}
static int attempt(DWORD ip, WORD port) {
    struct address address;
    unsigned long nonblock = 1;
    SOCKET client = socket(2, 1, 6);
    if (client == (SOCKET)-1) return WSAGetLastError();
    address.family = 2;
    address.port = (WORD)((port >> 8) | (port << 8));
    address.ipv4 = ip;
    for (int i = 0; i < 8; ++i) address.zero[i] = 0;
    int error = 0;
    if (ioctlsocket(client, (long)0x8004667e, &nonblock)) error = WSAGetLastError();
    else if (connect(client, &address, sizeof(address))) {
        error = WSAGetLastError();
        if (error == 10035) {
            struct fdset write = {1, {client}}, except = {1, {client}};
            struct timeval timeout = {1, 500000};
            int result = select(0, 0, &write, &except, &timeout);
            if (result < 0) error = WSAGetLastError();
            else if (!result) error = 10060;
            else {
                int length = sizeof(error);
                if (getsockopt(client, 0xffff, 0x1007, (char *)&error, &length)) error = WSAGetLastError();
            }
        }
    }
    closesocket(client);
    return error;
}
static char *append(char *out, const char *in) { while (*in) *out++ = *in++; return out; }
static char *decimal(char *out, unsigned long n) {
    char buffer[12]; int size = 0;
    do { buffer[size++] = (char)('0' + n % 10); n /= 10; } while (n);
    while (size) *out++ = buffer[--size];
    return out;
}
static void checkpoint(const char *name, DWORD peer, WORD peer_port, WORD host_port) {
    int private_error = attempt(peer, peer_port), host_error = attempt(0x0100007f, host_port);
    char buffer[256], *p = append(buffer, "{\"pid\":");
    p = decimal(p, GetCurrentProcessId());
    p = append(p, ",\"private\":{\"connected\":");
    p = append(p, private_error ? "false" : "true");
    p = append(p, ",\"error\":"); p = decimal(p, (unsigned long)private_error);
    p = append(p, "},\"host\":{\"connected\":");
    p = append(p, host_error ? "false" : "true");
    p = append(p, ",\"error\":"); p = decimal(p, (unsigned long)host_error);
    p = append(p, "}}");
    HANDLE file = CreateFileA(name, 0x40000000, 1, 0, 2, 0x80, 0);
    DWORD written;
    if (file == (HANDLE)-1 || !WriteFile(file, buffer, (DWORD)(p-buffer), &written, 0)
        || written != (DWORD)(p-buffer)) ExitProcess(21);
    CloseHandle(file);
}
static void wait_for(const char *name, DWORD deadline) {
    while (GetFileAttributesA(name) == 0xffffffff) {
        if (GetTickCount() - deadline > 60000) ExitProcess(22);
        Sleep(50);
    }
}
void mainCRTStartup(void) {
    char wsa[512];
    char *p = skip(GetCommandLineA());
    DWORD peer = 0;
    for (int i = 0; i < 4; ++i) {
        unsigned int octet = number(&p);
        if (octet > 255) ExitProcess(10);
        peer |= octet << (8*i);
        if (i != 3 && *p++ != '.') ExitProcess(11);
    }
    while (*p == ' ') ++p;
    unsigned int peer_port = number(&p);
    while (*p == ' ') ++p;
    unsigned int host_port = number(&p);
    if (!peer_port || peer_port > 65535 || !host_port || host_port > 65535 || *p) ExitProcess(12);
    if (WSAStartup(0x202, wsa)) ExitProcess(13);
    checkpoint("before.json", peer, (WORD)peer_port, (WORD)host_port);
    DWORD deadline = GetTickCount();
    wait_for("after-loss", deadline);
    checkpoint("after.json", peer, (WORD)peer_port, (WORD)host_port);
    wait_for("finish", deadline);
    WSACleanup();
    ExitProcess(0);
}
