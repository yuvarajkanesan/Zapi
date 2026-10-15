"""Where Zapi keeps its data (JSON in the per-user app data folder) and the small JSON helpers around it."""
import json
import os
import sys
import tempfile


PORTABLE_MARKER = "portable.txt"


def portable_dir():
    """The folder holding the exe when this is the portable edition (Zapi-Portable.exe, or any exe with a portable.txt
    next to it), else None. Not for source runs."""
    if not getattr(sys, "frozen", False):
        return None
    here = os.path.dirname(os.path.abspath(sys.executable))
    by_name = os.path.basename(sys.executable).lower().startswith("zapi-portable")
    return here if by_name or os.path.isfile(os.path.join(here, PORTABLE_MARKER)) else None


def data_dir():
    """Workspace, logs and caches. ZAPI_HOME wins; a portable build keeps everything in a data folder beside the exe."""
    override = os.environ.get("ZAPI_HOME")
    portable = portable_dir()
    if override:
        base = override
    elif portable:
        base = os.path.join(portable, "ZapiData")
    elif sys.platform == "win32":
        base = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "Zapi")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support/Zapi")
    else:
        base = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "zapi")
    os.makedirs(base, exist_ok=True)
    return base


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, data):
    """Write atomically so a crash never leaves a half-written workspace behind."""
    save_bytes(path, json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8"))


def save_bytes(path, data):
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".zapi-", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
