# Security

## Reporting a vulnerability
Please do **not** open a public issue. Send the details privately to **TODO: security contact address** and include the
Zapi version (My Workspace menu → Copy diagnostic info), what you did, and what you saw. We aim to answer within a few days.

## What Zapi protects, and what it does not
- Secrets (passwords, tokens, API keys, variables marked Secret, hand-typed `Authorization` / `Cookie` headers, URLs with
  tokens, scripts or bodies with hard-coded credentials) are encrypted in `%APPDATA%\Zapi\workspace.json` with Windows DPAPI.
  They can only be read by the same Windows user on the same computer.
- Exports you share (collections, environments, history) leave credentials out. A **full backup** is protected with the
  passphrase you choose; if you choose "no passphrase" it contains your secrets in plain text.
- Zapi sends requests only to the addresses you enter. It has no telemetry and no accounts. Crash reports stay on your
  computer in `%APPDATA%\Zapi\logs`.
- Not protected: anything Zapi cannot recognise as a credential (a plain word typed into a body field), memory of a running
  Zapi, and your scripts' behaviour: **scripts run with the same access as the app**, so only run scripts you trust
  (for example from an imported collection).

## Supported versions
Only the latest release receives fixes while Zapi is in preview.
