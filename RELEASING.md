# Going public: checklist

## Already in the repo
- MIT licence, installer EULA, `THIRD_PARTY_NOTICES.txt` (generated from the installed packages; includes the LGPL / GPL
  texts Qt requires and the SIL OFL for the fonts), Windows file-version resources, publisher shown as Lavati Softwares.
- No telemetry and no account. Crash reports are written to `%APPDATA%\Zapi\logs` and never sent.
- `build.bat` -> reproducible build + packaged self-test + installer (`build.bat all` also runs the unit tests first and builds the
  single-file portable exe; `build.bat help` lists the options). `.github/workflows/ci.yml` runs the tests
  on every push and builds the installer for `v*` tags (written without a way to run it here: check the first run).
- Themes (System / Dark / Light / Midnight / High contrast + accent colours), all checked for WCAG contrast.

## Must do before the first public release
1. **Code signing.** Without it every download shows "Unknown publisher" and SmartScreen warns. Get an OV/EV certificate or
   Azure Trusted Signing; set `SIGN_CERT_SHA1` (see `build.bat`). Sign `Zapi.exe`, `zapi-cli.exe` and the installer.
2. ~~Secrets at rest~~ Done: secrets are encrypted with Windows DPAPI (`zapi/vault.py`), shared exports omit credentials. Hand-typed
   auth headers / params, URLs with tokens and scripts or bodies with hard-coded credentials are covered too, and full backups
   are passphrase-encrypted. Remaining limit: a credential that is not recognisable by name or shape (a plain word in a body).
3. **Test on clean machines**: Windows 10 and 11 without Python, 100/150/200 % scaling, two monitors, a user name with
   non-ASCII characters, Defender on. Install, upgrade over the previous version, uninstall (with and without keeping data).
4. **Antivirus false positives.** PyInstaller apps are flagged sometimes. Sign, then submit the installer to Microsoft
   Security Intelligence and the big vendors before announcing.
5. **Name and brand check.** Search trademarks for "Zapi" in software, and decide who the legal publisher is.
6. **Version policy.** Semantic versions; bump `zapi/__init__.py`, add to `CHANGELOG.md`, tag `vX.Y.Z`.

## Preview-release kit (in the repo)
- `docs/RELEASE_NOTES_0.1-preview.md` (with screenshots from `tools/make_screenshots.py`), issue templates, `SECURITY.md`,
  `CONTRIBUTING.md`, "Copy diagnostic info" in the app menu.
- `tools/make_test_installers.ps1`, `tools/smoke_installed.ps1`, `docs/zapi-sandbox.wsb`, `docs/CLEAN_MACHINE_TEST.md`: the
  install / upgrade / downgrade / uninstall lifecycle test (36 checks).
- TODO before publishing: set the security contact in `SECURITY.md` and `.github/ISSUE_TEMPLATE/config.yml`, upload the
  installer with its SHA-256, and add the repository URL to the README.

## Distribution
- GitHub Releases with the installer, a SHA-256 file and the changelog; also a portable zip of `dist\Zapi`.
- winget (`microsoft/winget-pkgs` manifest), then Scoop and Chocolatey. Microsoft Store (MSIX) is optional and gives signing.
- ARM64 Windows: build on an ARM64 runner (the installer is already `x64compatible`).
- Updates: keep the no-telemetry promise. An opt-in "Check for updates" that reads the GitHub Releases API is enough.

## Project hygiene
- README with screenshots and a 60-second quick start; docs for the scripting API (`pm.*`) and `zapi-cli` in CI.
- `CONTRIBUTING.md`, `SECURITY.md` (how to report a vulnerability), issue templates, a code of conduct.
- A "Report a problem" menu item that opens a prefilled GitHub issue (version, OS, no data) and the logs folder.

## Features people will ask for first
Proxy settings and client certificates (corporate networks), a cookie manager, saving a response to a file, HAR / Insomnia /
Bruno import, `pm.sendRequest`, GraphQL schema introspection and docs, gRPC from `.proto` files (reflection is required today),
searching inside request bodies, and localisation.
