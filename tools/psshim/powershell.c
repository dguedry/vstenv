/* powershell.exe for a Wine prefix, in place of Wine's stub.

   Wine's powershell.exe runs nothing and exits 0 for any command. Most callers
   are happy with that: an installer's "configure the service" script that does
   nothing and reports success is better than one that fails. One question it
   gets wrong is "is this program running?": an exit code of 0 means "yes", so
   electron-builder installers (Softube Central, 2026-10-30) asked whether their
   app was running, were told it always was, and stopped for ever with "<App>
   cannot be closed".

   This answers that question honestly and keeps the stub's behaviour for the
   rest. A command that queries Win32_Process for executables under a folder
   (`$_.Path.StartsWith('<folder>' ...)`) is answered from the real process
   list: exit 0 if one runs, 1 if none; a command that also stops them
   (Stop-Process, .Terminate(), taskkill) ends them and exits 0. Anything else
   exits 0, exactly as the stub did. Every call is logged to
   %LOCALAPPDATA%\vstenv-powershell.log so the next surprise can be read. */
#include <windows.h>
#include <tlhelp32.h>
#include <wincrypt.h>
#include <stdio.h>
#include <wchar.h>
#include <wctype.h>

static void log_line(const wchar_t *what, const wchar_t *cmd, int rc)
{
    wchar_t path[MAX_PATH];
    DWORD n = GetEnvironmentVariableW(L"LOCALAPPDATA", path, MAX_PATH);
    if (!n || n > MAX_PATH - 32) return;
    wcscat(path, L"\\vstenv-powershell.log");
    HANDLE h = CreateFileW(path, FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_ALWAYS, 0, NULL);
    if (h == INVALID_HANDLE_VALUE) return;
    if (GetFileSize(h, NULL) > 1 << 20) { CloseHandle(h); return; }   /* 1 MB is plenty */
    char buf[8192]; SYSTEMTIME t; GetLocalTime(&t);
    int len = snprintf(buf, sizeof buf, "%04d-%02d-%02d %02d:%02d:%02d %ls -> %d: %.6000ls\r\n",
                       t.wYear, t.wMonth, t.wDay, t.wHour, t.wMinute, t.wSecond, what, rc, cmd);
    DWORD w; if (len > 0) WriteFile(h, buf, (DWORD)(len < (int)sizeof buf ? len : (int)sizeof buf - 1), &w, NULL);
    CloseHandle(h);
}

static const wchar_t *find_ci(const wchar_t *hay, const wchar_t *needle)
{
    size_t n = wcslen(needle);
    for (; *hay; hay++) if (!_wcsnicmp(hay, needle, n)) return hay;
    return NULL;
}

/* -EncodedCommand <base64 of UTF-16LE>: decode it, so the patterns below see it. */
static wchar_t *decoded_command(const wchar_t *cmdline)
{
    const wchar_t *p = find_ci(cmdline, L"-enc");
    if (!p) return NULL;
    while (*p && !iswspace(*p)) p++;
    while (iswspace(*p)) p++;
    const wchar_t *e = p; while (*e && !iswspace(*e)) e++;
    DWORD len = 0;
    if (e == p || !CryptStringToBinaryW(p, (DWORD)(e - p), CRYPT_STRING_BASE64, NULL, &len, NULL, NULL)) return NULL;
    BYTE *buf = calloc(1, len + 2);
    if (!buf || !CryptStringToBinaryW(p, (DWORD)(e - p), CRYPT_STRING_BASE64, buf, &len, NULL, NULL)) { free(buf); return NULL; }
    return (wchar_t *)buf;
}

/* The folder in `.StartsWith('<folder>'` / `.StartsWith("<folder>"`, or NULL. */
static wchar_t *startswith_folder(const wchar_t *cmd)
{
    const wchar_t *p = find_ci(cmd, L"StartsWith(");
    if (!p) return NULL;
    p += 11;
    while (*p == L' ') p++;
    wchar_t q = *p;
    if (q != L'\'' && q != L'"') return NULL;
    const wchar_t *s = ++p, *e = wcschr(s, q);
    if (!e || e == s) return NULL;
    wchar_t *out = calloc(e - s + 1, sizeof(wchar_t));
    if (out) wcsncpy(out, s, e - s);
    return out;
}

/* Processes whose executable lies under `folder`; with `stop`, end them. */
static int running_under(const wchar_t *folder, BOOL stop)
{
    size_t n = wcslen(folder);
    int found = 0;
    HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
    if (snap == INVALID_HANDLE_VALUE) return 0;
    PROCESSENTRY32W pe = { sizeof pe };
    for (BOOL ok = Process32FirstW(snap, &pe); ok; ok = Process32NextW(snap, &pe))
    {
        if (pe.th32ProcessID == GetCurrentProcessId()) continue;
        HANDLE h = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | (stop ? PROCESS_TERMINATE : 0), FALSE, pe.th32ProcessID);
        if (!h) continue;
        wchar_t image[MAX_PATH * 2]; DWORD sz = MAX_PATH * 2;
        if (QueryFullProcessImageNameW(h, 0, image, &sz) && sz >= n && !_wcsnicmp(image, folder, n))
        {
            found++;
            if (stop) TerminateProcess(h, 1);
        }
        CloseHandle(h);
    }
    CloseHandle(snap);
    return found;
}

int wmain(void)
{
    const wchar_t *cmdline = GetCommandLineW();
    wchar_t *decoded = decoded_command(cmdline);
    const wchar_t *cmd = decoded ? decoded : cmdline;
    int rc = 0;
    wchar_t *folder = find_ci(cmd, L"Win32_Process") ? startswith_folder(cmd) : NULL;
    if (folder)
    {
        BOOL stop = find_ci(cmd, L"Stop-Process") || find_ci(cmd, L".Terminate(") || find_ci(cmd, L"taskkill");
        int n = running_under(folder, stop);
        rc = stop ? 0 : (n ? 0 : 1);
        log_line(stop ? L"stop processes" : (n ? L"running" : L"not running"), cmd, rc);
    }
    else log_line(L"stub", cmd, rc);
    return rc;
}
