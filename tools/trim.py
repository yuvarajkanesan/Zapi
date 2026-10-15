"""Files the app never loads. Mirrors the deletions in build.bat, but as patterns a PyInstaller spec can filter with
(a one-file build has no folder to delete from afterwards). Paths are matched lower-case with forward slashes."""
from fnmatch import fnmatch

PATTERNS = [
    "pyside6/opengl32sw.dll", "pyside6/qt6quick*.dll", "pyside6/qt6pdf*.dll", "pyside6/qt6opengl*.dll",
    "pyside6/qt6virtualkeyboard*.dll", "pyside6/plugins/imageformats/qpdf.dll",
    "pyside6/plugins/platforminputcontexts/qtvirtualkeyboardplugin.dll", "pyside6/plugins/generic/qtuiotouchplugin.dll",
    "pyside6/translations/*", "pyside6/qml/*", "pyside6/resources/*",
    "pyside6/qt6webengine*.dll", "pyside6/qt6web*.dll", "pyside6/qt63d*.dll", "pyside6/qt6graphs*.dll",
    "pyside6/qt6charts*.dll", "pyside6/qt6location*.dll", "pyside6/qt6positioning*.dll", "pyside6/qt6sensors*.dll",
    "pyside6/qt6bluetooth*.dll", "pyside6/qt6nfc*.dll", "pyside6/qt6remoteobjects*.dll", "pyside6/qt6scxml*.dll",
    "pyside6/qt6serialport*.dll", "pyside6/qt6spatialaudio*.dll", "pyside6/qt6texttospeech*.dll",
    "pyside6/qt6multimedia*.dll", "pyside6/qt6datavisualization*.dll", "pyside6/qt6shadertools*.dll",
    "pyside6/qt6sql*.dll", "pyside6/qt6dbus*.dll", "pyside6/qt6designer*.dll", "pyside6/qt6help*.dll",
    "pyside6/qtwebengineprocess.exe",
    "pyside6/plugins/qmltooling/*", "pyside6/plugins/tls/*",
    "pyside6/plugins/imageformats/qicns.dll", "pyside6/plugins/imageformats/qtga.dll",
    "pyside6/plugins/imageformats/qtiff.dll", "pyside6/plugins/imageformats/qwbmp.dll",
    "pyside6/plugins/platforms/qdirect2d.dll", "pyside6/plugins/platforms/qminimal.dll",
    "pyside6/qt6labsstylekit*.dll", "pyside6/qt6test*.dll", "pyside6/qt6statemachine*.dll",
    "libssl-3-x64.dll", "libcrypto-3-x64.dll", "api-ms-win-*.dll", "ucrtbase.dll",
]


def wanted(dest):
    d = dest.replace("\\", "/").lower()
    return not any(fnmatch(d, p) for p in PATTERNS)


def keep(toc):
    """Filter a PyInstaller TOC of (dest, src, typecode) entries."""
    return [entry for entry in toc if wanted(entry[0])]
