"""Developer tools window: JWT decoder, Base64, URL encoding, hashes, timestamps and UUIDs."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
                               QPushButton, QSpinBox, QTabWidget, QVBoxLayout, QWidget)

from . import devtools, theme
from .widgets import copy_text, primary

TABS = ("jwt", "base64", "url", "hash", "time", "uuid")


def _box(read_only=False, placeholder=""):
    e = QPlainTextEdit()
    e.setFont(theme.mono(10))
    e.setReadOnly(read_only)
    e.setPlaceholderText(placeholder)
    return e


class _Page(QWidget):
    """input on top, result below, a status line and a Copy button."""

    def __init__(self, input_hint, output_hint="Result"):
        super().__init__()
        self.lay = QVBoxLayout(self)
        self.lay.setSpacing(8)
        self.options = QHBoxLayout()
        self.lay.addLayout(self.options)
        self.input = _box(placeholder=input_hint)
        self.output = _box(True, output_hint)
        self.lay.addWidget(self.input, 1)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setObjectName("muted")
        row = QHBoxLayout()
        self.copy_btn = QPushButton("Copy result")
        self.copy_btn.clicked.connect(lambda: copy_text(self.output.toPlainText(), self.copy_btn))
        row.addWidget(self.status, 1)
        row.addWidget(self.copy_btn)
        self.lay.addWidget(self.output, 1)
        self.lay.addLayout(row)

    def show_result(self, text="", error=""):
        self.output.setPlainText(text)
        self.status.setStyleSheet(f"color: {theme.RED};" if error else "")
        self.status.setText(error)
        self.copy_btn.setEnabled(bool(text))


class DevToolsDialog(QDialog):
    def __init__(self, parent=None, tab="jwt", text=""):
        super().__init__(parent)
        self.setWindowTitle("Developer tools")
        self.resize(760, 600)
        lay = QVBoxLayout(self)
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs, 1)
        note = QLabel("Everything here runs on your computer; nothing is sent anywhere.")
        note.setObjectName("muted")
        lay.addWidget(note)
        self._jwt()
        self._b64()
        self._url()
        self._hash()
        self._time()
        self._uuid()
        if tab in TABS:
            self.tabs.setCurrentIndex(TABS.index(tab))
            page = self.tabs.currentWidget()
            if text and hasattr(page, "input"):
                page.input.setPlainText(text)

    # ---- JWT ----
    def _jwt(self):
        p = _Page("Paste a JSON Web Token (a leading 'Bearer ' is fine)", "Decoded header and payload")
        self.jwt_secret = QLineEdit()
        self.jwt_secret.setPlaceholderText("Optional secret, to check an HS256 / HS384 / HS512 signature")
        self.jwt_secret.setEchoMode(QLineEdit.Password)
        p.options.addWidget(QLabel("Secret"))
        p.options.addWidget(self.jwt_secret, 1)

        def run():
            t = p.input.toPlainText().strip()
            if not t:
                return p.show_result()
            try:
                info = devtools.jwt_decode(t, self.jwt_secret.text() or None)
            except devtools.ToolError as e:
                return p.show_result(error=str(e))
            p.show_result(devtools.jwt_summary(info))
            if info["expired"]:
                p.status.setStyleSheet(f"color: {theme.RED};")
                p.status.setText("This token has expired.")
            elif info["signature_ok"] is False:
                p.status.setStyleSheet(f"color: {theme.RED};")
                p.status.setText("The signature does not match this secret.")
            elif info["signature_ok"]:
                p.status.setStyleSheet(f"color: {theme.GREEN};")
                p.status.setText("The signature is valid for this secret.")
        p.input.textChanged.connect(run)
        self.jwt_secret.textChanged.connect(run)
        self.tabs.addTab(p, "JWT")
        self.jwt = p

    # ---- Base64 ----
    def _b64(self):
        p = _Page("Text to encode, or Base64 to decode")
        self.b64_url = QCheckBox("URL-safe alphabet")
        enc, dec = primary(QPushButton("Encode")), QPushButton("Decode")
        swap = QPushButton("Use result as input")

        def encode():
            p.show_result(devtools.b64_encode(p.input.toPlainText(), self.b64_url.isChecked()))

        def decode():
            try:
                p.show_result(devtools.b64_decode(p.input.toPlainText()))
            except devtools.ToolError as e:
                p.show_result(error=str(e))
        enc.clicked.connect(encode)
        dec.clicked.connect(decode)
        swap.clicked.connect(lambda: p.input.setPlainText(p.output.toPlainText()))
        for w in (enc, dec, self.b64_url):
            p.options.addWidget(w)
        p.options.addStretch(1)
        p.options.addWidget(swap)
        self.tabs.addTab(p, "Base64")
        self.b64 = p

    # ---- URL ----
    def _url(self):
        p = _Page("Text to percent-encode, or an encoded string to decode")
        self.url_full = QCheckBox("Keep URL punctuation (: / ? & = #)")
        enc, dec = primary(QPushButton("Encode")), QPushButton("Decode")
        enc.clicked.connect(lambda: p.show_result(devtools.url_encode(p.input.toPlainText(), self.url_full.isChecked())))
        dec.clicked.connect(lambda: p.show_result(devtools.url_decode(p.input.toPlainText())))
        for w in (enc, dec, self.url_full):
            p.options.addWidget(w)
        p.options.addStretch(1)
        self.tabs.addTab(p, "URL")
        self.url = p

    # ---- hashes ----
    def _hash(self):
        p = _Page("Text to hash", "Hex digest")
        self.hash_algo = QComboBox()
        self.hash_algo.addItems(devtools.HASHES)
        self.hash_algo.setCurrentText("SHA-256")
        self.hash_key = QLineEdit()
        self.hash_key.setPlaceholderText("Optional key: makes it an HMAC")
        p.options.addWidget(QLabel("Algorithm"))
        p.options.addWidget(self.hash_algo)
        p.options.addWidget(self.hash_key, 1)

        def run(*_):
            try:
                p.show_result(devtools.hash_text(p.input.toPlainText(), self.hash_algo.currentText(), self.hash_key.text())
                              if p.input.toPlainText() else "")
            except devtools.ToolError as e:
                p.show_result(error=str(e))
        p.input.textChanged.connect(run)
        self.hash_algo.currentTextChanged.connect(run)
        self.hash_key.textChanged.connect(run)
        self.tabs.addTab(p, "Hash")
        self.hash = p

    # ---- time ----
    def _time(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        form = QFormLayout()
        self.time_in = QLineEdit()
        self.time_in.setPlaceholderText("Epoch seconds, epoch milliseconds or a date like 2026-10-21T14:30:00Z (empty = now)")
        now = QPushButton("Now")
        row = QHBoxLayout()
        row.addWidget(self.time_in, 1)
        row.addWidget(now)
        form.addRow("Time", row)
        self.time_out = {}
        for key, label in (("epoch_seconds", "Epoch seconds"), ("epoch_milliseconds", "Epoch milliseconds"),
                           ("iso_utc", "ISO 8601 (UTC)"), ("local", "Your local time"), ("relative", "Relative")):
            e = QLineEdit()
            e.setReadOnly(True)
            e.setFont(theme.mono(10))
            self.time_out[key] = e
            form.addRow(label, e)
        lay.addLayout(form)
        self.time_status = QLabel("")
        self.time_status.setWordWrap(True)
        lay.addWidget(self.time_status)
        lay.addStretch(1)

        def run(*_):
            try:
                info = devtools.time_info(self.time_in.text())
            except devtools.ToolError as e:
                self.time_status.setStyleSheet(f"color: {theme.RED};")
                self.time_status.setText(str(e))
                return
            self.time_status.setText("")
            for k, e in self.time_out.items():
                e.setText(str(info[k]))
        self.time_in.textChanged.connect(run)
        now.clicked.connect(lambda: (self.time_in.clear(), run()))
        run()
        self.tabs.addTab(w, "Time")
        self.time = w

    # ---- UUID ----
    def _uuid(self):
        p = _Page("", "Generated UUIDs (version 4)")
        p.input.hide()
        self.uuid_count = QSpinBox()
        self.uuid_count.setRange(1, 100)
        self.uuid_count.setValue(5)
        gen = primary(QPushButton("Generate"))

        def run():
            p.show_result("\n".join(devtools.new_uuids(self.uuid_count.value())))
        gen.clicked.connect(run)
        p.options.addWidget(QLabel("How many"))
        p.options.addWidget(self.uuid_count)
        p.options.addWidget(gen)
        p.options.addStretch(1)
        run()
        self.tabs.addTab(p, "UUID")
        self.uuid = p

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(ev)
