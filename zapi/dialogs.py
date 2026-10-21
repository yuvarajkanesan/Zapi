"""Dialogs: import, paste cURL, code snippets, save request, settings and the collection runner."""
import json
import time

from PySide6.QtCore import QObject, QThread, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                               QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPlainTextEdit,
                               QPushButton, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget,
                               QVBoxLayout, QWidget)

from . import curl, executor, importers, mock, runner, store, theme, vault
from .widgets import CodeEditor, CopyTable, KVTable, copy_text, primary


class SendThread(QThread):
    """Runs one request (pre-request script, send, tests) off the UI thread."""
    done = Signal(object)          # executor.ExecResult

    def __init__(self, req, scopes, opts, jar, info, parent=None):
        super().__init__(parent)
        self.req, self.scopes, self.opts, self.jar, self.info = req, scopes, opts, jar, info
        self.cancelled = False

    def run(self):
        out = executor.execute(self.req, self.scopes, self.opts, jar=self.jar, info=self.info)
        if not self.cancelled:
            self.done.emit(out)


class CurlDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Paste cURL, fetch() or a file")
        self.resize(620, 340)
        self.request = None
        self.text = ""
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Paste a cURL command (bash, cmd or PowerShell), a fetch() call, or the contents of a "
                             "Postman / OpenAPI / Zapi file."))
        self.edit = QPlainTextEdit()
        self.edit.setFont(theme.mono(10))
        self.edit.setPlaceholderText("curl -X POST https://api.example.com/users -H 'Content-Type: application/json' -d '{\"name\": \"Asha\"}'")
        lay.addWidget(self.edit, 1)
        self.err = QLabel()
        self.err.setStyleSheet(f"color: {theme.RED};")
        self.err.setWordWrap(True)
        lay.addWidget(self.err)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Import")
        primary(bb.button(QDialogButtonBox.Ok))
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        clip = QApplication.clipboard().text()
        if curl.looks_like_import(clip):
            self.edit.setPlainText(clip)

    def _ok(self):
        text = self.edit.toPlainText()
        try:
            kind, obj = curl.parse_any(text)
        except curl.CurlError as e:
            self.err.setText(str(e))
            return
        self.request = obj if kind == "request" else None
        self.text = text
        self.accept()


class ImportDialog(QDialog):
    """Pick a Postman / OpenAPI / Zapi file. `result` is a list of (kind, object)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import")
        self.resize(520, 220)
        self.results = []
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Import a Postman collection or environment (v2.x), an OpenAPI 3 / Swagger spec,\n"
                             "or a collection / environment exported from Zapi."))
        row = QHBoxLayout()
        self.path = QLineEdit()
        self.path.setPlaceholderText("Choose one or more .json, .yaml or .har files")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row.addWidget(self.path, 1)
        row.addWidget(browse)
        lay.addLayout(row)
        self.err = QLabel()
        self.err.setStyleSheet(f"color: {theme.RED};")
        self.err.setWordWrap(True)
        lay.addWidget(self.err)
        lay.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Import")
        primary(bb.button(QDialogButtonBox.Ok))
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.paths = []

    def _browse(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Import", "", "API files (*.json *.yaml *.yml *.har);;All files (*)")
        if paths:
            self.paths = paths
            self.path.setText("; ".join(paths))

    def _ok(self):
        paths = self.paths or [p.strip() for p in self.path.text().split(";") if p.strip()]
        if not paths:
            self.err.setText("Choose a file first.")
            return
        self.results = []
        errors = []
        for p in paths:
            try:
                self.results.append(importers.import_file(p))
            except (OSError, importers.ImportError_) as e:
                errors.append(f"{p}: {e}")
        if errors and not self.results:
            self.err.setText("\n".join(errors))
            return
        self.errors = errors
        self.accept()


class CodeDialog(QDialog):
    def __init__(self, req, values, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Generate code")
        self.resize(640, 420)
        self.req, self.values = req, values
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel("Language"))
        self.lang = QComboBox()
        self.lang.addItems(curl.LANGUAGES)
        self.lang.currentIndexChanged.connect(self._render)
        row.addWidget(self.lang)
        row.addStretch(1)
        copy = QPushButton("Copy")
        copy.clicked.connect(lambda: copy_text(self.view.toPlainText(), copy))
        row.addWidget(copy)
        lay.addLayout(row)
        self.view = CodeEditor(read_only=True)
        lay.addWidget(self.view, 1)
        note = QLabel("Variables are filled in from the active environment.")
        note.setObjectName("muted")
        lay.addWidget(note)
        self._render()

    def _render(self):
        try:
            self.view.setPlainText(curl.generate(self.req, self.values, self.lang.currentText()))
        except Exception as e:  # noqa: BLE001
            self.view.setPlainText(f"Could not generate code: {e}")


class SaveRequestDialog(QDialog):
    def __init__(self, ws, name, collection_id="", parent=None):
        super().__init__(parent)
        self.ws = ws
        self.setWindowTitle("Save request")
        self.resize(420, 200)
        form = QFormLayout(self)
        self.name = QLineEdit(name)
        form.addRow("Request name", self.name)
        self.combo = QComboBox()
        for c in ws.collections:
            self.combo.addItem(c["name"], c["id"])
        self.combo.addItem("+ New collection…", "")
        i = self.combo.findData(collection_id)
        self.combo.setCurrentIndex(i if i >= 0 else 0)
        self.new_name = QLineEdit("My Collection")
        form.addRow("Collection", self.combo)
        form.addRow("New collection", self.new_name)
        self.combo.currentIndexChanged.connect(self._toggle)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        primary(bb.button(QDialogButtonBox.Save))
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        form.addRow(bb)
        self.collection_id = ""
        self._toggle()

    def _toggle(self):
        self.new_name.setEnabled(self.combo.currentData() == "")

    def _ok(self):
        if not self.name.text().strip():
            return
        cid = self.combo.currentData()
        if cid == "":
            cid = self.ws.add_collection(self.new_name.text().strip() or "My Collection")["id"]
        self.collection_id = cid
        self.accept()


def _swatch(color, size=14):
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    p.drawRoundedRect(0, 0, size, size, 4, 4)
    p.end()
    return QIcon(pm)


class SettingsDialog(QDialog):
    look_changed = False
    def __init__(self, ws, parent=None):
        super().__init__(parent)
        self.ws = ws
        self.setWindowTitle("Settings")
        self.resize(580, 680)
        form = QFormLayout(self)
        s = ws.settings
        self.theme = QComboBox()
        self.theme.addItems(theme.THEME_CHOICES)
        self.theme.setCurrentText(s.get("theme", "Dark") if s.get("theme", "Dark") in theme.THEME_CHOICES else "Dark")
        self.accent = QComboBox()
        for name, color in theme.ACCENTS.items():
            self.accent.addItem(_swatch(color), name)
        self.accent.setCurrentText(s.get("accent", "Amber"))
        self.code_size = QSpinBox()
        self.code_size.setRange(8, 18)
        self.code_size.setSuffix(" pt")
        self.code_size.setValue(int(s.get("code_size", 10)))
        form.addRow("Theme", self.theme)
        form.addRow("Accent colour", self.accent)
        form.addRow("Editor font size", self.code_size)
        note = QLabel("“System” follows the Windows light / dark setting. The window reloads when you change "
                      "the look; your tabs come back as they were.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        form.addRow(note)
        self.name = QLineEdit(s.get("workspace_name", "My Workspace"))
        self.ssl = QCheckBox("Verify SSL certificates")
        self.ssl.setChecked(s.get("verify_ssl", True))
        self.redirects = QCheckBox("Follow redirects")
        self.redirects.setChecked(s.get("follow_redirects", True))
        self.timeout = QSpinBox()
        self.timeout.setRange(1, 600)
        self.timeout.setSuffix(" s")
        self.timeout.setValue(int(s.get("timeout", 30)))
        form.addRow("Workspace name", self.name)
        form.addRow("", self.ssl)
        form.addRow("", self.redirects)
        form.addRow("Request timeout", self.timeout)
        proxy = s.get("proxy", "")
        self.proxy_mode = QComboBox()
        self.proxy_mode.addItems(["System default", "No proxy", "Custom"])
        self.proxy_mode.setCurrentIndex(0 if proxy == "" else 1 if proxy.lower() == "none" else 2)
        self.proxy_url = QLineEdit(proxy if proxy and proxy.lower() != "none" else "")
        self.proxy_url.setPlaceholderText("http://proxy.example.com:8080")
        self.proxy_url.setEnabled(self.proxy_mode.currentIndex() == 2)
        self.proxy_mode.currentIndexChanged.connect(lambda i: self.proxy_url.setEnabled(i == 2))
        form.addRow("Proxy", self.proxy_mode)
        form.addRow("", self.proxy_url)
        self.cert, self.key, self.ca = QLineEdit(s.get("client_cert", "")), QLineEdit(s.get("client_key", "")), \
            QLineEdit(s.get("ca_file", ""))
        for edit_, hint in ((self.cert, "Client certificate (.pem / .crt), optional"),
                            (self.key, "Private key (.key), if not inside the certificate file"),
                            (self.ca, "Custom CA bundle (.pem), replaces the system store")):
            edit_.setPlaceholderText(hint)
            row = QHBoxLayout()
            row.addWidget(edit_, 1)
            browse = QPushButton("Browse…")
            browse.clicked.connect(lambda _=False, e=edit_: self._browse(e))
            row.addWidget(browse)
            form.addRow({"cert": "Client certificate", "key": "Client key", "ca": "CA bundle"}[
                "cert" if edit_ is self.cert else "key" if edit_ is self.key else "ca"], row)
        self.encrypt = QCheckBox("Compress and encrypt my workspace file")
        self.encrypt.setChecked(bool(s.get("encrypt_secrets", True)) and vault.available())
        self.encrypt.setEnabled(vault.available())
        form.addRow("Security", self.encrypt)
        enc_note = QLabel(
            "Everything (requests, collections, environments, history and settings) is compressed and encrypted with "
            "Windows (DPAPI) before it is written to disk, and credentials are encrypted a second time inside it. "
            "It only opens under your Windows account on this computer, so to move your data to another machine use "
            "Export → Full workspace backup with a passphrase." if vault.available() else
            "Encryption is only available on Windows; the workspace is stored as plain text here.")
        enc_note.setObjectName("muted")
        enc_note.setWordWrap(True)
        form.addRow(enc_note)
        where = QLabel(f"Your data is stored in:\n{ws.path}")
        where.setObjectName("muted")
        where.setWordWrap(True)
        where.setTextInteractionFlags(Qt.TextSelectableByMouse)
        form.addRow(where)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        primary(bb.button(QDialogButtonBox.Save))
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def _browse(self, edit_):
        path, _ = QFileDialog.getOpenFileName(self, "Choose file", "", "Certificates (*.pem *.crt *.cer *.key);;All files (*)")
        if path:
            edit_.setText(path)

    def _ok(self):
        s = self.ws.settings
        s["workspace_name"] = self.name.text().strip() or "My Workspace"
        s["verify_ssl"] = self.ssl.isChecked()
        s["follow_redirects"] = self.redirects.isChecked()
        s["timeout"] = self.timeout.value()
        s["encrypt_secrets"] = self.encrypt.isChecked() if vault.available() else False
        mode = self.proxy_mode.currentIndex()
        s["proxy"] = "" if mode == 0 else "none" if mode == 1 else self.proxy_url.text().strip()
        s["client_cert"], s["client_key"], s["ca_file"] = self.cert.text().strip(), self.key.text().strip(), \
            self.ca.text().strip()
        self.look_changed = (s.get("theme", "Dark"), s.get("accent", "Amber"), int(s.get("code_size", 10))) != (
            self.theme.currentText(), self.accent.currentText(), self.code_size.value())
        s["theme"], s["accent"], s["code_size"] = self.theme.currentText(), self.accent.currentText(), self.code_size.value()
        self.ws.save()
        self.accept()


class RunThread(QThread):
    step = Signal(object)          # runner.StepResult
    finished_all = Signal(object)  # runner.RunSummary

    def __init__(self, collection, scopes, opts, iterations, data, delay, bail, folder, parent=None):
        super().__init__(parent)
        self.args = (collection, scopes, opts)
        self.kw = dict(iterations=iterations, data=data, delay_ms=delay, bail=bail, folder=folder)
        self.stop = False

    def run(self):
        summary = runner.run_collection(*self.args, **self.kw, on_step=self.step.emit,
                                        should_stop=lambda: self.stop)
        self.finished_all.emit(summary)


class RunnerDialog(QDialog):
    """Collection Runner: iterations, CSV / JSON data files, delay, stop on failure, export of the results."""

    def __init__(self, ws, collection_id="", folder="", parent=None):
        super().__init__(parent)
        self.ws = ws
        self.thread_ = None
        self.summary = None
        self.data = None
        self.setWindowTitle("Collection Runner")
        self.resize(900, 620)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(8)
        row = QHBoxLayout()
        self.combo = QComboBox()
        for c in ws.collections:
            self.combo.addItem(f"{c['name']} ({len(c['items'])})", c["id"])
        i = self.combo.findData(collection_id)
        if i >= 0:
            self.combo.setCurrentIndex(i)
        self.combo.currentIndexChanged.connect(self._fill_folders)
        self.folder = QComboBox()
        row.addWidget(self.combo, 1)
        row.addWidget(self.folder, 1)
        form.addRow("Run", row)
        self.iterations = QSpinBox()
        self.iterations.setRange(1, 10000)
        self.iterations.setValue(1)
        self.delay = QSpinBox()
        self.delay.setRange(0, 60000)
        self.delay.setSuffix(" ms")
        r2 = QHBoxLayout()
        r2.addWidget(self.iterations)
        r2.addSpacing(12)
        r2.addWidget(QLabel("Delay between requests"))
        r2.addWidget(self.delay)
        r2.addStretch(1)
        form.addRow("Iterations", r2)
        r3 = QHBoxLayout()
        self.data_edit = QLineEdit()
        self.data_edit.setReadOnly(True)
        self.data_edit.setPlaceholderText("Optional CSV or JSON file: one iteration per row, columns become variables")
        pick = QPushButton("Select file…")
        pick.clicked.connect(self._pick_data)
        clear = QPushButton("Clear")
        clear.clicked.connect(self._clear_data)
        r3.addWidget(self.data_edit, 1)
        r3.addWidget(pick)
        r3.addWidget(clear)
        form.addRow("Data file", r3)
        r4 = QHBoxLayout()
        self.bail = QCheckBox("Stop at the first failure")
        self.keep = QCheckBox("Keep variable changes made by scripts")
        self.keep.setChecked(True)
        r4.addWidget(self.bail)
        r4.addWidget(self.keep)
        r4.addStretch(1)
        form.addRow("", r4)
        lay.addLayout(form)
        btns = QHBoxLayout()
        self.run_btn = primary(QPushButton("Run"))
        self.run_btn.clicked.connect(self._toggle)
        self.export_btn = QPushButton("Export results…")
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self._export)
        btns.addWidget(self.run_btn)
        btns.addWidget(self.export_btn)
        btns.addStretch(1)
        lay.addLayout(btns)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["#", "Iter", "Request", "Status", "Time", "Tests", "Details"])
        h = self.table.horizontalHeader()
        h.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        h.setSectionResizeMode(2, QHeaderView.Stretch)
        h.setSectionResizeMode(6, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setColumnWidth(0, 40)
        self.table.setColumnWidth(1, 40)
        self.table.setColumnWidth(3, 110)
        self.table.setColumnWidth(4, 70)
        self.table.setColumnWidth(5, 80)
        lay.addWidget(self.table, 1)
        self.summary_lb = QLabel("")
        self.summary_lb.setObjectName("muted")
        lay.addWidget(self.summary_lb)
        self._fill_folders(folder)
        if not ws.collections:
            self.run_btn.setEnabled(False)
            self.summary_lb.setText("Create a collection with some requests first.")

    def _fill_folders(self, keep=""):
        self.folder.clear()
        self.folder.addItem("All requests", "")
        c = self.ws.collection(self.combo.currentData())
        for f in (c or {}).get("folders", []):
            self.folder.addItem(f"Folder: {f}", f)
        if isinstance(keep, str) and keep:
            i = self.folder.findData(keep)
            if i >= 0:
                self.folder.setCurrentIndex(i)

    def _pick_data(self):
        path, _ = QFileDialog.getOpenFileName(self, "Iteration data", "", "Data files (*.csv *.json);;All files (*)")
        if not path:
            return
        try:
            self.data = runner.load_iteration_data(path)
        except (OSError, ValueError) as e:
            self.summary_lb.setText(f"Could not read the data file: {e}")
            return
        self.data_edit.setText(f"{path}  ({len(self.data)} rows)")
        self.iterations.setEnabled(False)

    def _clear_data(self):
        self.data = None
        self.data_edit.clear()
        self.iterations.setEnabled(True)

    def _toggle(self):
        if self.thread_ is not None and self.thread_.isRunning():
            self.thread_.stop = True
            self.run_btn.setText("Stopping…")
            return
        cid = self.combo.currentData()
        c = self.ws.collection(cid)
        folder = self.folder.currentData() or ""
        if not c or not runner.select_requests(c, folder):
            self.summary_lb.setText("There are no requests to run.")
            return
        opts = self.ws.request_options()
        self.before = self.ws.scopes(cid)
        self.table.setRowCount(0)
        self.summary = None
        self.export_btn.setEnabled(False)
        self.summary_lb.setText("Running…")
        self.run_btn.setText("Stop")
        for w in (self.combo, self.folder):
            w.setEnabled(False)
        self.thread_ = RunThread(c, self.before, opts, self.iterations.value(), self.data, self.delay.value(),
                                 self.bail.isChecked(), folder, self)
        self.thread_.step.connect(self._step)
        self.thread_.finished_all.connect(self._finished)
        self.thread_.start()

    def _step(self, s):
        i = self.table.rowCount()
        self.table.insertRow(i)
        if s.error:
            status, color = "ERROR", theme.RED
        else:
            status, color = f"{s.status} {s.reason}".strip(), theme.status_color(s.status)
        failed = len(s.failed_tests)
        tests = f"{len(s.tests) - failed}/{len(s.tests)}" if s.tests else "-"
        detail = s.error or (s.failed_tests[0]["name"] + ": " + s.failed_tests[0].get("error", "")
                             if failed else "; ".join(s.script_errors))
        cells = [str(i + 1), str(s.iteration + 1), f"{s.method}  {s.name}", status, f"{s.ms} ms" if not s.error else "",
                 tests, detail]
        for j, text in enumerate(cells):
            item = QTableWidgetItem(text)
            if j == 3:
                item.setForeground(QColor(color))
            if j == 5 and failed:
                item.setForeground(QColor(theme.RED))
            self.table.setItem(i, j, item)
        self.table.scrollToBottom()

    def _finished(self, summary):
        self.summary = summary
        self.run_btn.setText("Run")
        for w in (self.combo, self.folder):
            w.setEnabled(True)
        self.export_btn.setEnabled(True)
        self.summary_lb.setText(
            f"{summary.requests} request(s), {summary.failed_requests} failed to send · "
            f"{summary.tests_total - summary.tests_failed}/{summary.tests_total} tests passed · "
            f"{summary.duration_ms / 1000:.2f} s" + (" · stopped early" if summary.stopped else ""))
        if self.keep.isChecked():
            self.ws.apply_scopes(self.combo.currentData(), self.before, summary.scopes)
            self.ws.save()
            self.changed_variables = True

    def _export(self):
        if self.summary is None:
            return
        path, kind = QFileDialog.getSaveFileName(self, "Export results", "results.json",
                                                 "JSON (*.json);;JUnit XML (*.xml)")
        if not path:
            return
        text = runner.report_junit(self.summary) if path.lower().endswith(".xml") else runner.report_json(self.summary)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    changed_variables = False

    def reject(self):
        if self.thread_ is not None and self.thread_.isRunning():
            self.thread_.stop = True
            self.thread_.wait(15000)
        super().reject()


class MockBridge(QObject):
    """Carries 'a request hit the mock server' from the server's threads to the UI thread."""
    request = Signal(dict)


class MockDialog(QDialog):
    """Edit mock routes and start / stop the local mock server (it keeps running after the dialog closes)."""

    def __init__(self, ws, server, bridge, parent=None):
        super().__init__(parent)
        self.ws, self.server, self.bridge = ws, server, bridge
        self.setWindowTitle("Mock server")
        self.resize(980, 640)
        cfg = ws.settings.setdefault("mock", {"port": 3000, "cors": True, "routes": []})
        self.cfg = cfg
        server.routes = cfg["routes"]
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("Port"))
        self.port = QSpinBox()
        self.port.setRange(0, 65535)
        self.port.setValue(int(cfg.get("port", 3000)))
        self.port.setToolTip("0 picks a free port")
        top.addWidget(self.port)
        self.cors = QCheckBox("Allow CORS")
        self.cors.setChecked(cfg.get("cors", True))
        top.addWidget(self.cors)
        self.start_btn = primary(QPushButton("Start"))
        self.start_btn.clicked.connect(self._toggle)
        top.addWidget(self.start_btn)
        self.url_lb = QLabel("")
        self.url_lb.setTextInteractionFlags(Qt.TextSelectableByMouse)
        top.addWidget(self.url_lb, 1)
        self.gen_combo = QComboBox()
        for c in ws.collections:
            self.gen_combo.addItem(c["name"], c["id"])
        gen = QPushButton("Add routes from collection")
        gen.clicked.connect(self._generate)
        top.addWidget(self.gen_combo)
        top.addWidget(gen)
        lay.addLayout(top)

        split = QSplitter(Qt.Vertical)
        mid = QSplitter(Qt.Horizontal)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["On", "Method", "Path", "Status", "Delay (ms)"])
        hh = self.table.horizontalHeader()
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setColumnWidth(0, 40)
        self.table.setColumnWidth(1, 100)
        self.table.setColumnWidth(3, 70)
        self.table.setColumnWidth(4, 90)
        self.table.itemChanged.connect(self._cell_changed)
        self.table.itemSelectionChanged.connect(self._load_detail)
        ll.addWidget(self.table, 1)
        brow = QHBoxLayout()
        add = QPushButton("Add route")
        add.clicked.connect(self._add)
        rm = QPushButton("Remove")
        rm.clicked.connect(self._remove)
        brow.addWidget(add)
        brow.addWidget(rm)
        brow.addStretch(1)
        exp = QPushButton("Export routes…")
        exp.clicked.connect(self._export_routes)
        imp = QPushButton("Import routes…")
        imp.clicked.connect(self._import_routes)
        brow.addWidget(exp)
        brow.addWidget(imp)
        ll.addLayout(brow)
        mid.addWidget(left)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(8, 0, 0, 0)
        hint = QLabel("Path parameters: /users/:id  ·  in the body use {{params.id}}, {{query.page}}, {{body}}, "
                      "{{headers.authorization}}, {{$guid}}")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        rl.addWidget(hint)
        self.headers = KVTable(key_hint="Response header", value_hint="Value", variables=False)   # mock templates, not env variables
        self.headers.changed.connect(self._detail_changed)
        self.headers.setMaximumHeight(150)
        rl.addWidget(self.headers)
        self.body = CodeEditor()
        self.body.setPlaceholderText("Response body")
        self.body.textChanged.connect(self._detail_changed)
        rl.addWidget(self.body, 1)
        mid.addWidget(right)
        mid.setSizes([460, 480])
        split.addWidget(mid)
        self.log = QPlainTextEdit()
        self.log.setObjectName("code")
        self.log.setReadOnly(True)
        self.log.setFont(theme.mono(9))
        self.log.setPlaceholderText("Requests received by the mock server appear here.")
        split.addWidget(self.log)
        split.setSizes([420, 160])
        lay.addWidget(split, 1)
        self._loading = False
        self._refill()
        self._sync_running()
        bridge.request.connect(self._log_request)

    def _routes(self):
        return self.cfg["routes"]

    def _refill(self, select=0):
        self._loading = True
        self.table.setRowCount(0)
        for i, r in enumerate(self._routes()):
            self.table.insertRow(i)
            on = QTableWidgetItem()
            on.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            on.setCheckState(Qt.Checked if r.get("enabled", True) else Qt.Unchecked)
            self.table.setItem(i, 0, on)
            combo = QComboBox()
            combo.addItems(["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "ANY"])
            combo.setCurrentText(r["method"])
            combo.currentTextChanged.connect(lambda t, row=r: self._set(row, "method", t))
            self.table.setCellWidget(i, 1, combo)
            self.table.setItem(i, 2, QTableWidgetItem(r["path"]))
            self.table.setItem(i, 3, QTableWidgetItem(str(r["status"])))
            self.table.setItem(i, 4, QTableWidgetItem(str(r.get("delay_ms", 0))))
        self._loading = False
        if self._routes():
            self.table.selectRow(min(select, len(self._routes()) - 1))
        else:
            self._load_detail()

    def _set(self, route, key, value):
        if not self._loading:
            route[key] = value

    def _cell_changed(self, item):
        if self._loading:
            return
        r = self._routes()[item.row()]
        text = item.text().strip()
        if item.column() == 0:
            r["enabled"] = item.checkState() == Qt.Checked
        elif item.column() == 2:
            r["path"] = text if text.startswith("/") or text == "*" else "/" + text
        elif item.column() == 3:
            r["status"] = int(text) if text.isdigit() else r["status"]
        elif item.column() == 4:
            r["delay_ms"] = int(text) if text.isdigit() else r.get("delay_ms", 0)

    def _current(self):
        i = self.table.currentRow()
        return self._routes()[i] if 0 <= i < len(self._routes()) else None

    def _load_detail(self):
        r = self._current()
        self._loading = True
        self.headers.set_rows(r["headers"] if r else [])
        self.body.setPlainText(r["body"] if r else "")
        self._loading = False
        self.headers.setEnabled(r is not None)
        self.body.setEnabled(r is not None)

    def _detail_changed(self, *_):
        r = self._current()
        if r is None or self._loading:
            return
        r["headers"] = self.headers.rows()
        r["body"] = self.body.toPlainText()

    def _add(self):
        self._routes().append(mock.new_route("GET", "/new", 200, "{}", name="New route"))
        self._refill(len(self._routes()) - 1)

    def _remove(self):
        i = self.table.currentRow()
        if 0 <= i < len(self._routes()):
            del self._routes()[i]
            self._refill(i)

    def _export_routes(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export mock routes", "zapi-mock-routes.json", "JSON (*.json)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"zapi": "mock", "routes": self._routes()}, f, indent=2, ensure_ascii=False)
            self.log.appendPlainText(f"Exported {len(self._routes())} route(s) to {path}")

    def _import_routes(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import mock routes", "", "JSON (*.json)")
        if not path:
            return
        try:
            kind, obj = importers.import_file(path)
            if kind != "mock":
                raise importers.ImportError_("That file does not contain mock routes.")
        except (OSError, importers.ImportError_) as e:
            self.log.appendPlainText(f"Import failed: {e}")
            return
        routes = [mock.new_route(**{k: r[k] for k in ("method", "path", "status", "body", "headers", "delay_ms", "name")
                                    if k in r}) | {"enabled": r.get("enabled", True)} for r in obj.get("routes", [])]
        self._routes().extend(routes)
        self._refill(len(self._routes()) - 1)
        self.log.appendPlainText(f"Imported {len(routes)} route(s).")

    def _generate(self):
        c = self.ws.collection(self.gen_combo.currentData())
        if c is None:
            return
        have = {(r["method"], r["path"]) for r in self._routes()}
        added = [r for r in mock.routes_from_collection(c) if (r["method"], r["path"]) not in have]
        self._routes().extend(added)
        self._refill(len(self._routes()) - 1)
        self.log.appendPlainText(f"Added {len(added)} route(s) from “{c['name']}”.")

    def _toggle(self):
        self._save_cfg()
        if self.server.running:
            self.server.stop()
        else:
            self.server.cors = self.cors.isChecked()
            try:
                self.server.start(self.port.value())
            except OSError as e:
                self.log.appendPlainText(f"Could not start on port {self.port.value()}: {e}")
        self._sync_running()

    def _save_cfg(self):
        self.cfg["port"] = self.port.value()
        self.cfg["cors"] = self.cors.isChecked()
        self.server.cors = self.cors.isChecked()
        self.ws.save()

    def _sync_running(self):
        on = self.server.running
        self.start_btn.setText("Stop" if on else "Start")
        self.port.setEnabled(not on)
        self.url_lb.setText(f"Listening on {self.server.url()}" if on else "Stopped")

    def _log_request(self, e):
        match = f"  → {e['matched']}" if e["matched"] else "  (no route)"
        self.log.appendPlainText(f"{time.strftime('%H:%M:%S')}  {e['method']} {e['path']}  {e['status']}  {e['ms']} ms{match}")

    def done(self, result):
        self._save_cfg()
        try:
            self.bridge.request.disconnect(self._log_request)
        except (RuntimeError, TypeError):
            pass
        super().done(result)


class PassphraseDialog(QDialog):
    """Asks for a backup passphrase. `confirm` = choosing a new one (asked twice, at least 8 characters);
    `allow_plain` adds a "no passphrase" choice for people who want a readable file."""

    MIN_LENGTH = 8

    def __init__(self, parent=None, confirm=True, allow_plain=False, title="Backup passphrase", text=""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(460, 240)
        self.confirm, self.plain, self.passphrase = confirm, False, ""
        lay = QVBoxLayout(self)
        intro = QLabel(text or ("Choose a passphrase. Without it nobody can open the backup, and Zapi cannot recover "
                                "it for you." if confirm else "Enter the passphrase this backup was saved with."))
        intro.setWordWrap(True)
        lay.addWidget(intro)
        self.first = QLineEdit()
        self.first.setEchoMode(QLineEdit.Password)
        self.first.setPlaceholderText("Passphrase")
        lay.addWidget(self.first)
        self.second = QLineEdit()
        self.second.setEchoMode(QLineEdit.Password)
        self.second.setPlaceholderText("Repeat the passphrase")
        self.second.setVisible(confirm)
        lay.addWidget(self.second)
        self.plain_box = QCheckBox("No passphrase: save a readable file (secrets in plain text)")
        self.plain_box.setVisible(allow_plain)
        self.plain_box.toggled.connect(self._validate)
        lay.addWidget(self.plain_box)
        self.err = QLabel("")
        self.err.setStyleSheet(f"color: {theme.RED}; font-size: 8pt;")
        self.err.setWordWrap(True)
        lay.addWidget(self.err)
        self.bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        primary(self.bb.button(QDialogButtonBox.Ok))
        self.bb.accepted.connect(self._ok)
        self.bb.rejected.connect(self.reject)
        lay.addWidget(self.bb)
        self.first.textChanged.connect(self._validate)
        self.second.textChanged.connect(self._validate)
        self._validate()

    def _validate(self, *_):
        if self.plain_box.isChecked():
            self.first.setEnabled(False)
            self.second.setEnabled(False)
            self.err.setText("")
            self.bb.button(QDialogButtonBox.Ok).setEnabled(True)
            return
        self.first.setEnabled(True)
        self.second.setEnabled(True)
        msg = ""
        if self.confirm and self.first.text() and len(self.first.text()) < self.MIN_LENGTH:
            msg = f"Use at least {self.MIN_LENGTH} characters."
        elif self.confirm and self.second.text() and self.first.text() != self.second.text():
            msg = "The two passphrases are different."
        ok = bool(self.first.text()) and not msg and (not self.confirm or (
            len(self.first.text()) >= self.MIN_LENGTH and self.first.text() == self.second.text()))
        self.err.setText(msg)
        self.bb.button(QDialogButtonBox.Ok).setEnabled(ok)

    def _ok(self):
        self.plain = self.plain_box.isChecked()
        self.passphrase = "" if self.plain else self.first.text()
        self.accept()


class CookieDialog(QDialog):
    """The cookies Zapi received during this session (they are kept in memory, never written to disk)."""

    def __init__(self, jar, parent=None):
        super().__init__(parent)
        self.jar = jar
        self.setWindowTitle("Cookies")
        self.resize(820, 420)
        lay = QVBoxLayout(self)
        hint = QLabel("Cookies set by the servers you call are sent back automatically. They live in memory and are "
                      "forgotten when you close Zapi.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self.table = CopyTable(0, 6)
        self.table.setHorizontalHeaderLabels(["Domain", "Path", "Name", "Value", "Expires", "Secure"])
        h = self.table.horizontalHeader()
        h.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        h.setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setShowGrid(False)
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        rm = QPushButton("Delete selected")
        rm.clicked.connect(self._delete)
        clear = QPushButton("Clear all")
        clear.clicked.connect(self._clear)
        row.addWidget(rm)
        row.addWidget(clear)
        row.addStretch(1)
        lay.addLayout(row)
        self.refill()

    def refill(self):
        cookies = list(self.jar)
        self.table.setRowCount(len(cookies))
        for i, c in enumerate(cookies):
            exp = time.strftime("%Y-%m-%d %H:%M", time.localtime(c.expires)) if c.expires else "session"
            for j, text in enumerate((c.domain, c.path, c.name, c.value or "", exp, "yes" if c.secure else "")):
                self.table.setItem(i, j, QTableWidgetItem(text))

    def _delete(self):
        for r in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True):
            d, p, n = (self.table.item(r, c).text() for c in (0, 1, 2))
            try:
                self.jar.clear(d, p, n)
            except KeyError:
                pass
        self.refill()

    def _clear(self):
        self.jar.clear()
        self.refill()


SHORTCUTS = [
    ("Right-click a collection or folder", "Settings (auth, headers, scripts its requests inherit), Run… (Collection Runner) or Load test… (many virtual users, live response-time chart)"),
    ("Ctrl+K  /  Ctrl+P", "Command palette: jump to a request, switch environment, run a command"),
    ("Ctrl+Enter", "Send the request (or connect / send in WebSocket, MQTT, gRPC tabs)"),
    ("Ctrl+N  /  Ctrl+T", "New request"), ("Ctrl+S", "Save the request"), ("Ctrl+W", "Close the tab"),
    ("Ctrl+L", "Focus the URL bar"), ("Ctrl+Shift+C", "Copy the request as cURL"),
    ("Ctrl+Shift+E", "Environments"), ("Ctrl+`", "Show / hide the console"), ("F1", "This help"),
    ("Paste a cURL command, fetch() call or API file into any URL bar", "Imports it"),
    ("Right-click a tab, request, history entry or response", "Copy, duplicate, move, save and more"),
    ("Hover a {{variable}}", "Shows its value and where it comes from (secrets stay masked)"),
    ("Type {{  or  Ctrl+Space", "Suggests existing variables, filtered as you type; Up/Down, Enter or Tab to insert, Esc to close"),
    ("Bulk Edit (above the Headers, Params and form-urlencoded tables)", "Edit all rows as text, one Key: Value per line; // turns a line off"),
]

SCRIPTING = """Scripts are JavaScript. They run in a separate process with a 10 second limit.

PRE-REQUEST (before the request is sent)
  pm.environment.set('token', 'abc')        pm.globals / pm.collectionVariables / pm.variables work the same way
  pm.request.headers.upsert({key: 'X-Id', value: pm.variables.replaceIn('{{$guid}}')})
  pm.request.url = pm.request.url.toString() + '?x=1'        pm.request.method = 'POST'
  CryptoJS.HmacSHA256(message, key).toString()               btoa() / atob()

TESTS (after the response arrives)
  pm.test('Status is 200', () => pm.response.to.have.status(200))
  pm.expect(pm.response.json().id).to.be.a('number').and.above(0)
  pm.response.to.have.header('content-type')    pm.response.responseTime    pm.response.text()
  pm.environment.set('id', pm.response.json().id)            // pass a value to the next request
  postman.setNextRequest('Name')  /  postman.setNextRequest(null)    // in the Runner

Also: console.log(), pm.iterationData.get('column') in the Runner, pm.info.requestName.
Not available: pm.sendRequest() and require() of anything except 'crypto-js'.

VARIABLES
  {{name}} works in URLs, headers, params, auth and bodies. Order: environment > collection > globals.
  Dynamic: {{$guid}} {{$timestamp}} {{$isoTimestamp}} {{$randomInt}}
"""


class HelpDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Help")
        self.resize(760, 560)
        lay = QVBoxLayout(self)
        tabs = QTabWidget()
        table = QTableWidget(len(SHORTCUTS), 2)
        table.setHorizontalHeaderLabels(["Shortcut / action", "What it does"])
        table.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        table.horizontalHeader().setStretchLastSection(True)
        table.setColumnWidth(0, 330)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setShowGrid(False)
        for i, (a, b) in enumerate(SHORTCUTS):
            table.setItem(i, 0, QTableWidgetItem(a))
            table.setItem(i, 1, QTableWidgetItem(b))
        tabs.addTab(table, "Shortcuts")
        ref = QPlainTextEdit(SCRIPTING)
        ref.setReadOnly(True)
        ref.setObjectName("code")
        ref.setFont(theme.mono(theme.CODE_SIZE))
        tabs.addTab(ref, "Scripting and variables")
        lay.addWidget(tabs)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        bb.button(QDialogButtonBox.Close).clicked.connect(self.reject)
        lay.addWidget(bb)
