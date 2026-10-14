# Clean-machine test

The installer and the app have so far been exercised on the developer's PC only. Before announcing, run this on machines
that have never seen Python, Qt or Zapi.

## 1. Automated part (about 2 minutes)
Use Windows Sandbox (Windows 10/11 Pro or Enterprise → "Turn Windows features on or off" → *Windows Sandbox*) or a fresh VM.

**Sandbox:** edit the three host paths in `docs/zapi-sandbox.wsb`, create the folder `C:\Users\Public\Desktop\ZapiResults`,
double-click the file. It installs the real installer, runs everything below and writes `report.md` into that folder.

**VM or PC:**
```
powershell -ExecutionPolicy Bypass -File tools\smoke_installed.ps1 -Installer path\to\Zapi-Setup.exe
```
Add `-CleanMachine` **only in a VM / Sandbox**: it also runs an uninstall that deletes `%APPDATA%\Zapi`.

What it checks: silent install, every expected file, version resources (Lavati Softwares), Start menu + desktop shortcuts,
uninstall entry, PATH entry for `zapi-cli`, the packaged self-test (script engine, TLS, gRPC libraries, DPAPI, themes...),
`zapi-cli` against a local server (exit codes 0 and 1), the GUI opening, staying responsive and closing without a crash
report, no leftover processes, upgrade over the existing install (one uninstall entry, PATH not duplicated), uninstall
removing files / shortcuts / PATH / registry, and your PATH restored exactly. The only expected failure today is
"installer is signed" until you sign it.

Last result on the developer PC: **20 passed, 1 expected failure (unsigned)**. It found and fixed a real uninstaller bug
(a stray `;` left in the user PATH).

## 2. Manual part (about 20 minutes per machine)
Tick these on each machine and note the Windows build.

| Machine | Windows | Display | Done |
|---|---|---|---|
| Fresh Windows 10 22H2 VM | | 100 % | ☐ |
| Fresh Windows 11 VM | | 100 % | ☐ |
| Laptop with scaling 150 % or 200 % | | | ☐ |
| Two monitors with different scaling | | | ☐ |
| User account with a non-ASCII name (e.g. `Zoë`, `李`) | | | ☐ |
| Windows Defender on, no exclusions | | | ☐ |
| Behind a corporate proxy (if available) | | | ☐ |

**First run**
- [ ] Installer starts, shows Lavati branding, licence and welcome text; no errors; no admin prompt.
- [ ] Zapi starts in under ~5 seconds; welcome screen shows "Send your first request".
- [ ] Send `https://jsonplaceholder.typicode.com/todos/1` (GET): 200 OK, JSON pretty-printed.
- [ ] HTTPS to a site with a certificate chain works (no SSL error).
- [ ] Paste `curl "https://jsonplaceholder.typicode.com/posts" -H "content-type: application/json" -d "{\"title\":\"x\"}"` into the box: becomes a POST with a body.

**Everyday use**
- [ ] Create a collection, a folder, save two requests, restart Zapi: tabs and collection are back.
- [ ] Create an environment, mark a variable Secret, use `{{var}}` in a URL, switch environments.
- [ ] Open `%APPDATA%\Zapi\workspace.json` in Notepad: the Secret value is **not** readable (`dpapi:...`).
- [ ] Add a test script (Snippets → Status code is 200), Send: Test Results show PASS.
- [ ] Runner with a CSV data file; export results.
- [ ] Settings → switch theme to Light, then System, then back; tabs survive the switch.
- [ ] Copy menu: copy response body / as cURL; paste elsewhere to confirm.
- [ ] Export → Full workspace backup with a passphrase; import it on another machine with the passphrase.

**Edge cases**
- [ ] Resize to a small window (900×600) and maximise; nothing is cut off. Drag between monitors.
- [ ] Unplug the network and press Send: a clear error, no crash.
- [ ] Send to a server that never answers: times out per Settings, UI stays responsive, other tabs usable.
- [ ] A script with `while (true) {}` is stopped after ~10 s and reported.
- [ ] Open a 5 MB JSON response; Zapi stays responsive.
- [ ] Close the window during a request: it exits, no leftover `Zapi.exe` in Task Manager.

**Installer lifecycle**
- [ ] Install an older build, then the new one over it: data kept, shortcuts fine.
- [ ] Uninstall, choose *Yes* (keep data), reinstall: collections are still there.
- [ ] Uninstall, choose *No*: `%APPDATA%\Zapi` is gone.
- [ ] SmartScreen / Defender: note exactly what the user sees (screenshot it) for the release notes.

## 3. Report
Record failures as GitHub issues with the *Copy diagnostic info* block. Do not publish until every box above is ticked or
has a known-issues entry in the release notes.
