"""Where Zapi keeps its data (JSON in the per-user app data folder) and the small JSON helpers around it."""
import json
import os
import sys
import tempfile


def data_dir():
    override = os.environ.get("ZAPI_HOME")
    if override:
        base = override
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
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".zapi-", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
