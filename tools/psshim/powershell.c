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
   (Stop-Process, .Terminate(), taskkill) ends them and exits 0.

   A service script run with -File is the other case the stub broke: its
   "success" without doing anything left an old service running, so the new
   executable could not replace it, and never pointed the service at the new
   one (Softube's Installer Helper, whose service.ps1 takes stop | start <exe>
   | restart | remove | pace-running). A script that declares its service as
   `$name = '<service>'` and switches on those actions is carried out here
   through the service control manager -- which Wine implements, where its
   sc.exe has no `config` -- reading the names it needs (the dependency in
   `$pace = '...'`, the display name in `-DisplayName '...'`) from the script.

   Start-Process is the third case (Minimal Hub, 2026-11-04). The Hub installs
   its products by writing a script of `Start-Process -FilePath '<installer>'
   -ArgumentList '/VERYSILENT ...' -PassThru` lines followed by `Wait-Process`,
   and runs it elevated through `powershell -Command "$p = Start-Process
   -FilePath '<Hub>' -ArgumentList '--run-elevated-ps1 "<script>"' -Verb RunAs
   -Wait -PassThru; exit $p.ExitCode"`; the Hub then runs `powershell -File
   <script>`. The stub's success at the first step meant "installed" with
   nothing run and the download deleted. Both steps are carried out here: a
   -Command with Start-Process starts that program (and, with -Wait, waits and
   returns its exit code); a -File script made of Start-Process lines runs
   each one in turn, waiting, and exits 1 if any failed -- `New-Item
   -ItemType Directory` and `Remove-Item` lines in it are honoured too, so the
   installers' log folder exists and the downloads are cleaned up as the
   script intends. There is no elevation under Wine, so -Verb RunAs is simply
   a start.

   Anything else exits 0, exactly as the stub did. Every call is logged to
   %LOCALAPPDATA%\vstenv-powershell.log so the next surprise can be read. */
#include <windows.h>
#include <tlhelp32.h>
#include <wincrypt.h>
#include <stdio.h>
#include <wchar.h>
#include <wctype.h>
#include <shellapi.h>
#include <shlobj.h>

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

/* ---- service scripts ------------------------------------------------------------ */

/* The value of `<var> = '<value>'` or of `<flag> '<value>'` in a script, or NULL. */
static wchar_t *quoted_after(const wchar_t *text, const wchar_t *key)
{
    const wchar_t *p = find_ci(text, key);
    if (!p) return NULL;
    p += wcslen(key);
    while (*p == L' ' || *p == L'=' || *p == L'\t') p++;
    if (*p != L'\'' && *p != L'"') return NULL;
    wchar_t q = *p; const wchar_t *s = ++p, *e = wcschr(s, q);
    if (!e || e == s) return NULL;
    wchar_t *out = calloc(e - s + 1, sizeof(wchar_t));
    if (out) wcsncpy(out, s, e - s);
    return out;
}

static wchar_t *read_text(const wchar_t *path)
{
    HANDLE h = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL);
    if (h == INVALID_HANDLE_VALUE) return NULL;
    DWORD size = GetFileSize(h, NULL), got = 0;
    char *raw = calloc(1, size + 4);
    if (!raw || !ReadFile(h, raw, size, &got, NULL)) { CloseHandle(h); free(raw); return NULL; }
    CloseHandle(h);
    if (got >= 2 && (BYTE)raw[0] == 0xff && (BYTE)raw[1] == 0xfe)      /* UTF-16LE with BOM */
        return wcsdup((wchar_t *)(raw + 2));
    int skip = (got >= 3 && (BYTE)raw[0] == 0xef && (BYTE)raw[1] == 0xbb && (BYTE)raw[2] == 0xbf) ? 3 : 0;
    int n = MultiByteToWideChar(CP_UTF8, 0, raw + skip, got - skip, NULL, 0);
    wchar_t *w = calloc(n + 1, sizeof(wchar_t));
    if (w) MultiByteToWideChar(CP_UTF8, 0, raw + skip, got - skip, w, n);
    free(raw);
    return w;
}

/* argv after "-File <script>": the script path, then its arguments. */
static int file_args(wchar_t **script, wchar_t **action, wchar_t **arg)
{
    int argc; wchar_t **argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    if (!argv) return 0;
    for (int i = 1; i < argc - 1; i++)
        if (!_wcsicmp(argv[i], L"-File") || !_wcsicmp(argv[i], L"-f"))
        {
            *script = argv[i + 1];
            *action = i + 2 < argc ? argv[i + 2] : NULL;
            *arg = i + 3 < argc ? argv[i + 3] : NULL;
            /* named parameters: -Action x -Exe y */
            for (int j = i + 2; j < argc - 1; j++)
            {
                if (!_wcsicmp(argv[j], L"-Action")) *action = argv[j + 1];
                else if (!_wcsicmp(argv[j], L"-Exe")) *arg = argv[j + 1];
            }
            return 1;
        }
    return 0;
}

static DWORD service_pid(SC_HANDLE svc, DWORD *state)
{
    SERVICE_STATUS_PROCESS st; DWORD need;
    if (!QueryServiceStatusEx(svc, SC_STATUS_PROCESS_INFO, (BYTE *)&st, sizeof st, &need)) { *state = 0; return 0; }
    *state = st.dwCurrentState;
    return st.dwProcessId;
}

static BOOL wait_state(SC_HANDLE svc, DWORD want, int seconds)
{
    DWORD state;
    for (int i = 0; i < seconds * 4; i++) { service_pid(svc, &state); if (state == want) return TRUE; Sleep(250); }
    return FALSE;
}

static BOOL wait_exit(DWORD pid, int seconds)
{
    if (!pid) return TRUE;
    HANDLE h = OpenProcess(SYNCHRONIZE, FALSE, pid);
    if (!h) return TRUE;                      /* already gone */
    BOOL gone = WaitForSingleObject(h, seconds * 1000) == WAIT_OBJECT_0;
    CloseHandle(h);
    return gone;
}

/* Stop it and wait until its process has exited, so its executable can be replaced. */
static int svc_stop(SC_HANDLE svc)
{
    DWORD state, pid = service_pid(svc, &state);
    if (state == SERVICE_STOPPED || !state) return 0;
    SERVICE_STATUS st;
    if (state != SERVICE_STOP_PENDING) ControlService(svc, SERVICE_CONTROL_STOP, &st);
    if (!wait_exit(pid, 60))
    {
        SERVICE_FAILURE_ACTIONSW none = { 0 };
        ChangeServiceConfig2W(svc, SERVICE_CONFIG_FAILURE_ACTIONS, &none);   /* or it is restarted */
        HANDLE h = OpenProcess(PROCESS_TERMINATE | SYNCHRONIZE, FALSE, pid);
        if (h) { TerminateProcess(h, 1); WaitForSingleObject(h, 10000); CloseHandle(h); }
    }
    wait_state(svc, SERVICE_STOPPED, 10);
    return 0;
}

static void svc_recovery(SC_HANDLE svc)
{
    SC_ACTION a[3] = { { SC_ACTION_RESTART, 0 }, { SC_ACTION_RESTART, 0 }, { SC_ACTION_RESTART, 0 } };
    SERVICE_FAILURE_ACTIONSW fa = { 0, NULL, NULL, 3, a };
    ChangeServiceConfig2W(svc, SERVICE_CONFIG_FAILURE_ACTIONS, &fa);
}

static int svc_start(SC_HANDLE svc)
{
    svc_recovery(svc);
    DWORD state; service_pid(svc, &state);
    if (state == SERVICE_RUNNING) return 0;
    if (state != SERVICE_START_PENDING) StartServiceW(svc, 0, NULL);
    return wait_state(svc, SERVICE_RUNNING, 60) ? 0 : 2;
}

/* A service.ps1-style script: carried out here. Returns -1 when it is not one. */
static int service_script(wchar_t **what)
{
    wchar_t *script, *action, *arg;
    if (!file_args(&script, &action, &arg) || !action) return -1;
    wchar_t *text = read_text(script);
    if (!text) return -1;
    wchar_t *name = quoted_after(text, L"$name");
    if (!name || !find_ci(text, L"switch") || !find_ci(text, L"'stop'") || !find_ci(text, L"'start'")) return -1;
    wchar_t *dep = quoted_after(text, L"$pace");
    wchar_t *display = quoted_after(text, L"-DisplayName");
    SC_HANDLE scm = OpenSCManagerW(NULL, NULL, SC_MANAGER_ALL_ACCESS);
    if (!scm) return -1;
    SC_HANDLE svc = OpenServiceW(scm, name, SERVICE_ALL_ACCESS);
    int rc = 0;
    if (!_wcsicmp(action, L"stop")) { *what = L"service stop"; if (svc) rc = svc_stop(svc); }
    else if (!_wcsicmp(action, L"start") && arg)
    {
        *what = L"service start";
        wchar_t path[MAX_PATH * 2]; swprintf(path, MAX_PATH * 2, L"\"%ls\"", arg);
        wchar_t deps[256] = { 0 };                    /* double-NUL-terminated list */
        if (dep) wcsncpy(deps, dep, 250);
        if (svc)
        {
            if (!ChangeServiceConfigW(svc, SERVICE_NO_CHANGE, SERVICE_NO_CHANGE, SERVICE_NO_CHANGE, path,
                                      NULL, NULL, dep ? deps : NULL, NULL, NULL, NULL)) rc = 2;
        }
        else if (!(svc = CreateServiceW(scm, name, display ? display : name, SERVICE_ALL_ACCESS, SERVICE_WIN32_OWN_PROCESS,
                                        SERVICE_AUTO_START, SERVICE_ERROR_NORMAL, path, NULL, NULL, dep ? deps : NULL, NULL, NULL))) rc = 2;
        if (!rc) rc = svc_start(svc);
    }
    else if (!_wcsicmp(action, L"restart")) { *what = L"service restart"; if (svc) rc = svc_start(svc); }
    else if (!_wcsicmp(action, L"remove")) { *what = L"service remove"; if (svc) { svc_stop(svc); DeleteService(svc); } }
    else if (dep && find_ci(action, L"-running"))     /* pace-running: is the dependency running? */
    {
        *what = L"dependency running?";
        SC_HANDLE d = OpenServiceW(scm, dep, SERVICE_QUERY_STATUS);
        DWORD state = 0; if (d) { service_pid(d, &state); CloseServiceHandle(d); }
        rc = state == SERVICE_RUNNING ? 0 : 1;
    }
    else { rc = -1; }
    if (svc) CloseServiceHandle(svc);
    CloseServiceHandle(scm);
    return rc;
}

/* ---- Start-Process ---------------------------------------------------------------- */

/* The PowerShell string literal at p ('...' with '' for a quote, or "..."):
   malloc'd text, and *endp just past its closing quote. NULL if p is not one. */
static wchar_t *ps_string(const wchar_t *p, const wchar_t **endp)
{
    while (*p == L' ' || *p == L'\t') p++;
    wchar_t q = *p;
    if (q != L'\'' && q != L'"') return NULL;
    wchar_t *out = calloc(wcslen(p) + 1, sizeof(wchar_t));
    if (!out) return NULL;
    size_t n = 0;
    for (p++; *p; p++)
    {
        if (*p == q)
        {
            if (q == L'\'' && p[1] == L'\'') { out[n++] = q; p++; continue; }
            break;
        }
        out[n++] = *p;
    }
    if (*p != q) { free(out); return NULL; }
    if (endp) *endp = p + 1;
    return out;
}

/* The literal after `-Name` within this statement (up to the next newline or
   semicolon), or NULL. */
static wchar_t *param(const wchar_t *stmt, const wchar_t *name)
{
    const wchar_t *end = stmt;
    while (*end && *end != L'\n' && *end != L';') end++;
    const wchar_t *p = find_ci(stmt, name);
    if (!p || p >= end) return NULL;
    return ps_string(p + wcslen(name), NULL);
}

/* Start exe with args in its own folder; with wait, its exit code, else 0.
   -1 if it could not be started. */
static int start_process(const wchar_t *exe, const wchar_t *args, BOOL wait)
{
    size_t len = wcslen(exe) + (args ? wcslen(args) : 0) + 8;
    wchar_t *cmdline = calloc(len, sizeof(wchar_t));
    if (!cmdline) return -1;
    swprintf(cmdline, len, L"\"%ls\"%ls%ls", exe, args && *args ? L" " : L"", args ? args : L"");
    wchar_t *dir = wcsdup(exe), *slash = dir ? wcsrchr(dir, L'\\') : NULL;
    if (slash) *slash = 0;
    STARTUPINFOW si = { sizeof si }; PROCESS_INFORMATION pi = { 0 };
    BOOL ok = CreateProcessW(NULL, cmdline, NULL, NULL, FALSE, 0, NULL, slash ? dir : NULL, &si, &pi);
    free(cmdline); free(dir);
    if (!ok) return -1;
    DWORD code = 0;
    if (wait) { WaitForSingleObject(pi.hProcess, INFINITE); GetExitCodeProcess(pi.hProcess, &code); }
    CloseHandle(pi.hThread); CloseHandle(pi.hProcess);
    return wait ? (int)code : 0;
}

/* Every Start-Process in `text`, in order, each waited for: 0 if all exited 0,
   1 otherwise, -1 if there is none. New-Item -ItemType Directory and
   Remove-Item lines are carried out on the way. */
static int run_start_processes(const wchar_t *text, BOOL wait_each)
{
    int found = 0, failed = 0;
    for (const wchar_t *p = text; (p = find_ci(p, L"Start-Process")); p += 13)
    {
        wchar_t *exe = param(p, L"-FilePath");
        if (!exe) continue;
        wchar_t *args = param(p, L"-ArgumentList");
        found++;
        int rc = start_process(exe, args, wait_each);
        if (rc != 0) failed++;
        free(exe); free(args);
    }
    if (!found) return -1;
    for (const wchar_t *p = text; (p = find_ci(p, L"Remove-Item")); p += 11)
    {
        wchar_t *path = param(p, L"-Path");
        if (!path) path = ps_string(p + 11, NULL);
        if (!path) { const wchar_t *f = find_ci(p, L"-Force"); if (f) path = ps_string(f + 6, NULL); }
        if (path) { DeleteFileW(path); free(path); }
    }
    return failed ? 1 : 0;
}

static void make_directories(const wchar_t *text)
{
    for (const wchar_t *p = text; (p = find_ci(p, L"New-Item")); p += 8)
    {
        if (!find_ci(p, L"Directory")) continue;
        wchar_t *path = param(p, L"-Path");
        if (path) { SHCreateDirectoryExW(NULL, path, NULL); free(path); }
    }
}

/* `-File <script>` whose body is Start-Process lines (the Hub's install script). */
static int start_process_script(void)
{
    wchar_t *script, *action, *arg;
    if (!file_args(&script, &action, &arg)) return -1;
    wchar_t *text = read_text(script);
    if (!text || !find_ci(text, L"Start-Process")) return -1;
    make_directories(text);
    return run_start_processes(text, TRUE);
}

/* `-Command "... Start-Process -FilePath '<exe>' ... [-Wait] ..."`, as given
   on the command line (after CommandLineToArgvW, so \" is already "). */
static int start_process_command(void)
{
    int argc; wchar_t **argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    if (!argv) return -1;
    int i = 1;
    for (; i < argc; i++) if (!_wcsicmp(argv[i], L"-Command") || !_wcsicmp(argv[i], L"-c")) break;
    if (i >= argc - 1) return -1;
    size_t len = 1; for (int j = i + 1; j < argc; j++) len += wcslen(argv[j]) + 1;
    wchar_t *text = calloc(len, sizeof(wchar_t));
    if (!text) return -1;
    for (int j = i + 1; j < argc; j++) { wcscat(text, argv[j]); if (j < argc - 1) wcscat(text, L" "); }
    if (!find_ci(text, L"Start-Process")) { free(text); return -1; }
    int rc = run_start_processes(text, find_ci(text, L"-Wait") != NULL);
    free(text);
    return rc;
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
    else
    {
        wchar_t *what = L"service script";
        int src = service_script(&what);
        if (src < 0 && (src = start_process_script()) >= 0) what = L"install script";
        if (src < 0 && (src = start_process_command()) >= 0) what = L"start-process";
        if (src >= 0) { rc = src; log_line(what, cmd, rc); }
        else log_line(L"stub", cmd, rc);
    }
    return rc;
}
