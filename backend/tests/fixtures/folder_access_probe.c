/* Optional native Windows acceptance probe. No CRT or host runtime grants.
 * Build with cl /GS- /c, then link /entry:mainCRTStartup /subsystem:console
 * /nodefaultlib kernel32.lib. Set OAW_TEST_FOLDER_PROBE to the resulting exe.
 * Direct APIs avoid cmd.exe's additional drive/parent-directory probes.
 */
typedef unsigned long DWORD;
typedef unsigned short WCHAR;
__declspec(dllimport) DWORD __stdcall GetEnvironmentVariableW(const WCHAR*, WCHAR*, DWORD);
__declspec(dllimport) int __stdcall DeleteFileW(const WCHAR*);
__declspec(dllimport) int __stdcall CopyFileW(const WCHAR*, const WCHAR*, int);
__declspec(dllimport) void* __stdcall FindFirstFileW(const WCHAR*, void*);
__declspec(dllimport) int __stdcall FindClose(void*);
__declspec(dllimport) DWORD __stdcall GetLastError(void);
__declspec(dllimport) void __stdcall ExitProcess(DWORD);
__declspec(dllimport) void* __stdcall CreateFileW(const WCHAR*, DWORD, DWORD, void*, DWORD, DWORD, void*);
__declspec(dllimport) int __stdcall ReadFile(void*, void*, DWORD, DWORD*, void*);
__declspec(dllimport) int __stdcall WriteFile(void*, const void*, DWORD, DWORD*, void*);
__declspec(dllimport) int __stdcall CloseHandle(void*);
__declspec(dllimport) void* __stdcall GetStdHandle(DWORD);

void mainCRTStartup(void) {
    static WCHAR path[4096], mode[16];
    static union { void *alignment; unsigned char bytes[1024]; } entry;
    DWORD count = GetEnvironmentVariableW(L"DATA", path, 4000), i = 0;
    const WCHAR *suffix;
    GetEnvironmentVariableW(L"FOLDER_TEST_ACTION", mode, 16);
    if (mode[0] == 'r' || mode[0] == 'w') {
        void *file = CreateFileW(path, mode[0] == 'r' ? 0x80000000 : 0x40000000, 3, 0,
            mode[0] == 'r' ? 3 : 2, 0x80, 0);
        DWORD bytes = 0;
        if (file == (void*)-1) ExitProcess(GetLastError());
        if (mode[0] == 'r') {
            if (!ReadFile(file, entry.bytes, 1024, &bytes, 0)) ExitProcess(GetLastError());
            WriteFile(GetStdHandle((DWORD)-11), entry.bytes, bytes, &bytes, 0);
        } else if (!WriteFile(file, "modified", 8, &bytes, 0)) ExitProcess(GetLastError());
        CloseHandle(file);
        ExitProcess(0);
    }
    if (mode[0] == 'c') {
        if (CopyFileW(path, L"copied.txt", 0)) ExitProcess(0);
        ExitProcess(GetLastError());
    }
    suffix = mode[0] == 'l' ? L"\\*" : L"\\created.txt";
    if (!count || count >= 4000) ExitProcess(101);
    while (suffix[i]) { path[count+i]=suffix[i]; ++i; }
    path[count+i]=0;
    if (mode[0] == 'l') {
        void *handle = FindFirstFileW(path, entry.bytes);
        if (handle == (void*)-1) ExitProcess(GetLastError());
        FindClose(handle);
        ExitProcess(0);
    }
    if (DeleteFileW(path)) ExitProcess(0);
    ExitProcess(GetLastError());
}
