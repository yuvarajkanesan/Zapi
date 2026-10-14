# Clean-machine test

The installer and the app have so far been exercised on the developer's PC only. Before announcing, run this on machines
that have never seen Python, Qt or Zapi.

## 1. Automated part (about 5 minutes)
Use Windows Sandbox (Windows 10/11 Pro or Enterprise → "Turn Windows features on or off" → *Windows Sandbox*) or a fresh VM.

**Build the test installers once** (on the build machine, after `build.bat`; needs Inno Setup):
```
powershell -ExecutionPolicy Bypass -File tools\make_test_installers.ps1
```
This writes an older (0.0.9, with files the current version does not ship) and a newer (9.9) installer into `dist\test`.
Without them the upgrade and downgrade scenarios are reported as SKIP.

**Sandbox:** edit the three host paths in `docs/zapi-sandbox.wsb`, create the folder `C:\Users\Public\Desktop\ZapiResults`,
double-click the file. It runs the test with `-CleanMachine` and writes `report.md` into that folder.

**VM or PC:**
```
powershell -ExecutionPolicy Bypass -File tools\smoke_installed.ps1
```

Safety: the script refuses to run if Zapi is already installed (`-ReplaceExisting` allows it), restores your user PATH exactly
as it found it (text and registry type), and deletes `%APPDATA%\Zapi` only when that folder holds nothing but the script's own
marker file, or when `-CleanMachine` is given (that belongs in a VM / Sandbox).

What it checks:
1. **Fresh install**: every file, version resources, shortcuts, uninstall entry, `zapi-cli` on PATH exactly once, the packaged
   self-test, `zapi-cli` against a local server (exit codes 0 and 1), the GUI opening, staying responsive and closing without a
   crash report.
2. **Reinstall** over the same version: one uninstall entry, PATH not duplicated.
3. **Uninstall while Zapi is running**: the app is stopped, everything is removed, and **your data is kept** by default.
4. **Upgrade from an older version while it is running**: the old app is closed, files the old version had are removed (inside
   `_internal` and at the top level), the choices made at first install (PATH on, desktop shortcut off) are remembered, your data
   is untouched, there is one uninstall entry, and a later uninstall leaves nothing behind.
5. **Downgrade protection**: a silent install of an older version over a newer one is refused; `/ALLOWDOWNGRADE` allows it.
6. **Where it installs**: the default per-user folder (no admin prompt), and a folder with spaces, accents, Chinese letters and
   brackets.
7. **`/REMOVEDATA`** deletes the user's data (run only when the data folder holds nothing of value, or with `-CleanMachine`).

The only expected failure today is "installer is signed" until the installer is signed.

Silent switches. Install: `/DIR=...`, `/TASKS="addtopath,desktopicon"`, `/ALLOWDOWNGRADE`. Uninstall: none keeps your data,
`/REMOVEDATA` deletes it.

Last result on the developer PC: **36 passed, 1 expected failure (unsigned), 1 skipped (the data-deleting test, because real
Zapi data exists there)**. Running it found and fixed four real bugs: a stray `;` left in the user PATH, the uninstaller killing
itself, fonts not loading from a folder with non-Latin letters, and a silent uninstall that deleted the user's data by default.

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

**Installer lifecycle (the parts a script cannot judge)**
- [ ] Run the installer by hand, not silently: every page looks right, Back and Cancel work, the Finish page launches Zapi.
- [ ] Install an older build, then the new one over it: data kept, shortcuts fine.
- [ ] Leave unsaved edits in a tab, run a newer installer: Zapi closes by itself and the tabs come back afterwards.
- [ ] Try to install an older version over a newer one by hand: you are asked before it replaces anything.
- [ ] Uninstall (the wizard preselects *Yes, keep my data*), reinstall: collections are still there.
- [ ] Uninstall and choose *No*: `%APPDATA%\Zapi` is gone.
- [ ] SmartScreen / Defender: note exactly what the user sees (screenshot it) for the release notes.

## 3. Report
Record failures as GitHub issues with the *Copy diagnostic info* block. Do not publish until every box above is ticked or
has a known-issues entry in the release notes.
