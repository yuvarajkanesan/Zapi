"""Local crash reports: an unexpected error is written to %APPDATA%\\Zapi\\logs and the user is told where.
Nothing is ever sent anywhere; the user decides whether to attach the file to a bug report."""
import faulthandler
import os
import platform
import sys
import threading
import time
import traceback

from . import APP_NAME, VERSION, config

KEEP = 10
_installed = False
_fatal_file = None
_showing = False


def logs_dir():
    path = os.path.join(config.data_dir(), "logs")
    os.makedirs(path, exist_ok=True)
    return path


def _rotate(folder):
    files = sorted((f for f in os.listdir(folder) if f.startswith("crash-")), reverse=True)
    for old in files[KEEP:]:
        try:
            os.unlink(os.path.join(folder, old))
        except OSError:
            pass


def write_report(exc_type, exc, tb, where="main"):
    """Writes the report and returns its path (None if even that fails)."""
    try:
        folder = logs_dir()
        path = os.path.join(folder, time.strftime("crash-%Y%m%d-%H%M%S") + f"-{os.getpid()}.log")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"{APP_NAME} {VERSION} crash report ({where} thread)\n")
            f.write(f"time:     {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"python:   {sys.version.split()[0]}  frozen={getattr(sys, 'frozen', False)}\n")
            f.write(f"platform: {platform.platform()}\n\n")
            f.write("".join(traceback.format_exception(exc_type, exc, tb)))
            f.write("\nThis file stays on your computer. Zapi does not send it anywhere.\n")
        _rotate(folder)
        return path
    except Exception:  # noqa: BLE001 - the handler must never raise, whatever is wrong with the disk
        return None


def _show(path, exc):
    """A plain message box; guarded so an error inside the dialog cannot loop."""
    global _showing
    if _showing:
        return
    _showing = True
    try:
        from PySide6.QtCore import QThread
        from PySide6.QtWidgets import QApplication, QMessageBox
        app = QApplication.instance()
        if app is None or QThread.currentThread() is not app.thread():
            return
        box = QMessageBox()
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(f"{APP_NAME} hit an unexpected error")
        box.setText(f"Something went wrong: {exc.__class__.__name__}: {exc}")
        box.setInformativeText(
            (f"Details were saved to:\n{path}\n\n" if path else "") +
            "Your collections are safe. If this keeps happening, attach that file to a bug report. "
            "Zapi never sends it for you.")
        open_btn = box.addButton("Open logs folder", QMessageBox.ActionRole)
        box.addButton("Close", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is open_btn:
            open_logs_folder()
    except Exception:  # noqa: BLE001 - never raise from the crash handler
        pass
    finally:
        _showing = False


def open_logs_folder():
    folder = logs_dir()
    try:
        if sys.platform == "win32":
            os.startfile(folder)                      # noqa: S606 - a folder the app itself created
        elif sys.platform == "darwin":
            import subprocess
            subprocess.Popen(["open", folder])
        else:
            import subprocess
            subprocess.Popen(["xdg-open", folder])
    except OSError:
        pass
    return folder


def install():
    """Route uncaught exceptions (main and worker threads) and hard crashes to the logs folder. Idempotent."""
    global _installed, _fatal_file
    if _installed:
        return
    _installed = True
    previous = sys.excepthook

    def hook(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            return previous(exc_type, exc, tb)
        path = write_report(exc_type, exc, tb)
        _show(path, exc)
        try:
            previous(exc_type, exc, tb)               # still print to a console when there is one
        except Exception:  # noqa: BLE001
            pass

    def thread_hook(args):
        if args.exc_type is SystemExit:
            return
        write_report(args.exc_type, args.exc_value, args.exc_traceback, where=args.thread.name if args.thread else "worker")

    sys.excepthook = hook
    threading.excepthook = thread_hook
    try:                                              # segfaults inside Qt / grpc leave a Python stack here
        _fatal_file = open(os.path.join(logs_dir(), "fatal.log"), "a", encoding="utf-8")
        faulthandler.enable(file=_fatal_file, all_threads=True)
    except (OSError, RuntimeError, ValueError):
        pass
