"""Generates assets/zapi.ico (Zapi's own icon) and the Lavati-branded installer artwork.

Run from the project folder:  python tools/make_assets.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF, Qt                                   # noqa: E402
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication                              # noqa: E402

from zapi import VERSION, theme                                         # noqa: E402

LIGHT = "#F4F2FA"
SUB = "#5B4A8A"


def wizard_large(path):
    w, h = 328, 628
    pm = QPixmap(w, h)
    pm.fill(QColor(LIGHT))
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.TextAntialiasing)
    p.fillRect(0, 0, w, 8, QColor(theme.LOGO_PURPLE_1))
    fade = QLinearGradient(0, h * 0.72, 0, h)
    fade.setColorAt(0, QColor(LIGHT))
    fade.setColorAt(1, QColor("#C9BDE6"))
    p.fillRect(0, int(h * 0.72), w, h - int(h * 0.72), fade)
    mark = theme.lavati_mark_pixmap(150)
    p.drawPixmap((w - 150) // 2, 70, mark)
    word = theme.lavati_wordmark_pixmap(190)
    p.drawPixmap((w - word.width()) // 2, 250, word)
    f = QFont(theme.ui_family(), 11)
    p.setFont(f)
    p.setPen(QColor(theme.LOGO_DARK))
    p.drawText(QRectF(20, 400, w - 40, 60), Qt.AlignHCenter | Qt.AlignTop | Qt.TextWordWrap,
               "Zapi\nA fast, local-first API client")
    f2 = QFont(theme.ui_family(), 9)
    p.setFont(f2)
    p.setPen(QColor("#7A6BA6"))
    p.drawText(QRectF(0, 570, w, 24), Qt.AlignHCenter, f"Version {VERSION}")
    p.end()
    save_bmp(pm, path)


def wizard_small(path):
    s = 110
    pm = QPixmap(s, s)
    pm.fill(QColor(LIGHT))
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.drawPixmap(15, 15, theme.lavati_mark_pixmap(80))
    p.end()
    save_bmp(pm, path)


def save_bmp(pm, path):
    from PIL import Image
    import io
    from PySide6.QtCore import QBuffer, QIODevice
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    pm.save(buf, "PNG")
    Image.open(io.BytesIO(bytes(buf.data()))).convert("RGB").save(path, "BMP")


def main():
    app = QApplication.instance() or QApplication([])
    theme.load_fonts()
    os.makedirs(os.path.join(ROOT, "assets"), exist_ok=True)
    os.makedirs(os.path.join(ROOT, "installer"), exist_ok=True)
    theme.save_ico(os.path.join(ROOT, "assets", "zapi.ico"))
    theme.logo_pixmap(256).save(os.path.join(ROOT, "assets", "zapi.png"), "PNG")
    wizard_large(os.path.join(ROOT, "installer", "wizard-large.bmp"))
    wizard_small(os.path.join(ROOT, "installer", "wizard-small.bmp"))
    print("assets written")
    return app


if __name__ == "__main__":
    main()
