"""The facts a bug report needs, and nothing the user did not choose to share: no URLs, no collections, no secrets."""
import platform
import sys

from . import APP_NAME, VERSION, theme, vault


def info(ws=None):
    """A short plain-text block to paste into a bug report."""
    lines = [
        f"{APP_NAME} {VERSION}" + (" (installed build)" if getattr(sys, "frozen", False) else " (from source)"),
        f"Windows: {platform.platform()}",
        f"Python {sys.version.split()[0]}",
    ]
    try:
        import PySide6
        lines.append(f"Qt for Python {PySide6.__version__}")
    except ImportError:
        pass
    try:
        from PySide6.QtGui import QGuiApplication
        app = QGuiApplication.instance()
        if app is not None:
            screens = [f"{s.size().width()}x{s.size().height()} @{s.devicePixelRatio():g}x" for s in app.screens()]
            lines.append("Screens: " + ", ".join(screens))
    except ImportError:
        pass
    lines.append(f"Theme: {theme.THEME_NAME}")
    if ws is not None:
        s = ws.settings
        lines.append(f"Collections: {len(ws.collections)}, environments: {len(ws.environments)}, "
                     f"history: {len(ws.history)}")
        lines.append(f"Secrets encrypted: {'yes' if ws.secrets_encrypted() else 'no'}"
                     f"{'' if vault.available() else ' (not available on this system)'}")
        proxy = s.get("proxy", "")
        lines.append("Proxy: " + ("system" if not proxy else "none" if proxy == "none" else "custom"))
        lines.append(f"Client certificate: {'set' if s.get('client_cert') else 'not set'}, "
                     f"SSL verification: {'on' if s.get('verify_ssl', True) else 'off'}")
    return "\n".join(lines)
