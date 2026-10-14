"""Colours (selectable palettes + accent), fonts, painted icons and the application stylesheet."""
import os
import sys

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QIcon, QLinearGradient, QPainter, QPainterPath, QPen,
                           QPixmap, QPolygonF)

# ---- palettes ----------------------------------------------------------------
# Every colour the UI uses comes from here, so a theme is just a different dict (plus an accent colour).
_DARK = dict(
    IS_DARK=True, BG="#0b0d12", PANEL="#10131a", PANEL2="#151922", FIELD="#0d1016", BORDER="#1f2430",
    BORDER2="#2a3040", TEXT="#e8ebf2", MUTED="#8a93a6", FAINT="#5c6479", GREEN="#3ddc97", RED="#ff6b6b",
    BLUE="#5b9dff", PURPLE="#b392f0", ORANGE="#ff9f5a", HOVER="#161b26", SELECTED="#1c2230", MENU_SEL="#232a3b",
    BTN_HOVER="#1b2030", LINE_HL="#141925", SYN_STRING="#7ee0a1", SYN_KEY="#8ab4ff", SYN_NUMBER="#ffb86b",
    SYN_LITERAL="#c792ea", M_WS="#4dd0e1", M_SSE="#f48fb1", M_GRPC="#80cbc4", M_SOAP="#ce93d8")
PALETTES = {
    "Dark": _DARK,
    "Midnight": {**_DARK, "BG": "#0a0f1e", "PANEL": "#0f1730", "PANEL2": "#151f3f", "FIELD": "#0b1228",
                 "BORDER": "#1b2749", "BORDER2": "#26345f", "TEXT": "#e6ecff", "MUTED": "#8f9bc2",
                 "FAINT": "#5a6690", "HOVER": "#131d3b", "SELECTED": "#1b2850", "MENU_SEL": "#223261",
                 "BTN_HOVER": "#1c2a52", "LINE_HL": "#111a36", "BLUE": "#6aa5ff", "RED": "#ff6b81"},
    "Light": dict(
        IS_DARK=False, BG="#f6f7f9", PANEL="#ffffff", PANEL2="#eef0f4", FIELD="#ffffff", BORDER="#dfe3ea",
        BORDER2="#c9cfda", TEXT="#1b2030", MUTED="#5d667a", FAINT="#98a1b3", GREEN="#12894f", RED="#d6334a",
        BLUE="#2467d8", PURPLE="#7a4fd1", ORANGE="#c4620a", HOVER="#eceff4", SELECTED="#e3e8f1",
        MENU_SEL="#e3e8f1", BTN_HOVER="#e4e8ef", LINE_HL="#f0f3f8", SYN_STRING="#1a7f4b", SYN_KEY="#1f5fc4",
        SYN_NUMBER="#a8570a", SYN_LITERAL="#8a3fc4", M_WS="#0a7f92", M_SSE="#b8346f", M_GRPC="#1b7f75",
        M_SOAP="#8c3aa6"),
    "High contrast": {**_DARK, "BG": "#000000", "PANEL": "#0a0a0a", "PANEL2": "#141414", "FIELD": "#000000",
                      "BORDER": "#6b6b6b", "BORDER2": "#9a9a9a", "TEXT": "#ffffff", "MUTED": "#d0d0d0",
                      "FAINT": "#a8a8a8", "GREEN": "#4dff9f", "BLUE": "#7db4ff", "PURPLE": "#d2b4ff",
                      "ORANGE": "#ffb066", "HOVER": "#1f1f1f", "SELECTED": "#2d2d2d", "MENU_SEL": "#333333",
                      "BTN_HOVER": "#262626", "LINE_HL": "#121212", "SYN_STRING": "#8fffb8", "SYN_KEY": "#a9ccff",
                      "SYN_NUMBER": "#ffcf8a", "SYN_LITERAL": "#e0c2ff"},
}
THEME_CHOICES = ["System", "Dark", "Light", "Midnight", "High contrast"]
ACCENTS = {   # base colours; hover and the text on top are derived per theme so contrast always holds
    "Amber": "#ffc83d", "Blue": "#2f6fe8", "Green": "#2fbf71", "Purple": "#7c4dee", "Pink": "#ff5d9e",
    "Orange": "#ff8a3d",
}
CODE_SIZE = 10                  # editor font size in points (a setting)
THEME_NAME = "Dark"


def _mix(a, b, t):
    """`t` of colour a blended with the rest of colour b, as #rrggbb."""
    ca, cb = QColor(a), QColor(b)
    return QColor.fromRgbF(ca.redF() * t + cb.redF() * (1 - t), ca.greenF() * t + cb.greenF() * (1 - t),
                           ca.blueF() * t + cb.blueF() * (1 - t)).name()


def _lum(c):
    c = QColor(c)
    lin = [(v / 12.92) if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in (c.redF(), c.greenF(), c.blueF())]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a, b):
    """WCAG contrast ratio between two colours (1 to 21)."""
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def derive_accent(base, bg, is_dark):
    """(accent, hover, text-on-accent): darkened on light themes until it stands out from the background,
    text is whichever of near-black / white reads better, hover moves in the direction that keeps the contrast."""
    accent = base
    if not is_dark:
        for _ in range(14):
            if contrast(accent, bg) >= 3.0:
                break
            accent = QColor(accent).darker(108).name()
    dark_text, light_text = "#14120a", "#ffffff"
    text = dark_text if contrast(accent, dark_text) >= contrast(accent, light_text) else light_text
    hover = QColor(accent).lighter(110).name() if text == dark_text else QColor(accent).darker(110).name()
    return accent, hover, text


def system_theme():
    """'Dark' or 'Light' following the Windows app mode (Qt 6.5+); Dark when there is no application yet."""
    try:
        from PySide6.QtGui import QGuiApplication
        if QGuiApplication.instance() is not None:
            return "Dark" if QGuiApplication.styleHints().colorScheme() != Qt.ColorScheme.Light else "Light"
    except (ImportError, AttributeError):
        pass
    return "Dark"


def apply(name="Dark", accent="Amber", code_size=10):
    """Make `name` (a THEME_CHOICES entry) the active palette. Call before building any widget."""
    resolved = system_theme() if name == "System" else name
    pal = PALETTES.get(resolved, PALETTES["Dark"])
    acc = derive_accent(ACCENTS.get(accent, ACCENTS["Amber"]), pal["BG"], pal["IS_DARK"])
    g = globals()
    g.update(pal)
    g["ACCENT"], g["ACCENT_HOVER"], g["ACCENT_TEXT"] = acc
    g["ACCENT_DIS"] = _mix(acc[0], pal["BG"], 0.35)           # disabled primary button
    g["ACCENT_DIS_TEXT"] = _mix(acc[2], pal["BG"], 0.55)
    g["SEL_BG"] = _mix(acc[0], pal["BG"], 0.30)                # text selection
    g["CODE_SIZE"] = int(code_size)
    g["THEME_NAME"] = resolved
    g["METHOD_COLORS"] = {"GET": pal["GREEN"], "POST": acc[0] if pal["IS_DARK"] else pal["ORANGE"],
                          "PUT": pal["BLUE"], "PATCH": pal["PURPLE"], "DELETE": pal["RED"], "HEAD": pal["MUTED"],
                          "OPTIONS": pal["MUTED"], "WS": pal["M_WS"], "SSE": pal["M_SSE"], "MQTT": pal["ORANGE"],
                          "GRPC": pal["M_GRPC"], "SOAP": pal["M_SOAP"]}


# declared here so readers and linters can see them; apply() below fills every one in
IS_DARK = True
BG = PANEL = PANEL2 = FIELD = BORDER = BORDER2 = TEXT = MUTED = FAINT = ""
GREEN = RED = BLUE = PURPLE = ORANGE = ""
HOVER = SELECTED = MENU_SEL = BTN_HOVER = LINE_HL = SEL_BG = ""
SYN_STRING = SYN_KEY = SYN_NUMBER = SYN_LITERAL = ""
M_WS = M_SSE = M_GRPC = M_SOAP = ""
ACCENT = ACCENT_HOVER = ACCENT_TEXT = ACCENT_DIS = ACCENT_DIS_TEXT = ""
METHOD_COLORS = {}
apply()

# Lavati Softwares brand (installer artwork and the About box)
LOGO_PURPLE_1, LOGO_PURPLE_2, LOGO_DARK, LOGO_ORANGE = "#6D28D9", "#4C1D95", "#2A1160", "#FF7A59"


def method_color(method):
    return METHOD_COLORS.get(method.upper(), MUTED)


def status_color(code):
    if code <= 0:
        return RED
    if code < 300:
        return GREEN
    if code < 400:
        return BLUE
    if code < 500:
        return ACCENT
    return RED


def resource(*parts):
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)


# ---- fonts ------------------------------------------------------------------
_families = {}


def load_fonts():
    folder = resource("fonts")
    if os.path.isdir(folder):
        for name in sorted(os.listdir(folder)):
            if name.lower().endswith(".ttf"):
                QFontDatabase.addApplicationFont(os.path.join(folder, name))
    have = set(QFontDatabase.families())
    _families["ui"] = next((f for f in ("IBM Plex Sans", "Segoe UI", "Arial") if f in have), "Sans Serif")
    _families["mono"] = next((f for f in ("JetBrains Mono", "Cascadia Mono", "Consolas", "Courier New") if f in have),
                             "Monospace")


def ui_family():
    return _families.get("ui", "Segoe UI")


def mono_family():
    return _families.get("mono", "Consolas")


def mono(size=10, bold=False):
    f = QFont(mono_family(), size)
    f.setStyleHint(QFont.Monospace)
    f.setBold(bold)
    return f


# ---- painted icons ----------------------------------------------------------
def _bolt_path(s):
    pts = [(0.56, 0.08), (0.24, 0.56), (0.47, 0.56), (0.40, 0.92), (0.76, 0.42), (0.52, 0.42)]
    path = QPainterPath()
    path.moveTo(pts[0][0] * s, pts[0][1] * s)
    for x, y in pts[1:]:
        path.lineTo(x * s, y * s)
    path.closeSubpath()
    return path


def icon(name, color=None, size=18):
    """A simple line icon painted at runtime (no image files to ship). Default colour: the current text colour."""
    color = color or TEXT
    pm = QPixmap(size * 2, size * 2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    s = size * 2
    pen = QPen(QColor(color), s * 0.085)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    if name == "bolt":
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(color))
        p.drawPath(_bolt_path(s))
    elif name == "plus":
        p.drawLine(QPointF(s * .5, s * .22), QPointF(s * .5, s * .78))
        p.drawLine(QPointF(s * .22, s * .5), QPointF(s * .78, s * .5))
    elif name == "close":
        p.drawLine(QPointF(s * .28, s * .28), QPointF(s * .72, s * .72))
        p.drawLine(QPointF(s * .72, s * .28), QPointF(s * .28, s * .72))
    elif name in ("chevron-down", "chevron-right", "chevron-up"):
        path = QPainterPath()
        if name == "chevron-down":
            path.moveTo(s * .3, s * .4); path.lineTo(s * .5, s * .62); path.lineTo(s * .7, s * .4)
        elif name == "chevron-up":
            path.moveTo(s * .3, s * .6); path.lineTo(s * .5, s * .38); path.lineTo(s * .7, s * .6)
        else:
            path.moveTo(s * .4, s * .3); path.lineTo(s * .62, s * .5); path.lineTo(s * .4, s * .7)
        p.drawPath(path)
    elif name == "folder":
        path = QPainterPath()
        path.moveTo(s * .14, s * .30); path.lineTo(s * .40, s * .30); path.lineTo(s * .48, s * .38)
        path.lineTo(s * .86, s * .38); path.lineTo(s * .86, s * .74); path.lineTo(s * .14, s * .74)
        path.closeSubpath()
        p.drawPath(path)
    elif name == "search":
        p.drawEllipse(QRectF(s * .2, s * .2, s * .38, s * .38))
        p.drawLine(QPointF(s * .53, s * .53), QPointF(s * .8, s * .8))
    elif name == "copy":
        p.drawRoundedRect(QRectF(s * .34, s * .34, s * .46, s * .46), s * .06, s * .06)
        p.drawPath(_corner(s))
    elif name == "code":
        a = QPainterPath(); a.moveTo(s * .38, s * .3); a.lineTo(s * .18, s * .5); a.lineTo(s * .38, s * .7)
        b = QPainterPath(); b.moveTo(s * .62, s * .3); b.lineTo(s * .82, s * .5); b.lineTo(s * .62, s * .7)
        p.drawPath(a); p.drawPath(b)
    elif name == "globe":
        p.drawEllipse(QRectF(s * .18, s * .18, s * .64, s * .64))
        p.drawEllipse(QRectF(s * .36, s * .18, s * .28, s * .64))
        p.drawLine(QPointF(s * .18, s * .5), QPointF(s * .82, s * .5))
    elif name == "terminal":
        a = QPainterPath(); a.moveTo(s * .22, s * .34); a.lineTo(s * .42, s * .5); a.lineTo(s * .22, s * .66)
        p.drawPath(a)
        p.drawLine(QPointF(s * .5, s * .68), QPointF(s * .78, s * .68))
    elif name == "download":
        p.drawLine(QPointF(s * .5, s * .2), QPointF(s * .5, s * .62))
        a = QPainterPath(); a.moveTo(s * .32, s * .46); a.lineTo(s * .5, s * .64); a.lineTo(s * .68, s * .46)
        p.drawPath(a)
        p.drawLine(QPointF(s * .22, s * .8), QPointF(s * .78, s * .8))
    elif name == "trash":
        p.drawLine(QPointF(s * .22, s * .3), QPointF(s * .78, s * .3))
        p.drawRoundedRect(QRectF(s * .3, s * .3, s * .4, s * .5), s * .05, s * .05)
        p.drawLine(QPointF(s * .42, s * .18), QPointF(s * .58, s * .18))
    elif name == "dots":
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(color))
        for x in (.25, .5, .75):
            p.drawEllipse(QPointF(s * x, s * .5), s * .055, s * .055)
    p.end()
    pm.setDevicePixelRatio(2)
    return QIcon(pm)


def dot_icon(color, size=10):
    pm = QPixmap(size * 2, size * 2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    p.drawEllipse(2, 2, size * 2 - 4, size * 2 - 4)
    p.end()
    pm.setDevicePixelRatio(2)
    return QIcon(pm)


def _corner(s):
    path = QPainterPath()
    path.moveTo(s * .26, s * .6); path.lineTo(s * .26, s * .26); path.lineTo(s * .6, s * .26)
    return path


# ---- application icon (Zapi's own mark) ---------------------------------------
def logo_pixmap(size):
    """Zapi: an amber rounded square with a dark lightning bolt."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    g = QLinearGradient(0, 0, size, size)
    g.setColorAt(0, QColor("#ffd54f"))
    g.setColorAt(1, QColor("#ffb300"))
    p.setPen(Qt.NoPen)
    p.setBrush(g)
    r = size * 0.24
    p.drawRoundedRect(QRectF(0, 0, size, size), r, r)
    p.setBrush(QColor("#1a1405"))
    p.translate(size * 0.11, size * 0.09)
    p.scale(0.78, 0.82)
    p.drawPath(_bolt_path(size))
    p.end()
    return pm


def app_icon():
    ic = QIcon()
    for sz in (16, 20, 24, 32, 40, 48, 64, 128, 256):
        ic.addPixmap(logo_pixmap(sz))
    return ic


def save_ico(path):
    """Write a multi-size .ico (for the exe, shortcuts and the installer)."""
    import io
    from PIL import Image
    from PySide6.QtCore import QBuffer, QIODevice
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    logo_pixmap(256).save(buf, "PNG")
    Image.open(io.BytesIO(bytes(buf.data()))).save(
        path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


# ---- Lavati Softwares brand (installer / About) --------------------------------
def _chevron_path(x, y, w, h):
    path = QPainterPath()
    path.moveTo(x, y)
    path.lineTo(x + w / 2, y + h)
    path.lineTo(x + w, y)
    return path


def lavati_mark_pixmap(size):
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    g = QLinearGradient(0, 0, size, size)
    g.setColorAt(0, QColor(LOGO_PURPLE_1))
    g.setColorAt(1, QColor(LOGO_PURPLE_2))
    p.setPen(Qt.NoPen)
    p.setBrush(g)
    r = size * 0.27
    p.drawRoundedRect(QRectF(0, 0, size, size), r, r)
    pen = QPen(QColor("#ffffff"), size * 0.095)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawPath(_chevron_path(size * 0.255, size * 0.27, size * 0.49, size * 0.30))
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(LOGO_ORANGE))
    bar = size * 0.075
    p.drawRoundedRect(QRectF(size * 0.31, size * 0.69, size * 0.38, bar), bar / 2, bar / 2)
    p.end()
    return pm


def lavati_wordmark_pixmap(width, tagline=True, dark_text=LOGO_DARK):
    """'lavati' with the chevron as its v, plus the SOFTWARES line."""
    from PySide6.QtGui import QFontMetricsF
    h = int(width * (0.42 if tagline else 0.30))
    pm = QPixmap(width, h)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.TextAntialiasing)
    f = QFont(ui_family(), 10)
    f.setWeight(QFont.Bold)
    f.setPixelSize(int(width * 0.26))
    fm = QFontMetricsF(f)
    vw = fm.horizontalAdvance("v") * 0.92
    total = fm.horizontalAdvance("la") + vw + fm.horizontalAdvance("ati")
    x = (width - total) / 2
    base = width * 0.22
    p.setFont(f)
    p.setPen(QColor(dark_text))
    p.drawText(QPointF(x, base), "la")
    x += fm.horizontalAdvance("la")
    cap = fm.xHeight()
    pen = QPen(QColor(LOGO_PURPLE_1), width * 0.04)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.drawPath(_chevron_path(x + vw * 0.1, base - cap * 0.95, vw * 0.8, cap * 0.78))
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(LOGO_ORANGE))
    bh = width * 0.028
    p.drawRoundedRect(QRectF(x + vw * 0.22, base + width * 0.012, vw * 0.56, bh), bh / 2, bh / 2)
    x += vw
    p.setPen(QColor(dark_text))
    p.drawText(QPointF(x, base), "ati")
    if tagline:
        f2 = QFont(ui_family(), 10)
        f2.setWeight(QFont.Medium)
        f2.setPixelSize(int(width * 0.055))
        f2.setLetterSpacing(QFont.AbsoluteSpacing, width * 0.022)
        p.setFont(f2)
        p.setPen(QColor("#5B4A8A"))
        p.drawText(QRectF(0, base + width * 0.07, width, width * 0.1), Qt.AlignHCenter | Qt.AlignTop, "SOFTWARES")
    p.end()
    return pm


# ---- stylesheet ---------------------------------------------------------------
def _asset_png(name, painter_fn, size):
    """Qt stylesheets need image files, so small glyphs are painted once into the cache folder."""
    from . import config
    try:
        folder = os.path.join(config.data_dir(), "cache")
        os.makedirs(folder, exist_ok=True)
    except (OSError, ValueError):               # unwritable data folder: the look must not depend on it
        import tempfile
        folder = os.path.join(tempfile.gettempdir(), "zapi-cache")
        os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name)
    if not os.path.exists(path):
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        painter_fn(p, size)
        p.end()
        pm.save(path, "PNG")
    return path.replace("\\", "/")


def _paint_check(p, s):
    pen = QPen(QColor(ACCENT_TEXT), s * 0.14)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    path = QPainterPath()
    path.moveTo(s * .24, s * .52); path.lineTo(s * .43, s * .70); path.lineTo(s * .76, s * .30)
    p.drawPath(path)


def _paint_chevron(p, s):
    pen = QPen(QColor(MUTED), s * 0.11)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    path = QPainterPath()
    path.moveTo(s * .28, s * .4); path.lineTo(s * .5, s * .62); path.lineTo(s * .72, s * .4)
    p.drawPath(path)


def _paint_chevron_right(p, s):
    pen = QPen(QColor(MUTED), s * 0.11)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    path = QPainterPath()
    path.moveTo(s * .4, s * .28); path.lineTo(s * .62, s * .5); path.lineTo(s * .4, s * .72)
    p.drawPath(path)


def _paint_x(p, s, color):
    pen = QPen(QColor(color), s * 0.1)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.drawLine(QPointF(s * .32, s * .32), QPointF(s * .68, s * .68))
    p.drawLine(QPointF(s * .68, s * .32), QPointF(s * .32, s * .68))


def _paint_close(p, s):
    _paint_x(p, s, FAINT)


def _paint_close_hot(p, s):
    _paint_x(p, s, TEXT)


def qss():
    def key(name, *colors):
        return f"{name}-{'-'.join(c.lstrip('#') for c in colors)}.png"
    check = _asset_png(key("check", ACCENT_TEXT, ACCENT), _paint_check, 32)
    chev = _asset_png(key("chevron", MUTED), _paint_chevron, 32)
    chev_r = _asset_png(key("chevron-right", MUTED), _paint_chevron_right, 32)
    close = _asset_png(key("close", FAINT), _paint_close, 32)
    close_hot = _asset_png(key("close-hot", TEXT), _paint_close_hot, 32)
    return f"""
* {{ outline: 0; }}
QWidget {{ background: {BG}; color: {TEXT}; font-family: "{ui_family()}"; font-size: 9pt; }}
QMainWindow, QDialog {{ background: {BG}; }}
QLabel {{ background: transparent; }}
QToolTip {{ background: {PANEL2}; color: {TEXT}; border: 1px solid {BORDER2}; padding: 4px 6px; }}

QFrame#sidebar, QWidget#sidebar {{ background: {PANEL}; border-right: 1px solid {BORDER}; }}
QFrame#sidebar QWidget, QWidget#sidebar QWidget {{ background: transparent; }}
QWidget#header {{ background: {PANEL}; border-bottom: 1px solid {BORDER}; }}
QWidget#header QWidget {{ background: transparent; }}
QWidget#statusbar {{ background: {PANEL}; border-top: 1px solid {BORDER}; }}
QWidget#statusbar QLabel, QWidget#statusbar QPushButton {{ color: {MUTED}; font-size: 8pt; }}
QWidget#statusbar QPushButton {{ border: none; background: transparent; padding: 2px 6px; min-height: 0; }}
QWidget#statusbar QPushButton:hover {{ color: {TEXT}; }}
QWidget#panel {{ background: {BG}; }}
QWidget#card {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 8px; }}
QWidget#card:hover {{ border-color: {BORDER2}; background: {PANEL2}; }}
QWidget#card QLabel {{ background: transparent; }}
QLabel#title {{ font-size: 20pt; font-weight: 700; }}
QLabel#h2 {{ font-size: 12pt; font-weight: 600; }}
QLabel#muted, QLabel[muted="true"] {{ color: {MUTED}; }}
QLabel#crumb {{ color: {MUTED}; }}

QPushButton {{ background: {PANEL2}; border: 1px solid {BORDER}; border-radius: 6px; padding: 6px 14px;
  color: {TEXT}; min-height: 18px; }}
QPushButton:hover {{ border-color: {BORDER2}; background: {BTN_HOVER}; }}
QPushButton:pressed {{ background: {BORDER}; }}
QPushButton:disabled {{ color: {FAINT}; }}
QPushButton[primary="true"] {{ background: {ACCENT}; color: {ACCENT_TEXT}; border: 1px solid {ACCENT}; font-weight: 600; }}
QPushButton[primary="true"]:hover {{ background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
QPushButton[primary="true"]:disabled {{ background: {ACCENT_DIS}; border-color: {ACCENT_DIS}; color: {ACCENT_DIS_TEXT}; }}
QPushButton[danger="true"] {{ color: {RED}; }}
QPushButton[flat="true"] {{ background: transparent; border: none; color: {MUTED}; padding: 4px 8px; }}
QPushButton[flat="true"]:hover {{ color: {TEXT}; background: {PANEL2}; }}
QToolButton {{ background: transparent; border: none; border-radius: 4px; padding: 3px; }}
QToolButton:hover {{ background: {PANEL2}; }}
QToolButton::menu-indicator {{ image: none; width: 0; }}

QLineEdit, QPlainTextEdit, QTextEdit, QTextBrowser, QSpinBox {{ background: {FIELD}; border: 1px solid {BORDER};
  border-radius: 6px; padding: 5px 8px; selection-background-color: {SEL_BG}; selection-color: {TEXT}; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus {{ border-color: {ACCENT}; }}
QLineEdit#urlEdit {{ border: none; border-radius: 0; background: {FIELD}; font-family: "{mono_family()}"; font-size: 10pt; padding: 0 12px; }}
QPlainTextEdit#code, QTextBrowser#code {{ font-family: "{mono_family()}"; font-size: {CODE_SIZE}pt; border-radius: 8px; padding: 6px; }}

QComboBox {{ background: {PANEL2}; border: 1px solid {BORDER}; border-radius: 6px; padding: 5px 28px 5px 10px; min-height: 18px; }}
QComboBox:hover {{ border-color: {BORDER2}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow {{ image: url({chev}); width: 14px; height: 14px; }}
QComboBox QAbstractItemView {{ background: {PANEL2}; border: 1px solid {BORDER2}; selection-background-color: {MENU_SEL};
  selection-color: {TEXT}; padding: 3px; outline: 0; }}
QComboBox#methodBox {{ border: none; border-radius: 0; background: {PANEL2}; font-family: "{mono_family()}"; font-weight: 700; padding-left: 16px; min-width: 70px; }}
QComboBox#envBox {{ border-radius: 6px; padding: 4px 26px 4px 10px; min-height: 16px; }}

QTabBar {{ background: transparent; }}
QTabBar::tab {{ background: transparent; color: {MUTED}; padding: 9px 16px; border: none; border-bottom: 2px solid transparent; }}
QTabBar::tab:hover {{ color: {TEXT}; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {ACCENT}; }}
QTabBar#reqTabs::tab {{ background: {PANEL}; border-right: 1px solid {BORDER}; padding: 10px 14px; border-bottom: 2px solid transparent; }}
QTabBar#reqTabs::tab:selected {{ background: {BG}; border-bottom: 2px solid {ACCENT}; }}
QTabWidget::pane {{ border: none; border-top: 1px solid {BORDER}; }}

QTreeWidget, QListWidget, QTableWidget {{ background: transparent; border: none; alternate-background-color: transparent; }}
QTreeWidget::item, QListWidget::item {{ padding: 5px 4px; border-radius: 5px; }}
QTreeWidget::item:hover, QListWidget::item:hover {{ background: {HOVER}; }}
QTreeWidget::item:selected, QListWidget::item:selected {{ background: {SELECTED}; color: {TEXT}; }}
QTreeWidget::branch {{ background: transparent; }}
QTreeWidget::branch:has-children:closed {{ image: url({chev_r}); }}
QTreeWidget::branch:has-children:open {{ image: url({chev}); }}
QTabBar::close-button {{ image: url({close}); subcontrol-position: right; padding: 2px; border-radius: 4px; }}
QTabBar::close-button:hover {{ image: url({close_hot}); background: {BORDER2}; }}
QTableWidget {{ gridline-color: {BORDER}; selection-background-color: {SELECTED}; }}
QTableWidget::item {{ padding: 2px 6px; border-bottom: 1px solid {BORDER}; }}
QTableWidget QLineEdit {{ border: none; border-radius: 0; background: transparent; padding: 4px 8px; }}
QTableWidget QLineEdit:focus {{ background: {FIELD}; border: 1px solid {ACCENT}; }}
QHeaderView {{ background: transparent; }}
QHeaderView::section:horizontal {{ text-align: left; }}
QHeaderView::section {{ background: {PANEL}; color: {MUTED}; border: none; border-bottom: 1px solid {BORDER};
  padding: 7px 8px; font-weight: 500; }}

QCheckBox {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {BORDER2}; border-radius: 4px; background: {FIELD}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: url({check}); }}
QCheckBox::indicator:hover {{ border-color: {ACCENT}; }}
QRadioButton {{ spacing: 6px; color: {MUTED}; background: transparent; }}
QRadioButton:checked {{ color: {TEXT}; font-weight: 600; }}
QRadioButton::indicator {{ width: 12px; height: 12px; border: 2px solid {BORDER2}; border-radius: 8px; background: transparent; }}
QRadioButton::indicator:checked {{ border-color: {ACCENT}; background: transparent; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {BORDER2}; border-radius: 4px; min-height: 28px; margin: 2px; }}
QScrollBar::handle:vertical:hover {{ background: {FAINT}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {BORDER2}; border-radius: 4px; min-width: 28px; margin: 2px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QSplitter::handle {{ background: {BORDER}; }}
QSplitter::handle:vertical {{ height: 1px; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QMenu {{ background: {PANEL2}; border: 1px solid {BORDER2}; padding: 4px; border-radius: 6px; }}
QMenu::item {{ padding: 6px 22px 6px 14px; border-radius: 4px; }}
QMenu::item:selected {{ background: {MENU_SEL}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 6px; }}
QMessageBox, QInputDialog {{ background: {PANEL}; }}
QProgressBar {{ background: {PANEL2}; border: none; border-radius: 3px; height: 6px; text-align: center; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}
"""


# ---- native title bar ------------------------------------------------------------
def set_titlebar(widget):
    """Dark / light caption and, on Windows 11, a caption colour that matches the header. Silently a no-op elsewhere."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        hwnd = int(widget.winId())
        dwm = ctypes.windll.dwmapi

        def attr(code, value):
            v = ctypes.c_int(value)
            dwm.DwmSetWindowAttribute(hwnd, code, ctypes.byref(v), ctypes.sizeof(v))

        def colorref(hex_color):
            c = QColor(hex_color)
            return c.red() | (c.green() << 8) | (c.blue() << 16)
        attr(20, 1 if IS_DARK else 0)               # DWMWA_USE_IMMERSIVE_DARK_MODE
        attr(35, colorref(PANEL))                    # DWMWA_CAPTION_COLOR (Windows 11)
        attr(36, colorref(TEXT))                     # DWMWA_TEXT_COLOR
        attr(34, colorref(BORDER))                   # DWMWA_BORDER_COLOR
    except (OSError, AttributeError, ValueError):
        pass


def install_titlebar_filter(app):
    """Style every top-level window (main window and dialogs) as it is shown."""
    from PySide6.QtCore import QEvent, QObject
    from PySide6.QtWidgets import QWidget

    class Filter(QObject):
        def eventFilter(self, obj, event):
            if event.type() == QEvent.Show and isinstance(obj, QWidget) and obj.isWindow():
                set_titlebar(obj)
            return False
    f = Filter(app)
    app.installEventFilter(f)
    return f
