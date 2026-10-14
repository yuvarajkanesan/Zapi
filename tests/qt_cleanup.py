"""Test helper: destroy every top-level widget a test left behind.

Qt re-applies the stylesheet to ALL live widgets whenever it changes, so widgets that earlier tests created and never
destroyed make later (theme) tests slower and slower."""
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication


def purge():
    app = QApplication.instance()
    if app is None:
        return
    for w in app.topLevelWidgets():
        w.close()
        w.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()
