# PyInstaller spec for the portable edition: ONE file, dist\Zapi-Portable.exe (no installer, no extra files).
# Run through make_portable.bat. Same program as Zapi.exe; it keeps its data beside the exe (see zapi/config.py).
import os
import sys

from PyInstaller.utils.hooks import collect_all

sys.path.insert(0, os.path.abspath("tools"))
import trim  # noqa: E402

datas = [("fonts", "fonts"), ("assets", "assets")]
binaries = []
hidden = ["yaml"]
for pkg in ("grpc", "grpc_reflection", "google.protobuf"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hidden += h

a = Analysis(
    ["main.py"],
    pathex=["."],
    datas=datas,
    binaries=binaries,
    hiddenimports=hidden,
    excludes=["PySide6.QtWebEngineCore", "PySide6.Qt3DCore", "PySide6.QtQuick", "PySide6.QtPdf", "PySide6.QtOpenGL",
              "PySide6.QtDBus", "PySide6.QtSql", "PySide6.QtTest", "tkinter", "PIL", "numpy"],
)
a.binaries = trim.keep(a.binaries)
a.datas = trim.keep(a.datas)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="Zapi-Portable", icon="assets/zapi.ico", console=False,
          version="installer/generated/version_Zapi.txt", upx=False)
