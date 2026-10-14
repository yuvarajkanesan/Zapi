# Contributing

Thanks for helping. Zapi is a Python 3.14 + PySide6 desktop app; the installer is built with PyInstaller and Inno Setup.

## Set up
```
pip install -r requirements-dev.txt
python main.py                                   # run the app
python -W error::SyntaxWarning -m unittest discover -s tests -t .
python -m pyflakes zapi main.py tools            # catches undefined names
build.bat                                        # PyInstaller build + packaged self-test + installer (needs Inno Setup 6)
```

## Ground rules
- **No telemetry, no accounts, nothing leaves the computer unless the user sent a request.** Changes that break this will not be merged.
- Keep secrets safe: anything credential-like must go through `zapi/vault.py` (encrypted on disk, left out of shared exports).
- Colours come from `zapi/theme.py`; do not hard-code hex values in widgets. Check new UI in the Dark and Light themes.
- Add a test with every change. Network code is tested against local servers, never the internet.
- UI text: plain, friendly, no jargon; say what happened and what to do next.
- Edit files with a proper editor; the repo uses UTF-8 and LF where possible.

## Layout
`zapi/` application code · `tests/` unit and UI tests (offscreen Qt) · `tools/` build helpers · `installer/` Inno Setup assets ·
`docs/` release notes, screenshots and the clean-machine test.
