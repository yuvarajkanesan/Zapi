"""Test package. Qt's offscreen platform prints "This plugin does not support propagateSizeHints()" (and raise(), lower() ...)
every time a test shows or resizes a window. Those lines are harmless and bury real output, so they are filtered here;
every other Qt message still reaches stderr."""
import sys

try:
    from PySide6.QtCore import qInstallMessageHandler
except ImportError:                                   # tests that do not need Qt can still run
    qInstallMessageHandler = None

PLATFORM_NOISE = ("This plugin does not support",)


def _handler(_mode, _context, message):
    if any(message.startswith(n) for n in PLATFORM_NOISE):
        return
    sys.stderr.write(message + "\n")


if qInstallMessageHandler is not None:
    qInstallMessageHandler(_handler)
