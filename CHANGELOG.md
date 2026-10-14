# Changelog

## 0.1 (unreleased)
- HTTP, GraphQL and SOAP requests; WebSocket, SSE, MQTT and gRPC (reflection).
- Auth: API key, Bearer, Basic, Digest, OAuth 1.0 / 2.0 (PKCE), AWS Signature v4, Hawk, NTLMv2.
- Collections with folders, environments (initial / current values, secrets), history, runner with data files, mock server.
- JavaScript pre-request and test scripts (`pm` API); `zapi-cli run` for CI with JSON / JUnit reports.
- Import: cURL (bash / cmd), `fetch()`, Postman, OpenAPI. Export: Zapi JSON, Postman v2.1, history, full backup.
- Themes: System, Dark, Light, Midnight, High contrast, with accent colours and editor font size.
- Copy menu on responses, Save body to file, copyable tables, tab / history context menus, cookie manager, proxy and
  client-certificate settings, built-in help (F1).
- Secrets are encrypted at rest with Windows DPAPI (including hand-typed auth headers, params, URLs and scripts that hold
  credentials); shared exports leave credentials out; full backups are protected by a passphrase.
- Installer: upgrades close the running app and remove files the old version had; downgrades are refused; uninstall keeps your
  data by default (`/REMOVEDATA` deletes it) and only stops processes that run from the install folder. Fixed a stray `;` left in
  PATH, the uninstaller killing itself, and fonts not loading from folders with non-Latin characters. Added an automated
  lifecycle test.
- Crash reports stay on the computer; third-party licence notices are installed with the app.
