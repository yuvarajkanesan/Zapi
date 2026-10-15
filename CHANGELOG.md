# Changelog

## 0.1 (unreleased)
- HTTP, GraphQL and SOAP requests; WebSocket, SSE, MQTT and gRPC (reflection).
- Auth: API key, Bearer, Basic, Digest, OAuth 1.0 / 2.0 (PKCE), AWS Signature v4, Hawk, NTLMv2.
- Collections with folders, environments (initial / current values, secrets), history, runner with data files, mock server.
- JavaScript pre-request and test scripts (`pm` API); `zapi-cli run` for CI with JSON / JUnit reports.
- Import: cURL (bash / cmd), `fetch()`, Postman, OpenAPI. Export: Zapi JSON, Postman v2.1, history, full backup.
- Themes: System, Dark, Light, Midnight, High contrast, with accent colours and editor font size.
- The whole workspace file is now compressed (zlib) and encrypted with Windows DPAPI, not only the secret fields; credentials are still encrypted a second time inside. Older plain files are read and upgraded on the next save. A file that cannot be opened (another account or computer) is copied aside untouched and Zapi tells you, instead of silently starting empty. The "looks like a secret" warning under the URL bar is gone because it is no longer needed.
- Right-click menus (History, collections, tabs) always use the active theme; they could appear black when opened from some panels.
- Portable edition: `make_portable.bat` builds a single file, `dist\Zapi-Portable.exe` (no installer, no admin rights). It keeps the workspace, logs and caches in a `ZapiData` folder next to the exe. A `portable.txt` beside any Zapi exe also switches it to portable mode. `zapi-cli.exe --data-dir` shows where a copy keeps its data.
- Request tabs: the method badge no longer touches the tab edge or the neighbouring tab (and OPTIONS / PATCH show as OPT / PAT).
- form-data rows of type `file` get a Choose file button inside the field (and the chooser opens as soon as you pick `file`).
- Menus and dropdown lists no longer show a black backdrop (the title-bar styling is applied only to real windows and dialogs; dropdown lists get an explicit background).
- Dropdown lists (HTTP method and other selectors) highlight the row under the pointer, each method keeps its own colour, and the light theme's popup background and hover/selection colours are fixed and contrast-tested.
- The "secret in body or script" and "unresolved variable" hints are worded more clearly and hide themselves after 20 seconds (they return only if the problem changes).
- Copy buttons turn green with a tick and "Copied" for a moment. The divider between request and response is wider, highlights on hover, and both panes can be dragged small.
- History is grouped by day (Today, Yesterday, weekday, date), newest first. The Variables tab in the sidebar and each environment editor now have a search (by name or value; secret values are never matched).
- Variables: hover a `{{variable}}` to see its value and scope (secrets masked); typing `{{` (or Ctrl+Space) lists existing variables, filtered as you type, in the URL, headers, params, body, auth and bulk-edit fields.
- Bulk Edit for Headers, Params and form-urlencoded bodies (also WebSocket / SSE / gRPC headers): paste a block of `Key: Value`
  lines, `//` disables a line, switches back to the table without losing anything.
- Copy menu on responses, Save body to file, copyable tables, tab / history context menus, cookie manager, proxy and
  client-certificate settings, built-in help (F1).
- Secrets are encrypted at rest with Windows DPAPI (including hand-typed auth headers, params, URLs and scripts that hold
  credentials); shared exports leave credentials out; full backups are protected by a passphrase.
- Installer: upgrades close the running app and remove files the old version had; downgrades are refused; uninstall keeps your
  data by default (`/REMOVEDATA` deletes it) and only stops processes that run from the install folder. Fixed a stray `;` left in
  PATH, the uninstaller killing itself, and fonts not loading from folders with non-Latin characters. Added an automated
  lifecycle test.
- Crash reports stay on the computer; third-party licence notices are installed with the app.
