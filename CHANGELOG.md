# Changelog

## 0.1 (unreleased)
- HTTP, GraphQL and SOAP requests; WebSocket, SSE, MQTT and gRPC (reflection).
- Auth: API key, Bearer, Basic, Digest, OAuth 1.0 / 2.0 (PKCE), AWS Signature v4, Hawk, NTLMv2.
- Collections with folders, environments (initial / current values, secrets), history, runner with data files, mock server.
- JavaScript pre-request and test scripts (`pm` API); `zapi-cli run` for CI with JSON / JUnit reports.
- Import: cURL (bash / cmd), `fetch()`, Postman, OpenAPI. Export: Zapi JSON, Postman v2.1, history, full backup.
- Themes: System, Dark, Light, Midnight, High contrast, with accent colours and editor font size.
- cURL import and code generation for form-data and URL-encoded bodies, checked end to end against a local server:
  `-F` modifiers (`;type=`, `;filename=`) are kept instead of being glued to the value; `--form=`, `--form-string`, `-Fa=1`, `--opt=value` and combined
  flags work; Postman's `file=@"/path with spaces"` and quoted values work; `--data-urlencode`, `-d @file`, `--data-binary @file`, `-T`, `-I`, `--digest`,
  `--ntlm`, `--oauth2-bearer` and `Name;` headers are understood. A browser's "Copy as cURL" for a multipart form (Chrome bash and Windows cmd, Firefox,
  `fetch()`) becomes real form rows instead of one big raw body; the file is not in a copied command, so Zapi asks you to choose it. PowerShell backtick-quotes
  work. Code generation now covers multipart in every language (it only worked in cURL and Python before) and keeps content types and filenames.
  Sending a file field with no file chosen gives a clear message.
- Scripts now live on one Scripts tab, like Postman, with a Pre-request / Post-response switch (a dot shows which has content); the old separate Pre-request Script and Tests tabs are gone. Stored requests are unchanged.
- Collection and folder settings: authorization, headers and pre-request / test scripts set once and inherited by the requests inside (right-click a collection or folder, Settings; also in the command palette). New requests default to "Inherit auth from parent"; a request can set its own, a folder overrides its collection, scripts run collection, then folders, then the request. Applies to Send, the Runner, the CLI, load tests, Copy as cURL and Code. Postman import keeps collection / folder auth and scripts instead of copying them into every request, and export writes them back. The sidebar can no longer be dragged too narrow to fit its tabs and buttons.
- Fixed: clicking a row in the response Headers (and other read-only tables) made its text disappear; selected rows now keep readable text in every theme.
- Command palette (Ctrl+K or Ctrl+P): type a few letters to open a saved request or a history entry, switch environment, or run any command.
- Developer tools (workspace menu, command palette, or right-click a selection in a response): JWT decoder with signature and expiry check, Base64, URL encode / decode, MD5 / SHA hashes and HMAC, epoch time converter, UUID generator.
- Response filter: type a JSON path (`$.data[0].id`, `$..name`, `$.items[?(@.price < 10)].title`, `.length()`) above a JSON response to see only that part.
- Import browser HAR files (DevTools: Network, Save all as HAR): API calls become a collection; pictures, fonts, scripts and stylesheets are skipped.
- `build.bat` options: `test`, `portable`, `all`; retries Inno Setup if antivirus locks the file and prints the SHA-256 of what it built.
- Load test (like Postman's performance testing): right-click a collection or folder, use the Load test button in the status bar or the workspace menu. Virtual users (up to 500) with a Fixed, Ramp up or Spike profile, think time, optional data file; live tiles, a response-time chart and a per-request table; export to JSON or CSV. It asks before sending (it names the hosts) and reminds you to test only servers you own. `zapi-cli load` does the same from a terminal with `--max-error-rate` / `--max-p95` thresholds for CI.
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
