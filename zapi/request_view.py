"""One HTTP / SOAP request tab: URL bar, Params / Authorization / Headers / Body / Scripts editors and the
response viewer (body, headers, cookies, test results)."""
import copy
import json
import urllib.parse

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout,
                               QHeaderView, QInputDialog, QLabel, QLineEdit, QMenu, QPlainTextEdit, QPushButton, QRadioButton,
                               QScrollArea,
                               QSplitter, QStackedWidget, QTabBar, QTableWidget, QTableWidgetItem, QTextBrowser,
                               QToolButton,
                               QVBoxLayout, QWidget)

from shiboken6 import isValid

from . import curl, http_client, oauth2, store, theme, variable_assist, variables
from .widgets import (CodeEditor, CopyTable, KVEditor, KVTable, Worker, copy_text, flat, pretty_json, pretty_xml,
                      primary)

PRETTY_LIMIT = 2 * 1024 * 1024

SNIPPETS = {
    "pre": [
        ("Set an environment variable", "pm.environment.set('name', 'value');"),
        ("Add a header", "pm.request.headers.upsert({ key: 'X-Request-Id', value: pm.variables.replaceIn('{{$guid}}') });"),
        ("Log to the console", "console.log('url:', pm.request.url.toString());"),
        ("Timestamp variable", "pm.variables.set('now', new Date().toISOString());"),
        ("HMAC signature header", "pm.request.headers.upsert({ key: 'X-Signature',\n  value: CryptoJS.HmacSHA256(pm.request.body.raw, pm.environment.get('secret')).toString() });"),
    ],
    "test": [
        ("Status code is 200", "pm.test('Status code is 200', function () {\n  pm.response.to.have.status(200);\n});"),
        ("Response time is below 500 ms", "pm.test('Response time is below 500 ms', function () {\n  pm.expect(pm.response.responseTime).to.be.below(500);\n});"),
        ("JSON value check", "pm.test('Body has the expected value', function () {\n  const json = pm.response.json();\n  pm.expect(json).to.have.property('id');\n});"),
        ("Header is present", "pm.test('Content-Type is JSON', function () {\n  pm.expect(pm.response.headers.get('Content-Type')).to.include('application/json');\n});"),
        ("Save a value for the next request", "pm.environment.set('id', pm.response.json().id);"),
    ],
}

# Authorization forms: (key, label, hint, kind) with kind text | secret | [(value, label), ...]
AUTH_TYPES = [("none", "No auth"), ("bearer", "Bearer token"), ("basic", "Basic auth"), ("digest", "Digest auth"),
              ("apikey", "API key"), ("oauth1", "OAuth 1.0"), ("oauth2", "OAuth 2.0"), ("aws", "AWS Signature"),
              ("hawk", "Hawk"), ("ntlm", "NTLM")]
AUTH_FORMS = {
    "bearer": [("token", "Token", "{{token}}", "secret")],
    "basic": [("username", "Username", "", "text"), ("password", "Password", "", "secret")],
    "digest": [("username", "Username", "", "text"), ("password", "Password", "", "secret")],
    "apikey": [("key", "Key", "X-API-Key", "text"), ("value", "Value", "{{api_key}}", "secret"),
               ("in", "Add to", "", [("header", "Header"), ("query", "Query params")])],
    "oauth1": [("consumer_key", "Consumer key", "", "text"), ("consumer_secret", "Consumer secret", "", "secret"),
               ("token", "Access token", "", "text"), ("token_secret", "Token secret", "", "secret"),
               ("sig_method", "Signature method", "", [(m, m) for m in ("HMAC-SHA1", "HMAC-SHA256", "PLAINTEXT")]),
               ("realm", "Realm", "optional", "text")],
    "oauth2": [("o2_grant", "Grant type", "", [(g, g) for g in oauth2.GRANTS]),
               ("o2_auth_url", "Authorization URL", "https://auth.example.com/authorize", "text"),
               ("o2_token_url", "Access token URL", "https://auth.example.com/token", "text"),
               ("o2_client_id", "Client ID", "", "text"), ("o2_client_secret", "Client secret", "", "secret"),
               ("o2_client_auth", "Client authentication", "", [("Basic header", "Send as Basic Auth header"),
                                                              ("Request body", "Send in request body")]),
               ("o2_scope", "Scope", "read write", "text"),
               ("username", "Username", "password grant only", "text"),
               ("password", "Password", "password grant only", "secret"),
               ("token", "Access token", "{{access_token}} or paste one", "secret"),
               ("o2_refresh_token", "Refresh token", "", "secret"),
               ("o2_prefix", "Header prefix", "Bearer", "text")],
    "aws": [("access_key", "Access key", "", "text"), ("secret_key", "Secret key", "", "secret"),
            ("session_token", "Session token", "optional", "secret"), ("region", "Region", "us-east-1", "text"),
            ("service", "Service name", "execute-api, s3, ...", "text")],
    "hawk": [("hawk_id", "Hawk auth ID", "", "text"), ("hawk_key", "Hawk auth key", "", "secret"),
             ("hawk_algorithm", "Algorithm", "", [("sha256", "sha256"), ("sha1", "sha1")]),
             ("hawk_ext", "ext", "optional", "text")],
    "ntlm": [("username", "Username", "", "text"), ("password", "Password", "", "secret"),
             ("domain", "Domain", "", "text"), ("workstation", "Workstation", "optional", "text")],
}
AUTH_NOTES = {
    "none": "This request does not use any authorization. Variables like {{token}} work in every field.",
    "digest": "Zapi sends the request, answers the server's Digest challenge and retries automatically.",
    "ntlm": "NTLMv2 over one connection: negotiate, challenge, authenticate.",
    "aws": "Signs the request with AWS Signature Version 4 (Authorization and X-Amz-* headers).",
    "hawk": "Adds a Hawk Authorization header with a MAC over the request.",
}
# keys several forms use: only the selected form may write them, so switching type never wipes another form's value
AUTH_SHARED = {"username", "password", "token", "key", "value", "in"}


def human_size(n):
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / 1024 / 1024:.1f} MB"


def status_text(resp):
    return f"{resp.status} {resp.reason}".strip()


def _label(text, name=None):
    lb = QLabel(text)
    if name:
        lb.setObjectName(name)
    return lb


class _TabStrip(QWidget):
    """A QTabBar bound to a QStackedWidget (so tab titles can carry counts)."""

    def __init__(self, titles, stack, parent=None):
        super().__init__(parent)
        self.bar = QTabBar()
        self.bar.setDrawBase(False)
        self.bar.setExpanding(False)
        for t in titles:
            self.bar.addTab(t)
        self.stack = stack
        self.bar.currentChanged.connect(stack.setCurrentIndex)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.bar)
        lay.addStretch(1)
        self.extra = QHBoxLayout()
        self.extra.setSpacing(10)
        lay.addLayout(self.extra)

    def set_title(self, i, text):
        self.bar.setTabText(i, text)


class RequestView(QWidget):
    sendRequested = Signal(object)
    saveRequested = Signal(object)
    codeRequested = Signal(object)
    changed = Signal(object)
    pasteImport = Signal(object, str)      # (view, pasted text): cURL / fetch() / API file in the URL bar

    def __init__(self, request, collection_id="", values_provider=None, parent=None):
        super().__init__(parent)
        self.req = store.normalize_request(request)
        self.collection_id = collection_id
        self.collection_name = ""
        self.persisted = False
        self.values_provider = values_provider or (lambda cid: {})
        self.response = None
        self.exec_result = None
        self._loading = True
        self._saved = None
        self._token_worker = None
        self._build()
        # hover a {{variable}} for its value, type {{ for suggestions (not in the response search or the scripts)
        variable_assist.attach_all(self, skip=(self.search, self.script_pre, self.script_test))
        self.load(self.req)
        self.mark_saved()
        self._loading = False

    # ---- construction --------------------------------------------------------
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # breadcrumb + save / code
        top = QWidget()
        tl = QHBoxLayout(top)
        tl.setContentsMargins(18, 14, 18, 10)
        self.crumb = QLabel()
        self.crumb.setObjectName("crumb")
        self.crumb.setTextFormat(Qt.RichText)
        self.crumb.setCursor(Qt.PointingHandCursor)
        self.crumb.setToolTip("Double-click to rename")
        self.crumb.mouseDoubleClickEvent = lambda e: self.rename()
        tl.addWidget(self.crumb, 1)
        self.save_btn = QPushButton("Save")
        self.save_btn.clicked.connect(lambda: self.saveRequested.emit(self))
        self.code_btn = QPushButton("  Code")
        self.code_btn.setIcon(theme.icon("code", theme.TEXT, 16))
        self.code_btn.clicked.connect(lambda: self.codeRequested.emit(self))
        tl.addWidget(self.save_btn)
        tl.addWidget(self.code_btn)
        root.addWidget(top)

        # url bar
        bar = QFrame()
        bar.setObjectName("urlbar")
        bar.setStyleSheet(f"QFrame#urlbar {{ background: {theme.FIELD}; border: 1px solid {theme.BORDER}; border-radius: 8px; }}"
                          f"QFrame#urlbar QWidget {{ background: transparent; }}")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(0)
        self.method = QComboBox()
        self.method.setObjectName("methodBox")
        self.method.addItems(store.METHODS)
        self.method.setFixedHeight(40)
        self.method.currentTextChanged.connect(self._method_changed)
        self.url = QLineEdit()
        self.url.setObjectName("urlEdit")
        self.url.setPlaceholderText("https://api.example.com/v1/users  (use {{variables}})")
        self.url.setFixedHeight(40)
        self._url_len = 0
        self.url.textEdited.connect(self._url_typed)
        self.url.returnPressed.connect(self._url_enter)
        self.url.editingFinished.connect(self._split_query_from_url)
        self.send_btn = QPushButton("  Send")
        self.send_btn.setIcon(theme.icon("bolt", theme.ACCENT_TEXT, 14))
        primary(self.send_btn, "border-top-left-radius: 0; border-bottom-left-radius: 0;")
        self.send_btn.setFixedSize(92, 40)
        self.send_btn.setToolTip("Send (Ctrl+Enter)")
        self.send_btn.clicked.connect(lambda: self.sendRequested.emit(self))
        sep = QFrame()
        sep.setFixedWidth(1)
        sep.setStyleSheet(f"background: {theme.BORDER};")
        bl.addWidget(self.method)
        bl.addWidget(sep)
        bl.addWidget(self.url, 1)
        bl.addWidget(self.send_btn)
        wrap = QWidget()
        wl = QVBoxLayout(wrap)
        wl.setContentsMargins(18, 0, 18, 0)
        wl.setSpacing(4)
        wl.addWidget(bar)
        self.warn = QLabel()
        self.warn.setStyleSheet(f"color: {theme.ACCENT}; font-size: 8pt;")
        self.warn.hide()
        wl.addWidget(self.warn)
        root.addWidget(wrap)

        split = QSplitter(Qt.Vertical)
        split.setChildrenCollapsible(False)
        top, bottom = self._build_request_side(), self._build_response_side()
        top.setMinimumHeight(110)               # both halves can be dragged small; without this the layouts' own minimums block it
        bottom.setMinimumHeight(110)
        split.addWidget(top)
        split.addWidget(bottom)
        split.setSizes([330, 300])
        root.addWidget(split, 1)

    def _build_request_side(self):
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(18, 8, 18, 0)
        lay.setSpacing(0)
        self.rstack = QStackedWidget()
        self.params = KVEditor(KVTable(key_hint="Key", value_hint="Value"), "param")
        self.headers = KVEditor(KVTable(key_hint="Header", value_hint="Value"), "header")
        self.params.changed.connect(self._params_changed)
        self.headers.changed.connect(self._headers_changed)
        self.rstack.addWidget(self.params)
        self.rstack.addWidget(self._build_auth())
        self.rstack.addWidget(self.headers)
        self.rstack.addWidget(self._build_body())
        self.rstack.addWidget(self._build_script("pre"))
        self.rstack.addWidget(self._build_script("test"))
        self.rtabs = _TabStrip(["Params", "Authorization", "Headers", "Body", "Pre-request Script", "Tests"],
                               self.rstack)
        self.rtabs.bar.setCurrentIndex(0)
        lay.addWidget(self.rtabs)
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background: {theme.BORDER};")
        lay.addWidget(line)
        lay.addWidget(self.rstack, 1)
        return box

    def _build_script(self, which):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 8, 0, 0)
        lay.setSpacing(6)
        row = QHBoxLayout()
        hint = QLabel("Runs before the request is sent." if which == "pre" else
                      "Runs after the response arrives. Use pm.test() and pm.expect().")
        hint.setObjectName("muted")
        row.addWidget(hint, 1)
        snip = QComboBox()
        snip.addItem("Snippets…")
        for title, _code in SNIPPETS[which]:
            snip.addItem(title)
        row.addWidget(snip)
        lay.addLayout(row)
        ed = CodeEditor()
        ed.set_language("js")
        ed.setPlaceholderText("// JavaScript. pm.environment.set('x', 1); console.log(pm.response.json());")
        ed.textChanged.connect(self._edited)
        lay.addWidget(ed, 1)

        def insert(i):
            if i > 0:
                code = SNIPPETS[which][i - 1][1]
                if ed.toPlainText().strip():
                    ed.appendPlainText("\n" + code)
                else:
                    ed.setPlainText(code)
                snip.setCurrentIndex(0)
        snip.activated.connect(insert)
        setattr(self, f"script_{which}", ed)
        return w

    def _build_auth(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 14, 0, 0)
        lay.setSpacing(12)
        row = QHBoxLayout()
        row.addWidget(_label("Type", "muted"))
        self.auth_type = QComboBox()
        for key, title in AUTH_TYPES:
            self.auth_type.addItem(title, key)
        self.auth_type.currentIndexChanged.connect(self._auth_changed)
        row.addWidget(self.auth_type)
        row.addStretch(1)
        lay.addLayout(row)
        self.auth_stack = QStackedWidget()
        self.auth_widgets = {}          # (type, key) -> widget
        for key, _title in AUTH_TYPES:
            page = QWidget()
            pl = QVBoxLayout(page)
            pl.setContentsMargins(0, 0, 0, 0)
            pl.setSpacing(10)
            fields = AUTH_FORMS.get(key)
            if fields:
                form = QFormLayout()
                form.setContentsMargins(0, 0, 0, 0)
                form.setSpacing(10)
                form.setLabelAlignment(Qt.AlignLeft)
                for k, text, hint, kind in fields:
                    if isinstance(kind, list):
                        wdg = QComboBox()
                        for val, label in kind:
                            wdg.addItem(label, val)
                        wdg.currentIndexChanged.connect(self._edited)
                    else:
                        wdg = QLineEdit()
                        wdg.setPlaceholderText(hint)
                        if kind == "secret":
                            wdg.setEchoMode(QLineEdit.Password)
                        wdg.textEdited.connect(self._edited)
                    self.auth_widgets[(key, k)] = wdg
                    form.addRow(_label(text, "muted"), wdg)
                pl.addLayout(form)
            if key in AUTH_NOTES:
                note = QLabel(AUTH_NOTES[key])
                note.setObjectName("muted")
                note.setWordWrap(True)
                pl.addWidget(note)
            if key == "oauth2":
                brow = QHBoxLayout()
                self.token_btn = primary(QPushButton("Get new access token"))
                self.token_btn.clicked.connect(self._get_token)
                self.token_status = QLabel("")
                self.token_status.setWordWrap(True)
                brow.addWidget(self.token_btn)
                brow.addWidget(self.token_status, 1)
                pl.addLayout(brow)
            pl.addStretch(1)
            if len(fields or []) > 5:
                sc = QScrollArea()
                sc.setWidgetResizable(True)
                sc.setFrameShape(QFrame.NoFrame)
                sc.setWidget(page)
                self.auth_stack.addWidget(sc)
            else:
                self.auth_stack.addWidget(page)
        lay.addWidget(self.auth_stack, 1)
        return w

    def _build_body(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 10, 0, 0)
        lay.setSpacing(8)
        row = QHBoxLayout()
        row.setSpacing(14)
        self.mode_group = QButtonGroup(self)
        self.mode_buttons = {}
        for i, m in enumerate(store.BODY_MODES):
            rb = QRadioButton(m)
            rb.setCursor(Qt.PointingHandCursor)
            self.mode_group.addButton(rb, i)
            self.mode_buttons[m] = rb
            row.addWidget(rb)
        row.addStretch(1)
        self.raw_type = QComboBox()
        self.raw_type.addItems(store.RAW_TYPES)
        self.raw_type.currentTextChanged.connect(self._raw_type_changed)
        self.beautify = flat(QPushButton("Beautify"))
        self.beautify.clicked.connect(self._beautify)
        row.addWidget(self.beautify)
        row.addWidget(self.raw_type)
        lay.addLayout(row)
        self.mode_group.idToggled.connect(self._mode_changed)

        self.bstack = QStackedWidget()
        none = QLabel("This request has no body.")
        none.setObjectName("muted")
        none.setAlignment(Qt.AlignCenter)
        self.bstack.addWidget(none)                                      # none
        self.form = KVTable(with_type=True)
        self.form.changed.connect(self._edited)
        self.bstack.addWidget(self.form)                                 # form-data
        self.urlenc = KVEditor(KVTable(), "param")
        self.urlenc.changed.connect(self._edited)
        self.bstack.addWidget(self.urlenc)                               # urlencoded
        self.raw = CodeEditor()
        self.raw.textChanged.connect(self._edited)
        self.bstack.addWidget(self.raw)                                  # raw
        holder = QWidget()
        outer = QVBoxLayout(holder)
        outer.setContentsMargins(0, 8, 0, 0)
        bl = QHBoxLayout()
        self.bin_path = QLineEdit()
        self.bin_path.setPlaceholderText("Choose a file to send as the raw request body")
        self.bin_path.textEdited.connect(self._edited)
        pick = QPushButton("Select file")
        pick.clicked.connect(self._pick_binary)
        bl.addWidget(self.bin_path, 1)
        bl.addWidget(pick)
        outer.addLayout(bl)
        outer.addStretch(1)
        self.bstack.addWidget(holder)                                    # binary
        gq = QWidget()
        gl = QVBoxLayout(gq)
        gl.setContentsMargins(0, 0, 0, 0)
        self.gql_query = CodeEditor()
        self.gql_query.setPlaceholderText("query { viewer { id } }")
        self.gql_vars = CodeEditor()
        self.gql_vars.set_language("json")
        self.gql_vars.setPlaceholderText('Variables (JSON), e.g. {"id": 1}')
        self.gql_query.textChanged.connect(self._edited)
        self.gql_vars.textChanged.connect(self._edited)
        gl.addWidget(self.gql_query, 3)
        gl.addWidget(self.gql_vars, 1)
        self.bstack.addWidget(gq)                                        # graphql
        lay.addWidget(self.bstack, 1)
        return w

    def _build_response_side(self):
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(18, 0, 18, 12)
        lay.setSpacing(0)
        self.pstack = QStackedWidget()

        # body page: empty / text / preview
        self.body_stack = QStackedWidget()
        empty = QWidget()
        el = QVBoxLayout(empty)
        el.setAlignment(Qt.AlignCenter)
        self.empty_icon = QLabel()
        self.empty_icon.setPixmap(theme.icon("bolt", theme.FAINT, 28).pixmap(28, 28))
        self.empty_icon.setAlignment(Qt.AlignCenter)
        self.empty_title = QLabel("Hit Send to get a response")
        self.empty_title.setAlignment(Qt.AlignCenter)
        self.empty_title.setStyleSheet("font-weight: 600;")
        self.empty_sub = QLabel("Ctrl + Enter also sends")
        self.empty_sub.setObjectName("muted")
        self.empty_sub.setAlignment(Qt.AlignCenter)
        self.empty_sub.setWordWrap(True)
        for x in (self.empty_icon, self.empty_title, self.empty_sub):
            el.addWidget(x)
        self.body_stack.addWidget(empty)                                 # 0
        self.view = CodeEditor(read_only=True)
        self.view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._body_menu)
        self.body_stack.addWidget(self.view)                             # 1
        self.html = QTextBrowser()
        self.html.setObjectName("code")
        self.html.setOpenLinks(False)
        self.body_stack.addWidget(self.html)                             # 2
        self.image = QLabel()
        self.image.setAlignment(Qt.AlignCenter)
        self.body_stack.addWidget(self.image)                            # 3
        self.pstack.addWidget(self.body_stack)

        self.resp_headers = self._table(["Name", "Value"])
        self.pstack.addWidget(self.resp_headers)
        self.resp_cookies = self._table(["Name", "Value", "Domain", "Path"])
        self.pstack.addWidget(self.resp_cookies)
        self.test_table = self._table(["Result", "Test", "Details"])
        self.test_table.setColumnWidth(0, 90)
        self.test_table.setColumnWidth(1, 320)
        self.pstack.addWidget(self.test_table)

        self.ptabs = _TabStrip(["Body", "Headers", "Cookies", "Test Results"], self.pstack)
        self.status_pill = QLabel()
        self.status_pill.hide()
        self.time_lb = QLabel()
        self.time_lb.setObjectName("muted")
        self.size_lb = QLabel()
        self.size_lb.setObjectName("muted")
        for wdg in (self.status_pill, self.time_lb, self.size_lb):
            self.ptabs.extra.addWidget(wdg)
        lay.addWidget(self.ptabs)
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background: {theme.BORDER};")
        lay.addWidget(line)

        tools = QHBoxLayout()
        tools.setContentsMargins(0, 8, 0, 8)
        self.view_group = QButtonGroup(self)
        self.view_btns = {}
        for i, name in enumerate(("Pretty", "Raw", "Preview")):
            b = QPushButton(name)
            b.setCheckable(True)
            b.setChecked(i == 0)
            b.setStyleSheet(
                f"QPushButton {{ padding: 3px 10px; border-radius: 5px; background: transparent; border: none; color: {theme.MUTED}; }}"
                f"QPushButton:checked {{ background: {theme.BORDER2}; color: {theme.TEXT}; }}"
                f"QPushButton:hover {{ color: {theme.TEXT}; }}")
            self.view_group.addButton(b, i)
            self.view_btns[name] = b
            tools.addWidget(b)
        self.view_group.idClicked.connect(lambda _: self._render_body())
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search response")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(200)
        self.search.returnPressed.connect(self._find_next)
        self.search.textChanged.connect(lambda *_: self._find_next(restart=True))
        tools.addSpacing(8)
        tools.addWidget(self.search)
        tools.addStretch(1)
        self.example_btn = QPushButton("Save example")
        self.example_btn.setToolTip("Keep this response with the request; the mock server can serve it")
        self.example_btn.clicked.connect(self.save_example)
        self.example_btn.setEnabled(False)
        tools.addWidget(self.example_btn)
        self.wrap_cb = QCheckBox("Wrap")
        self.wrap_cb.setToolTip("Wrap long lines in the response")
        self.wrap_cb.toggled.connect(lambda on: self.view.setLineWrapMode(
            QPlainTextEdit.WidgetWidth if on else QPlainTextEdit.NoWrap))
        tools.addWidget(self.wrap_cb)
        self.copy_btn = QPushButton("  Copy")
        self.copy_btn.setIcon(theme.icon("copy", theme.TEXT, 14))
        self.copy_btn.setToolTip("Copy the response body (Ctrl+Shift+C copies the request as cURL)")
        self.copy_btn.clicked.connect(lambda: self.copy_what("body"))
        self.copy_more = QToolButton()
        self.copy_more.setIcon(theme.icon("chevron-down", theme.MUTED, 14))
        self.copy_more.setToolTip("More ways to copy or save")
        self.copy_more.setPopupMode(QToolButton.InstantPopup)
        self.copy_more.setMenu(self._build_copy_menu())
        tools.addWidget(self.copy_btn)
        tools.addWidget(self.copy_more)
        self.tools_widget = QWidget()
        self.tools_widget.setLayout(tools)
        lay.addWidget(self.tools_widget)
        lay.addWidget(self.pstack, 1)
        self.ptabs.bar.currentChanged.connect(lambda i: self.tools_widget.setVisible(i == 0))
        return box

    def _build_copy_menu(self):
        m = QMenu(self)
        for label, what in (("Response body", "body"), ("Response headers", "headers"), ("Status line", "status"),
                            ("Whole response (status, headers, body)", "full")):
            m.addAction(label, lambda w=what: self.copy_what(w))
        m.addSeparator()
        m.addAction("Request as cURL", lambda: self.copy_what("curl"))
        m.addAction("Request URL (with params)", lambda: self.copy_what("url"))
        m.addSeparator()
        m.addAction("Save body to file…", self.save_body)
        return m

    def copy_text_for(self, what):
        """The text a copy action puts on the clipboard (None when there is nothing to copy yet)."""
        r = self.response
        if what in ("body", "headers", "status", "full") and (r is None or not r.ok):
            return None
        if what == "body":
            return r.text()
        if what == "headers":
            return r.headers_text()
        if what == "status":
            return r.status_line()
        if what == "full":
            return r.raw_text()
        values = self.values_provider(self.collection_id)
        if what == "curl":
            return curl.generate(self.collect(), values, "cURL")
        if what == "url":
            return http_client.build_url(self.collect(), values)
        return None

    def copy_what(self, what):
        text = self.copy_text_for(what)
        names = {"body": "response body", "headers": "response headers", "status": "status line",
                 "full": "whole response", "curl": "request as cURL", "url": "request URL"}
        if text is None:
            self._say("Nothing to copy yet: send the request first.", ok=False)
            return False
        copy_text(text, self.copy_btn)
        self._say(f"Copied {names[what]} ({len(text):,} characters)")
        return True

    def save_body(self):
        r = self.response
        if r is None or not r.ok or not r.body:
            self._say("There is no response body to save yet.", ok=False)
            return
        import mimetypes
        ext = {"application/json": ".json", "text/plain": ".txt", "text/html": ".html", "text/csv": ".csv",
               "application/xml": ".xml", "text/xml": ".xml"}.get(r.content_type) or \
            mimetypes.guess_extension(r.content_type or "") or ".bin"
        name = (urllib.parse.urlsplit(r.url).path.rstrip("/").split("/")[-1] or "response").split(".")[0] + ext
        path, _ = QFileDialog.getSaveFileName(self, "Save response body", name)
        if path:
            try:
                with open(path, "wb") as f:
                    f.write(r.body)
            except OSError as e:
                self._say(f"Could not save: {e}", ok=False)
                return
            self._say(f"Saved {len(r.body):,} bytes to {path}")

    def _say(self, text, ok=True):
        """A short confirmation under the URL bar that clears itself."""
        self.warn.setStyleSheet(f"color: {theme.GREEN if ok else theme.ACCENT}; font-size: 8pt;")
        self.warn.setText(text)
        self.warn.show()
        QTimer.singleShot(2500, lambda: self._unsay(text))

    def _unsay(self, text):
        if isValid(self) and self.warn.text() == text:
            self.warn.hide()
            self._check_variables()

    def _body_menu(self, pos):
        m = QMenu(self)
        if self.view.textCursor().hasSelection():
            m.addAction("Copy selection", self.view.copy)
        m.addAction("Copy all", lambda: self.copy_what("body"))
        m.addAction("Select all", self.view.selectAll)
        m.addSeparator()
        m.addAction("Copy whole response", lambda: self.copy_what("full"))
        m.addAction("Copy request as cURL", lambda: self.copy_what("curl"))
        m.addAction("Save body to file…", self.save_body)
        m.exec(self.view.viewport().mapToGlobal(pos))

    def _table(self, headers):
        t = CopyTable(0, len(headers))
        t.copied.connect(self._say)
        t.setHorizontalHeaderLabels(headers)
        t.verticalHeader().setVisible(False)
        t.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        t.horizontalHeader().setStretchLastSection(True)
        t.setEditTriggers(QTableWidget.NoEditTriggers)
        t.setShowGrid(False)
        t.setSelectionBehavior(QTableWidget.SelectRows)
        t.setColumnWidth(0, 220)
        return t

    # ---- load / collect --------------------------------------------------------
    def load(self, req):
        self._loading = True
        self.req = store.normalize_request(req)
        r = self.req
        self.method.setCurrentText(r["method"])
        self.url.setText(r["url"])
        self._url_len = len(r["url"])
        self.params.set_rows(r["params"])
        self.headers.set_rows(r["headers"])
        a = {**store.AUTH_DEFAULTS, **r["auth"]}
        self.auth_type.setCurrentIndex(max(0, self.auth_type.findData(a["type"])))
        for (atype, key), wdg in self.auth_widgets.items():
            if isinstance(wdg, QComboBox):
                wdg.setCurrentIndex(max(0, wdg.findData(a.get(key, ""))))
            else:
                wdg.setText(a.get(key, ""))
        self.auth_stack.setCurrentIndex(self.auth_type.currentIndex())
        b = r["body"]
        self.form.set_rows(b["form"])
        self.urlenc.set_rows(b["urlencoded"])
        self.raw_type.setCurrentText(b["raw_type"])
        self.raw.setPlainText(b["raw"])
        self.bin_path.setText(b["file"])
        self.gql_query.setPlainText(b["query"])
        self.gql_vars.setPlainText(b["variables"])
        self.mode_buttons.get(b["mode"], self.mode_buttons["none"]).setChecked(True)
        self.script_pre.setPlainText(r["scripts"]["pre"])
        self.script_test.setPlainText(r["scripts"]["test"])
        self._apply_mode(b["mode"])
        self._refresh_titles()
        self._style_method()
        self._loading = False

    def collect(self):
        r = copy.deepcopy(self.req)
        r["method"] = self.method.currentText()
        r["url"] = self.url.text()
        r["params"] = self.params.rows()
        r["headers"] = self.headers.rows()
        auth = {**store.AUTH_DEFAULTS, **self.req.get("auth", {})}
        auth["type"] = self.auth_type.currentData()
        for (atype, key), wdg in self.auth_widgets.items():
            if atype == auth["type"] or key not in AUTH_SHARED:
                auth[key] = wdg.currentData() if isinstance(wdg, QComboBox) else wdg.text()
        r["auth"] = auth
        mode = self.current_mode()
        r["body"] = {"mode": mode, "raw_type": self.raw_type.currentText(), "raw": self.raw.toPlainText(),
                     "form": [x for x in self.form.rows()], "urlencoded": self.urlenc.rows(),
                     "file": self.bin_path.text(), "query": self.gql_query.toPlainText(),
                     "variables": self.gql_vars.toPlainText()}
        r["scripts"] = {"pre": self.script_pre.toPlainText(), "test": self.script_test.toPlainText()}
        self.req = r
        return r

    def current_mode(self):
        i = self.mode_group.checkedId()
        return store.BODY_MODES[i] if i >= 0 else "none"

    def primary_action(self):
        self.sendRequested.emit(self)

    # ---- state ------------------------------------------------------------------
    def mark_saved(self):
        self._saved = json.dumps(self._comparable(), sort_keys=True)
        self.update_crumb()

    def _comparable(self):
        return copy.deepcopy(self.collect())

    def is_dirty(self):
        return json.dumps(self._comparable(), sort_keys=True) != self._saved

    def title(self):
        return self.req.get("name") or "Untitled request"

    def update_crumb(self):
        dirty = self.is_dirty() if self._saved is not None else False
        head = (f"{self.collection_name} / " if self.collection_name else "Unsaved / ")
        folder = self.req.get("folder", "")
        if folder and self.collection_name:
            head += folder.replace("/", " / ") + " / "
        dot = f" <span style='color:{theme.ACCENT}'>•</span>" if dirty else ""
        self.crumb.setText(f"{head}<b style='color:{theme.TEXT}'>{_esc(self.title())}</b>{dot}")

    def rename(self):
        name, ok = QInputDialog.getText(self, "Rename request", "Name", text=self.title())
        if ok and name.strip():
            self.req["name"] = name.strip()
            self._edited()

    def set_collection(self, cid, name):
        self.collection_id, self.collection_name = cid, name
        self.update_crumb()

    # ---- editing events -----------------------------------------------------------
    def _edited(self, *_):
        if self._loading:
            return
        self._refresh_titles()
        self._check_variables()
        self.update_crumb()
        self.changed.emit(self)

    def _method_changed(self, *_):
        self._style_method()
        self._edited()

    def _style_method(self):
        c = theme.method_color(self.method.currentText())
        # colour only the box itself; the dropdown list keeps normal text and colours each verb by itself
        self.method.setStyleSheet(f"QComboBox#methodBox {{ color: {c}; }} "
                                  f"QComboBox#methodBox QAbstractItemView {{ color: {theme.TEXT}; background: {theme.PANEL2}; "
                                  f"border: 1px solid {theme.BORDER2}; selection-background-color: {theme.SELECTED}; "
                                  f"selection-color: {theme.TEXT}; padding: 3px; outline: 0; }} "
                                  f"QComboBox#methodBox QAbstractItemView::item {{ padding: 6px 10px; min-height: 20px; "
                                  f"border-radius: 4px; }} "
                                  f"QComboBox#methodBox QAbstractItemView::item:selected {{ background: {theme.SELECTED}; }} "
                                  f"QComboBox#methodBox QAbstractItemView::item:hover {{ background: {theme.MENU_SEL}; }}")
        for i in range(self.method.count()):
            self.method.setItemData(i, QColor(theme.method_color(self.method.itemText(i))), Qt.ForegroundRole)

    def _params_changed(self):
        if self._loading:
            return
        base = self.url.text().split("?", 1)[0]
        self._loading = True
        self.url.setText(base)
        self._loading = False
        self._edited()

    def _headers_changed(self):
        self._edited()

    def _url_typed(self, text):
        """A big jump in length means something was pasted: a cURL command, fetch() call or API file is imported."""
        jumped = len(text) - self._url_len > 12
        self._url_len = len(text)
        if jumped and curl.looks_like_import(text):
            self.pasteImport.emit(self, text)
            return
        self._edited()

    def _url_enter(self):
        if curl.looks_like_import(self.url.text()):
            self.pasteImport.emit(self, self.url.text())

    def show_import_error(self, message):
        self.warn.setStyleSheet(f"color: {theme.RED}; font-size: 8pt;")
        self.warn.setText(message)
        self.warn.show()

    def _split_query_from_url(self):
        text = self.url.text()
        if "?" not in text or "{{" in text.split("?", 1)[1]:
            return
        base, _, query = text.partition("?")
        query, _, frag = query.partition("#")
        rows = self.params.rows() + [store.kv(k, v) for k, v in urllib.parse.parse_qsl(query, keep_blank_values=True)]
        self._loading = True
        self.url.setText(base + ("#" + frag if frag else ""))
        self.params.set_rows(rows)
        self._loading = False
        self._edited()

    def _auth_changed(self, i):
        self.auth_stack.setCurrentIndex(i)
        self._edited()

    def _mode_changed(self, _id, checked):
        if checked and not self._loading:
            self._apply_mode(self.current_mode())
            self._edited()

    def _apply_mode(self, mode):
        idx = {"none": 0, "form-data": 1, "x-www-form-urlencoded": 2, "raw": 3, "binary": 4, "GraphQL": 5}[mode]
        self.bstack.setCurrentIndex(idx)
        is_raw = mode == "raw"
        self.raw_type.setVisible(is_raw)
        self.beautify.setVisible(is_raw)
        self._apply_raw_language()

    def _apply_raw_language(self):
        self.raw.set_language(self.raw_type.currentText())

    def _raw_type_changed(self, *_):
        self._apply_raw_language()
        self._edited()

    def _beautify(self):
        text = self.raw.toPlainText()
        kind = self.raw_type.currentText()
        out = pretty_json(text) if kind == "JSON" else pretty_xml(text) if kind in ("XML", "SOAP 1.1", "SOAP 1.2") else None
        if out is None:
            self.warn.setStyleSheet(f"color: {theme.ACCENT}; font-size: 8pt;")
            self.warn.setText("Beautify failed: the body is not valid " + kind + ".")
            self.warn.show()
            return
        self.raw.setPlainText(out)
        self._check_variables()

    def _pick_binary(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select file")
        if path:
            self.bin_path.setText(path)
            self._edited()

    def _refresh_titles(self):
        n = len([h for h in self.headers.rows() if h["enabled"]])
        self.rtabs.set_title(2, f"Headers ({n})" if n else "Headers")
        n = len([h for h in self.params.rows() if h["enabled"]])
        self.rtabs.set_title(0, f"Params ({n})" if n else "Params")
        self.rtabs.set_title(1, "Authorization" if self.auth_type.currentData() == "none" else "Authorization •")
        self.rtabs.set_title(4, "Pre-request Script" + (" •" if self.script_pre.toPlainText().strip() else ""))
        self.rtabs.set_title(5, "Tests" + (" •" if self.script_test.toPlainText().strip() else ""))

    def _check_variables(self):
        values = self.values_provider(self.collection_id)
        r = self.req if self._loading else self.collect()
        try:
            resolved = http_client.build_url(r, values)
        except (ValueError, KeyError):
            resolved = ""
        self.url.setToolTip(f"Sends to: {resolved}" if resolved and resolved != r["url"] else "")
        texts = [r["url"]] + [x["value"] for x in r["headers"] if x["enabled"]] + [r["body"]["raw"]]
        texts += [r["auth"].get("token", "")]
        missing = []
        for t in texts:
            for n in variables.unresolved(t, values):
                if n not in missing:
                    missing.append(n)
        if missing:
            self._advise("Unresolved variable" + ("s" if len(missing) > 1 else "") + ": " + ", ".join(missing) +
                         ". Select an environment or define them under Variables.")
            return
        self._warn_dismissed = None
        self.warn.hide()

    WARN_SECONDS = 20

    def _advise(self, text):
        """A persistent hint under the URL bar that hides itself after WARN_SECONDS and stays hidden until it changes."""
        if text == getattr(self, "_warn_dismissed", None):
            self.warn.hide()
            return
        self.warn.setStyleSheet(f"color: {theme.ACCENT}; font-size: 8pt;")
        if self.warn.text() != text or not self.warn.isVisible():
            self.warn.setText(text)
            QTimer.singleShot(self.WARN_SECONDS * 1000, lambda: self._expire_advice(text))
        self.warn.show()

    def _expire_advice(self, text):
        if isValid(self) and self.warn.text() == text:
            self._warn_dismissed = text
            self.warn.hide()

    def refresh_variables(self):
        self._check_variables()

    def focus_url(self):
        self.url.setFocus()

    # ---- OAuth 2.0 ---------------------------------------------------------------------
    def _get_token(self):
        if self._token_worker is not None and self._token_worker.isRunning():
            return
        a = self.collect()["auth"]
        values = self.values_provider(self.collection_id)
        a = {k: variables.substitute(str(v), values) for k, v in a.items()}
        grant = a.get("o2_grant")
        self.token_btn.setEnabled(False)
        self.token_status.setStyleSheet(f"color: {theme.MUTED};")
        self.token_status.setText("Waiting for the browser…" if grant == oauth2.GRANTS[2] else "Requesting token…")

        def work():
            if grant == oauth2.GRANTS[2]:
                return oauth2.authorization_code_flow(a)
            return oauth2.request_token(a)
        self._token_worker = Worker(work, self)
        self._token_worker.done.connect(self._token_done)
        self._token_worker.start()

    def _token_done(self, result, error):
        self.token_btn.setEnabled(True)
        if error:
            self.token_status.setStyleSheet(f"color: {theme.RED};")
            self.token_status.setText(error)
            return
        self.auth_widgets[("oauth2", "token")].setText(result["access_token"])
        if result.get("refresh_token"):
            self.auth_widgets[("oauth2", "o2_refresh_token")].setText(result["refresh_token"])
        exp = result.get("expires_in")
        self.token_status.setStyleSheet(f"color: {theme.GREEN};")
        self.token_status.setText("Token received" + (f" (expires in {exp} s)" if exp else "") + ".")
        self._edited()

    # ---- response -------------------------------------------------------------------
    def set_busy(self, busy):
        self.send_btn.setEnabled(not busy)
        self.send_btn.setText("  Sending…" if busy else "  Send")
        if busy:
            self.status_pill.hide()
            self.time_lb.setText("")
            self.size_lb.setText("")
            self.empty_title.setText("Sending request…")
            self.empty_sub.setText("")
            self.body_stack.setCurrentIndex(0)

    def set_response(self, resp, exec_result=None):
        self.response = resp
        self.exec_result = exec_result
        self.send_btn.setEnabled(True)
        self.send_btn.setText("  Send")
        self.example_btn.setEnabled(bool(resp.ok))
        self._fill_tests(exec_result)
        if not resp.ok:
            self.status_pill.setText("Error")
            self._pill(theme.RED)
            self.time_lb.setText("")
            self.size_lb.setText("")
            self.empty_title.setText("Could not get any response")
            self.empty_sub.setText(resp.error)
            self.body_stack.setCurrentIndex(0)
            self.resp_headers.setRowCount(0)
            self.resp_cookies.setRowCount(0)
            self.ptabs.set_title(1, "Headers")
            return
        self.status_pill.setText(status_text(resp))
        self._pill(theme.status_color(resp.status))
        self.time_lb.setText(f"{resp.elapsed_ms} ms")
        self.size_lb.setText(human_size(resp.size))
        self.resp_headers.setRowCount(len(resp.headers))
        for i, (k, v) in enumerate(resp.headers):
            self.resp_headers.setItem(i, 0, QTableWidgetItem(k))
            self.resp_headers.setItem(i, 1, QTableWidgetItem(v))
        self.ptabs.set_title(1, f"Headers ({len(resp.headers)})")
        self.resp_cookies.setRowCount(len(resp.cookies))
        for i, c in enumerate(resp.cookies):
            for j, key in enumerate(("name", "value", "domain", "path")):
                self.resp_cookies.setItem(i, j, QTableWidgetItem(c[key]))
        self.ptabs.set_title(2, f"Cookies ({len(resp.cookies)})" if resp.cookies else "Cookies")
        self.view_btns["Pretty"].setChecked(True)
        self._render_body()

    def _fill_tests(self, ex):
        tests = list(ex.tests) if ex else []
        errors = list(ex.script_errors) if ex else []
        rows = tests + [{"name": "Script error", "passed": False, "error": e} for e in errors]
        self.test_table.setRowCount(len(rows))
        for i, t in enumerate(rows):
            res = QTableWidgetItem("PASS" if t["passed"] else "FAIL")
            res.setForeground(QColor(theme.GREEN if t["passed"] else theme.RED))
            self.test_table.setItem(i, 0, res)
            self.test_table.setItem(i, 1, QTableWidgetItem(t["name"]))
            self.test_table.setItem(i, 2, QTableWidgetItem(t.get("error", "")))
        passed = sum(1 for t in rows if t["passed"])
        self.ptabs.set_title(3, f"Test Results ({passed}/{len(rows)})" if rows else "Test Results")

    def _pill(self, color):
        self.status_pill.setStyleSheet(
            f"color: {color}; background: {_alpha(color, 0.14)}; border: 1px solid {_alpha(color, 0.45)};"
            "border-radius: 5px; padding: 2px 9px; font-weight: 600;")
        self.status_pill.show()

    def _render_body(self):
        resp = self.response
        if resp is None or not resp.ok:
            return
        mode = ["Pretty", "Raw", "Preview"][max(0, self.view_group.checkedId())]
        ct = resp.content_type
        if not resp.body:
            self.empty_title.setText("This response has no body")
            self.empty_sub.setText("")
            self.body_stack.setCurrentIndex(0)
            return
        if mode == "Preview":
            if ct.startswith("image/"):
                pm = QPixmap()
                if pm.loadFromData(resp.body):
                    self.image.setPixmap(pm.scaled(self.image.size() * 0.95, Qt.KeepAspectRatio,
                                                    Qt.SmoothTransformation) if pm.width() > 900 else pm)
                    self.body_stack.setCurrentIndex(3)
                    return
            if "html" in ct:
                self.html.setHtml(resp.text())
                self.body_stack.setCurrentIndex(2)
                return
        if ct.startswith("image/"):
            self.view.setPlainText(f"[{ct}, {human_size(resp.size)}] - choose Preview to see the image.")
            self.view.set_language("none")
            self.body_stack.setCurrentIndex(1)
            return
        text = resp.text()
        lang = "json" if "json" in ct else "xml" if "xml" in ct else "html" if "html" in ct else "text"
        if lang == "text" and text.lstrip()[:1] in ("{", "[") and pretty_json(text) is not None:
            lang = "json"
        if mode == "Pretty" and len(resp.body) <= PRETTY_LIMIT:
            out = pretty_json(text) if lang == "json" else pretty_xml(text) if lang == "xml" else None
            if out is not None:
                text = out
        self.view.set_language(lang if len(resp.body) <= PRETTY_LIMIT else "none")
        self.view.setPlainText(text)
        self.body_stack.setCurrentIndex(1)
        if self.search.text():
            self._find_next(restart=True)

    def _find_next(self, restart=False):
        needle = self.search.text()
        if self.body_stack.currentIndex() != 1 or not needle:
            return
        if restart:
            cur = self.view.textCursor()
            cur.setPosition(0)
            self.view.setTextCursor(cur)
        if not self.view.find(needle):
            cur = self.view.textCursor()
            cur.setPosition(0)
            self.view.setTextCursor(cur)
            self.view.find(needle)

    def save_example(self):
        r = self.response
        if r is None or not r.ok:
            return
        name = f"{r.status} {r.reason}".strip()
        skip = ("content-length", "transfer-encoding", "connection", "content-encoding", "date", "server")
        self.req.setdefault("examples", []).append({
            "name": name, "status": r.status, "headers": [list(h) for h in r.headers if h[0].lower() not in skip],
            "body": r.text()})
        self._edited()
        self.warn.setStyleSheet(f"color: {theme.GREEN}; font-size: 8pt;")
        self.warn.setText(f"Saved “{name}” as an example. Save the request to keep it.")
        self.warn.show()



def _esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _alpha(hex_color, a):
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{a})"
