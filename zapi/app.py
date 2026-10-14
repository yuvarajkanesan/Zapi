"""Main window: header, sidebar, request tabs, environments page, console and status bar."""
import copy
import json
import time

from PySide6.QtCore import QSize, Qt, QTimer
from shiboken6 import isValid
from PySide6.QtGui import QColor, QIcon, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
                               QMenu, QMessageBox, QPlainTextEdit, QPushButton, QSizePolicy, QSplitter, QStackedWidget, QTabBar,
                               QToolButton, QVBoxLayout, QWidget)

from . import APP_NAME, VERSION, curl, importers, mock, scripting, store, theme, variable_assist, variables, vault
from .dialogs import (CodeDialog, CookieDialog, CurlDialog, HelpDialog, ImportDialog, MockBridge, MockDialog,
                      PassphraseDialog, RunnerDialog, SaveRequestDialog, SendThread, SettingsDialog)
from .env_view import EnvironmentsPage
from .proto_views import GrpcView, StreamView
from .request_view import RequestView, human_size, status_text
from .sidebar import Sidebar
from .widgets import primary


dot_icon = theme.dot_icon


class WelcomePage(QWidget):
    """Shown when no request tab is open."""

    def __init__(self, window):
        super().__init__()
        self.window_ = window
        outer = QVBoxLayout(self)
        outer.setAlignment(Qt.AlignCenter)
        col = QWidget()
        col.setFixedWidth(600)
        col.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Maximum)
        lay = QVBoxLayout(col)
        lay.setSpacing(14)
        logo = QLabel()
        logo.setPixmap(theme.logo_pixmap(48))
        lay.addWidget(logo)
        title = QLabel("Send your first request")
        title.setObjectName("title")
        lay.addWidget(title)
        sub = QLabel("Paste a URL, pick a method and hit Send. Everything stays on this computer, and no account is needed.")
        sub.setObjectName("muted")
        sub.setWordWrap(True)
        lay.addWidget(sub)

        bar = QFrame()
        bar.setStyleSheet(f"QFrame#qbar {{ background: {theme.PANEL}; border: 1px solid {theme.BORDER}; border-radius: 8px; }}"
                          "QFrame#qbar QWidget { background: transparent; }")
        bar.setObjectName("qbar")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(1, 1, 1, 1)
        bl.setSpacing(0)
        self.method = QComboBox()
        self.method.setObjectName("methodBox")
        self.method.addItems(store.METHODS)
        self.method.setFixedHeight(42)
        self.method.setStyleSheet(f"color: {theme.GREEN};")
        self.method.currentTextChanged.connect(
            lambda m: self.method.setStyleSheet(f"color: {theme.method_color(m)};"))
        self.url = QLineEdit()
        self.url.setObjectName("urlEdit")
        self.url.setPlaceholderText("https://api.example.com/v1/users")
        self.url.setFixedHeight(42)
        self._url_len = 0
        self.url.textEdited.connect(self._typed)
        self.url.returnPressed.connect(self.send)
        go = primary(QPushButton("Send"), "border-top-left-radius: 0; border-bottom-left-radius: 0;")
        go.setFixedSize(92, 42)
        go.clicked.connect(self.send)
        bl.addWidget(self.method)
        bl.addWidget(self.url, 1)
        bl.addWidget(go)
        lay.addWidget(bar)
        self.err = QLabel()
        self.err.setWordWrap(True)
        self.err.setStyleSheet(f"color: {theme.RED}; font-size: 8pt;")
        self.err.hide()
        lay.addWidget(self.err)

        cards = QHBoxLayout()
        cards.setSpacing(12)
        for icon, head, text, cb in (
                ("terminal", "Paste cURL", "Turn a copied cURL command into a ready-to-send request.", window.paste_curl),
                ("download", "Import a file", "Bring in a Postman collection or an OpenAPI spec.", window.import_file),
                ("plus", "Blank collection", "Start organised: group requests, variables and auth in one place.",
                 window.new_collection)):
            cards.addWidget(self._card(icon, head, text, cb))
        lay.addSpacing(8)
        lay.addLayout(cards)
        hint = QLabel(f"<span style='color:{theme.MUTED}'><b>Ctrl</b> + <b>N</b> new request &nbsp;&nbsp; "
                      "<b>Ctrl</b> + <b>Enter</b> send &nbsp;&nbsp; <b>Ctrl</b> + <b>S</b> save</span>")
        hint.setTextFormat(Qt.RichText)
        lay.addSpacing(6)
        lay.addWidget(hint)
        outer.addWidget(col, 0, Qt.AlignCenter)

    def _card(self, icon, head, text, cb):
        card = QWidget()
        card.setObjectName("card")
        card.setCursor(Qt.PointingHandCursor)
        card.setFixedHeight(124)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(16, 14, 16, 14)
        cl.setSpacing(6)
        ic = QLabel()
        ic.setPixmap(theme.icon(icon, theme.ACCENT, 20).pixmap(20, 20))
        h = QLabel(head)
        h.setStyleSheet("font-weight: 600;")
        t = QLabel(text)
        t.setObjectName("muted")
        t.setWordWrap(True)
        cl.addWidget(ic)
        cl.addWidget(h)
        cl.addWidget(t)
        cl.addStretch(1)
        card.mousePressEvent = lambda e: cb()
        return card

    def _typed(self, text):
        """Pasting a cURL command, fetch() call or API file here imports it instead of treating it as a URL."""
        jumped = len(text) - self._url_len > 12
        self._url_len = len(text)
        self.err.hide()
        if jumped and curl.looks_like_import(text):
            self._import(text)

    def _import(self, text):
        err = self.window_.import_pasted(text)
        if err:
            self.err.setText(err)
            self.err.show()
        else:
            self.url.clear()
            self._url_len = 0

    def send(self):
        url = self.url.text().strip()
        if curl.looks_like_import(url):
            self._import(url)
            return
        if url:
            self.window_.quick_send(self.method.currentText(), url)
            self.url.clear()
        else:
            self.window_.new_request("")
            self.url.clear()


class MainWindow(QMainWindow):
    def __init__(self, ws):
        super().__init__()
        self.ws = ws
        self.threads = {}
        from . import http_client
        self.jar = http_client.new_cookie_jar()
        self.mock_bridge = MockBridge()
        self.mock_server = mock.MockServer(ws.settings.get("mock", {}).get("routes", []),
                                           ws.settings.get("mock", {}).get("cors", True),
                                           on_request=self.mock_bridge.request.emit)
        self.mock_bridge.request.connect(lambda _e: self._sync_mock_label())
        self.setAttribute(Qt.WA_DeleteOnClose, True)     # a replaced window (theme switch) must not linger
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(theme.app_icon())
        self.resize(1280, 800)
        self.setMinimumSize(900, 560)
        self._build()
        variable_assist.set_provider(lambda: self.ws.variable_catalog(self._current_collection_id()))
        self._wire_shortcuts()
        self.refresh_environment_box()
        self._restore_tabs()
        self._update_empty_state()
        self._init_network_indicator()
        if ws.secret_failures:
            QTimer.singleShot(400, self._warn_unreadable_secrets)

    # ---- construction ---------------------------------------------------------------
    def _build(self):
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())

        self.pages = QStackedWidget()
        root.addWidget(self.pages, 1)
        root.addWidget(self._build_statusbar())
        self.setCentralWidget(central)

        self.main_page = QSplitter(Qt.Horizontal)
        self.main_page.setChildrenCollapsible(False)
        self.sidebar = Sidebar(self.ws)
        self.sidebar.openRequest.connect(self.open_saved_request)
        self.sidebar.openHistory.connect(self.open_history)
        self.sidebar.newRequest.connect(self.new_request)
        self.sidebar.runCollection.connect(self.open_runner)
        self.sidebar.exportRequested.connect(self.export_what)
        self.sidebar.copied.connect(self._flash)
        self.sidebar.newCollection.connect(self.new_collection)
        self.sidebar.pasteCurl.connect(self.paste_curl)
        self.sidebar.importRequested.connect(self.import_file)
        self.sidebar.manageEnvironments.connect(self.show_environments)
        self.sidebar.workspaceChanged.connect(self._workspace_changed)
        self.main_page.addWidget(self.sidebar)

        right = QSplitter(Qt.Vertical)
        right.setChildrenCollapsible(False)
        area = QWidget()
        al = QVBoxLayout(area)
        al.setContentsMargins(0, 0, 0, 0)
        al.setSpacing(0)
        self.area_stack = QStackedWidget()
        self.welcome = WelcomePage(self)
        self.area_stack.addWidget(self.welcome)
        self.tab_area = QWidget()
        tl = QVBoxLayout(self.tab_area)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(0)
        bar_row = QWidget()
        bar_row.setStyleSheet(f"background: {theme.PANEL}; border-bottom: 1px solid {theme.BORDER};")
        br = QHBoxLayout(bar_row)
        br.setContentsMargins(0, 0, 0, 0)
        br.setSpacing(0)
        self.tab_bar = QTabBar()
        self.tab_bar.setObjectName("reqTabs")
        self.tab_bar.setTabsClosable(True)
        self.tab_bar.setMovable(True)
        self.tab_bar.setDrawBase(False)
        self.tab_bar.setExpanding(False)
        self.tab_bar.setUsesScrollButtons(True)
        self.tab_bar.tabCloseRequested.connect(self.close_tab)
        self.tab_bar.currentChanged.connect(self._tab_changed)
        self.tab_bar.tabMoved.connect(self._tab_moved)
        self.tab_bar.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tab_bar.customContextMenuRequested.connect(self._tab_menu)
        plus = QToolButton()
        plus.setIcon(theme.icon("plus", theme.MUTED, 16))
        plus.setToolTip("New request (Ctrl+N)")
        plus.setFixedSize(38, 38)
        plus.clicked.connect(lambda: self.new_request(""))
        br.addWidget(self.tab_bar)
        br.addWidget(plus)
        br.addStretch(1)
        tl.addWidget(bar_row)
        self.view_stack = QStackedWidget()
        tl.addWidget(self.view_stack, 1)
        self.area_stack.addWidget(self.tab_area)
        al.addWidget(self.area_stack, 1)
        right.addWidget(area)

        self.console = QPlainTextEdit()
        self.console.setObjectName("code")
        self.console.setReadOnly(True)
        self.console.setFont(theme.mono(9))
        self.console.setMaximumBlockCount(1000)
        self.console.setPlaceholderText("Requests you send are logged here.")
        self.console.setContextMenuPolicy(Qt.CustomContextMenu)
        self.console.customContextMenuRequested.connect(self._console_menu)
        self.console.hide()
        right.addWidget(self.console)
        right.setSizes([620, 140])
        self.right_split = right
        self.main_page.addWidget(right)
        self.main_page.setSizes([270, 1010])
        self.main_page.setStretchFactor(1, 1)
        self.pages.addWidget(self.main_page)

        self.env_page = EnvironmentsPage(self.ws)
        self.env_page.back.connect(self.show_main)
        self.env_page.workspaceChanged.connect(self._env_changed)
        self.pages.addWidget(self.env_page)

    def _build_header(self):
        h = QWidget()
        h.setObjectName("header")
        h.setFixedHeight(48)
        lay = QHBoxLayout(h)
        lay.setContentsMargins(12, 0, 14, 0)
        lay.setSpacing(10)
        # no logo or name here: the Windows title bar above already shows the icon and "Zapi"
        self.workspace_btn = QPushButton()
        self.workspace_btn.setProperty("flat", True)
        self.workspace_btn.setIcon(theme.icon("chevron-down", theme.MUTED, 12))
        self.workspace_btn.setLayoutDirection(Qt.RightToLeft)
        self.workspace_btn.setStyleSheet(f"QPushButton {{ color: {theme.BLUE}; background: transparent; border: none; padding: 4px 6px; }}"
                                         f"QPushButton:hover {{ color: {theme.ACCENT_HOVER}; }}")
        self.workspace_btn.clicked.connect(self._workspace_menu)
        lay.addWidget(self.workspace_btn)
        lay.addStretch(1)
        self.env_box = QComboBox()
        self.env_box.setObjectName("envBox")
        self.env_box.setMinimumWidth(170)
        self.env_box.activated.connect(self._env_picked)
        lay.addWidget(self.env_box)
        self._update_workspace_label()
        return h

    def _build_statusbar(self):
        bar = QWidget()
        bar.setObjectName("statusbar")
        bar.setFixedHeight(26)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(12, 0, 12, 0)
        lay.setSpacing(6)
        self.net_dot = QLabel()
        self.net_dot.setPixmap(dot_icon(theme.GREEN, 8).pixmap(8, 8))
        self.net_label = QLabel("Online")
        lay.addWidget(self.net_dot)
        lay.addWidget(self.net_label)
        lay.addSpacing(8)
        self.console_btn = QPushButton("Console")
        self.console_btn.clicked.connect(self.toggle_console)
        runner_btn = QPushButton("Runner")
        runner_btn.clicked.connect(lambda: self.open_runner())
        mock_btn = QPushButton("Mock server")
        mock_btn.clicked.connect(self.open_mock)
        lay.addWidget(self.console_btn)
        lay.addWidget(runner_btn)
        lay.addWidget(mock_btn)
        self.mock_label = QLabel("")
        self.mock_label.setStyleSheet(f"color: {theme.GREEN}; font-size: 8pt;")
        lay.addWidget(self.mock_label)
        lay.addStretch(1)
        self.status_msg = QLabel("")
        lay.addWidget(self.status_msg)
        lay.addSpacing(10)
        lay.addWidget(QLabel(f"{APP_NAME} {VERSION}"))
        return bar

    def _init_network_indicator(self):
        self._net_info = None
        try:
            from PySide6.QtNetwork import QNetworkInformation
            if QNetworkInformation.loadDefaultBackend():
                self._net_info = QNetworkInformation.instance()
                self._net_info.reachabilityChanged.connect(self._reachability)
                self._reachability()
        except Exception:  # noqa: BLE001 - QtNetwork is optional; the label just stays "Ready"
            self.net_label.setText("Ready")

    def _reachability(self, *_):
        from PySide6.QtNetwork import QNetworkInformation
        r = self._net_info.reachability()
        offline = r == QNetworkInformation.Reachability.Disconnected
        self.net_dot.setPixmap(dot_icon(theme.RED if offline else theme.GREEN, 8).pixmap(8, 8))
        self.net_label.setText("Offline" if offline else "Online")

    def _wire_shortcuts(self):
        def sc(keys, fn):
            for k in keys:
                s = QShortcut(QKeySequence(k), self)
                s.setContext(Qt.ApplicationShortcut)
                s.activated.connect(fn)
        sc(["Ctrl+N", "Ctrl+T"], lambda: self.new_request(self._current_collection_id()))
        sc(["Ctrl+Return", "Ctrl+Enter"], self.send_current)
        sc(["Ctrl+S"], self.save_current)
        sc(["Ctrl+W"], lambda: self.close_tab(self.tab_bar.currentIndex()))
        sc(["Ctrl+Shift+C"], self.copy_current_as_curl)
        sc(["F1"], lambda: HelpDialog(self).exec())
        sc(["Ctrl+L"], lambda: self.current_view() and self.current_view().focus_url())
        sc(["Ctrl+`"], self.toggle_console)
        sc(["Ctrl+Shift+E"], self.show_environments)

    # ---- tabs --------------------------------------------------------------------------
    def views(self):
        return [self.view_stack.widget(i) for i in range(self.view_stack.count())]

    def current_view(self):
        i = self.tab_bar.currentIndex()
        return self._view_at(i) if i >= 0 else None

    def _view_at(self, i):
        # the tab bar can be reordered, so the view is stored on the tab itself
        return self.tab_bar.tabData(i)

    def _current_collection_id(self):
        v = self.current_view()
        return v.collection_id if v else ""

    def _settings_for_views(self):
        return {"verify_ssl": self.ws.settings.get("verify_ssl", True)}

    def _add_view(self, req, collection_id="", persisted=False):
        kind = req.get("kind", "http")
        if kind in ("websocket", "sse", "mqtt"):
            view = StreamView(req, collection_id, self.ws.variable_values, self._settings_for_views)
        elif kind == "grpc":
            view = GrpcView(req, collection_id, self.ws.variable_values, self._settings_for_views)
        else:
            view = RequestView(req, collection_id, values_provider=self.ws.variable_values)
        view.persisted = persisted
        c = self.ws.collection(collection_id)
        view.collection_name = c["name"] if c else ""
        view.update_crumb()
        if hasattr(view, "pasteImport"):
            view.pasteImport.connect(self._view_paste)
        view.sendRequested.connect(self.send_view)
        view.saveRequested.connect(self.save_view)
        view.codeRequested.connect(self.show_code)
        view.changed.connect(self._view_changed)
        self.view_stack.addWidget(view)
        i = self.tab_bar.addTab("")
        self.tab_bar.setTabData(i, view)
        self._decorate_tab(i, view)
        self.tab_bar.setCurrentIndex(i)
        self._update_empty_state()
        view._check_variables()
        return view

    def _index_of(self, view):
        for i in range(self.tab_bar.count()):
            if self.tab_bar.tabData(i) is view:
                return i
        return -1

    def _decorate_tab(self, i, view):
        r = view.req
        badge = store.badge(r)
        m = QLabel(badge[:4] if badge != "DELETE" else "DEL")
        m.setStyleSheet(f"color: {theme.method_color(badge)}; font-family: '{theme.mono_family()}'; "
                        "font-size: 7.5pt; font-weight: 700; background: transparent; border: none;")
        self.tab_bar.setTabButton(i, QTabBar.LeftSide, m)
        dirty = view.is_dirty() or not view.persisted
        name = view.title()
        if len(name) > 22:
            name = name[:21] + "…"
        self.tab_bar.setTabText(i, name + ("  •" if view.is_dirty() else ""))
        self.tab_bar.setTabToolTip(i, f"{r['method']} {r['url']}")

    def _view_changed(self, view):
        i = self._index_of(view)
        if i >= 0:
            self._decorate_tab(i, view)

    def _tab_changed(self, i):
        v = self._view_at(i) if i >= 0 else None
        if v is not None:
            self.view_stack.setCurrentWidget(v)
            v.refresh_variables()

    def _tab_moved(self, *_):
        pass

    def _update_empty_state(self):
        self.area_stack.setCurrentIndex(1 if self.tab_bar.count() else 0)

    def close_tab(self, i):
        """Close tab `i` (asking about unsaved changes). Returns False when the user cancelled."""
        if i < 0 or i >= self.tab_bar.count():
            return True
        view = self._view_at(i)
        if not self._confirm_close(view):
            return False
        self.tab_bar.removeTab(i)
        self.view_stack.removeWidget(view)
        t = self.threads.pop(id(view), None)
        if t is not None:
            t.cancelled = True
        if hasattr(view, "shutdown"):
            view.shutdown()
        view.deleteLater()
        self._update_empty_state()
        return True

    def _confirm_close(self, view):
        if not view.persisted and not view.req.get("url") and not view.is_dirty():
            return True
        needs = view.is_dirty() or (not view.persisted and bool(view.collect()["url"]))
        if not needs:
            return True
        box = QMessageBox(self)
        box.setWindowTitle("Unsaved changes")
        box.setText(f"Save changes to “{view.title()}”?")
        box.setStandardButtons(QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        r = box.exec()
        if r == QMessageBox.Cancel:
            return False
        if r == QMessageBox.Save:
            return self.save_view(view)
        return True

    # ---- creating / opening -----------------------------------------------------------------
    def new_request(self, collection_id="", folder="", kind="http"):
        req = store.new_kind_request(kind) if kind != "http" else store.new_request("Untitled request")
        req["folder"] = store.clean_folder(folder)
        self._add_view(req, collection_id if self.ws.collection(collection_id) else "", persisted=False)
        self.current_view().focus_url()
        self.show_main()

    def quick_send(self, method, url):
        req = store.new_request(url, method, url)
        view = self._add_view(req, "", persisted=False)
        view.url.setText(url)
        view._split_query_from_url()
        self.send_view(view)

    def open_saved_request(self, rid):
        for i in range(self.tab_bar.count()):
            if self._view_at(i).req["id"] == rid:
                self.tab_bar.setCurrentIndex(i)
                return
        c, r = self.ws.find_request(rid)
        if r is not None:
            self._add_view(copy.deepcopy(r), c["id"], persisted=True)

    def open_history(self, hid):
        h = next((x for x in self.ws.history if x["id"] == hid), None)
        if h is None:
            return
        req = copy.deepcopy(h["request"])
        req["id"] = store.new_id()
        view = self._add_view(req, "", persisted=False)
        view.req["name"] = h["request"]["name"]
        view.update_crumb()

    def paste_curl(self):
        dlg = CurlDialog(self)
        if dlg.exec() and dlg.text:
            self.import_pasted(dlg.text)

    def _view_paste(self, view, text):
        err = self.import_pasted(text, view)
        if err:
            view.show_import_error(err)

    def import_pasted(self, text, view=None):
        """Import pasted cURL / fetch() / Postman / OpenAPI / Zapi text. Returns an error message, or "" on success.
        A request replaces the contents of `view` (the tab that was pasted into) or opens in a new tab."""
        try:
            kind, obj = curl.parse_any(text)
        except curl.CurlError as e:
            return str(e)
        in_view = view is not None and self._index_of(view) >= 0
        if kind == "request":
            if in_view and view.req.get("kind", "http") in ("http", "soap"):
                obj["id"] = view.req["id"]
                obj["folder"] = view.req.get("folder", "")
                if view.req.get("name") not in ("", "Untitled request"):
                    obj["name"] = view.req["name"]
                view.load(obj)
                view._edited()
                view.url.setFocus()
            else:
                self._add_view(obj, "", persisted=False)
            self.show_main()
            self._flash("Imported request")
        else:
            was_empty = in_view and not view.req.get("url")
            if in_view and hasattr(view, "url"):
                prior = view.req.get("url", "")          # the pasted text is not a URL: put the old one back
                view.url.setText(prior)
                view._url_len = len(prior)
            msg = self.apply_import(kind, obj)
            if not msg:
                return ""
            self._flash("Imported " + msg)
            if was_empty and not view.is_dirty():
                self.close_tab(self._index_of(view))
        return ""

    def new_collection(self):
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "New collection", "Name", text="New collection")
        if ok and name.strip():
            c = self.ws.add_collection(name.strip())
            self.ws.save()
            self.sidebar.refresh()
            self.sidebar.tabs.setCurrentIndex(0)
            self._flash(f"Created collection “{c['name']}”")
            self.show_main()

    def import_file(self):
        dlg = ImportDialog(self)
        if not dlg.exec():
            return
        done = [m for m in (self.apply_import(k, o) for k, o in dlg.results) if m]
        if done:
            self._flash("Imported " + "; ".join(done))
        if getattr(dlg, "errors", None):
            QMessageBox.warning(self, "Some files were skipped", "\n".join(dlg.errors))

    def apply_import(self, kind, obj):
        """Add an imported document to the workspace. Returns a short description, or "" if the user cancelled."""
        ws = self.ws
        if kind == "collection":
            ws.collections.append(ws._fix_collection(obj))
            msg = f"collection “{obj['name']}” ({len(obj['items'])} requests)"
        elif kind == "collections":
            for c in obj:
                ws.collections.append(ws._fix_collection(c))
            msg = f"{len(obj)} collection(s)"
        elif kind == "environment":
            ws.environments.append(obj)
            msg = f"environment “{obj['name']}”"
        elif kind == "environments":
            ws.environments.extend(obj)
            msg = f"{len(obj)} environment(s)"
        elif kind == "history":
            msg = f"{ws.import_history(obj)} history entries"
        elif kind == "mock":
            routes = obj.get("routes", [])
            cfg = ws.settings.setdefault("mock", {"port": 3000, "cors": True, "routes": []})
            cfg["routes"][:] = routes              # same list object the running server uses
            msg = f"{len(routes)} mock route(s)"
        elif kind == "encrypted":
            for attempt in range(3):
                dlg = PassphraseDialog(self, confirm=False, title="Encrypted backup",
                                       text="This backup is protected with a passphrase." if attempt == 0 else
                                       "That passphrase did not open the backup. Try again.")
                if not dlg.exec():
                    return ""
                try:
                    inner = vault.decrypt_backup(obj, dlg.passphrase)
                    break
                except vault.BackupError as e:
                    last = str(e)
            else:
                QMessageBox.warning(self, "Encrypted backup", last)
                return ""
            try:
                inner_kind, inner_obj = importers.import_data(inner)
            except importers.ImportError_ as e:
                QMessageBox.warning(self, "Encrypted backup", str(e))
                return ""
            return self.apply_import(inner_kind, inner_obj)
        elif kind == "workspace":
            box = QMessageBox(self)
            box.setWindowTitle("Restore workspace backup")
            box.setText("How should this backup be restored?")
            box.setInformativeText("Merge adds the backup's collections and environments to what you have "
                                   "(same ones are overwritten). Replace swaps everything, including history.")
            merge = box.addButton("Merge", QMessageBox.AcceptRole)
            replace = box.addButton("Replace everything", QMessageBox.DestructiveRole)
            box.addButton(QMessageBox.Cancel)
            box.exec()
            if box.clickedButton() not in (merge, replace):
                return ""
            msg = "workspace backup: " + ws.import_workspace(obj, replace=box.clickedButton() is replace)
            self.mock_server.routes = ws.settings.get("mock", {}).get("routes", [])
        else:
            return ""
        ws.save()
        self.sidebar.refresh()
        self.refresh_environment_box()
        self.env_page.refresh_list()
        return msg

    # ---- export -------------------------------------------------------------------------------
    def _save_json(self, title, default_name, obj, filter_="JSON (*.json)"):
        path, _ = QFileDialog.getSaveFileName(self, title, default_name, filter_)
        if not path:
            return False
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(obj, f, indent=2, ensure_ascii=False)
        except OSError as e:
            QMessageBox.warning(self, "Export failed", str(e))
            return False
        self._flash(f"Exported to {path}")
        return True

    def export_what(self, what):
        """what: collections | environments | history | workspace"""
        ws = self.ws
        if what == "collections":
            if not ws.collections:
                QMessageBox.information(self, "Export", "There are no collections to export yet.")
                return
            self._save_json("Export all collections", "zapi-collections.json", ws.export_all_collections())
        elif what == "environments":
            if not ws.environments:
                QMessageBox.information(self, "Export", "There are no environments to export yet.")
                return
            self._save_json("Export all environments", "zapi-environments.json", ws.export_all_environments())
        elif what == "history":
            if not ws.history:
                QMessageBox.information(self, "Export", "The history is empty.")
                return
            self._save_json("Export history", "zapi-history.json", ws.export_history())
        elif what == "workspace":
            dlg = PassphraseDialog(
                self, confirm=True, allow_plain=True, title="Full workspace backup",
                text="A full backup contains Current values and secrets (tokens, passwords). Protect it with a "
                     "passphrase so it is safe to store or move to another computer.")
            if not dlg.exec():
                return
            if dlg.plain:
                if QMessageBox.question(self, "Readable backup", "Without a passphrase everyone who gets this file can "
                                        "read your secrets.\n\nSave it anyway?") != QMessageBox.Yes:
                    return
                self._save_json("Save workspace backup", "zapi-backup.json", ws.export_workspace())
            else:
                doc = vault.encrypt_backup(ws.export_workspace(), dlg.passphrase)
                self._save_json("Save encrypted workspace backup", "zapi-backup.encrypted.json", doc)

    def export_current_request(self):
        """Save the open request as a one-request Zapi collection file (handy for sharing a single call)."""
        v = self.current_view()
        if v is None:
            return
        r = vault.strip_request(v.collect())              # no credentials in a file you share
        doc = {"zapi": "collection", "name": r["name"], "variables": [], "folders": [], "items": [r]}
        self._save_json("Export request", f"{r['name']}.zapi-collection.json", doc)

    # ---- sending ------------------------------------------------------------------------------
    def send_current(self):
        v = self.current_view()
        if v is not None:
            v.primary_action()

    def send_view(self, view):
        if id(view) in self.threads:
            return
        req = view.collect()
        if not req["url"].strip():
            view.warn.setStyleSheet(f"color: {theme.ACCENT}; font-size: 8pt;")
            view.warn.setText("Enter a URL first.")
            view.warn.show()
            view.focus_url()
            return
        view._split_query_from_url()
        req = view.collect()
        before = self.ws.scopes(view.collection_id)
        opts = self.ws.request_options()
        view.set_busy(True)
        t = SendThread(copy.deepcopy(req), before, opts, self.jar, {"iteration": 0, "iterationCount": 1}, self)
        self.threads[id(view)] = t
        t.done.connect(lambda out, v=view, r=req, b=before, th=t: self._sent(v, r, b, out, th))
        t.start()

    def _sent(self, view, req, before, out, thread):
        self.threads.pop(id(view), None)
        thread.deleteLater()
        resp = out.resp
        # script changes to variables are kept even if the tab was closed meanwhile
        if out.scopes and out.scopes != before:
            self.ws.apply_scopes(view.collection_id, before, out.scopes)
            self.sidebar.refresh()
            self.refresh_environment_box()
        stamp = time.strftime("%H:%M:%S")
        if resp.ok:
            line = (f"[{stamp}] {req['method']} {resp.url}  →  {status_text(resp)}  "
                    f"{resp.elapsed_ms} ms  {human_size(resp.size)}")
            self.ws.add_history(req, resp.status, resp.elapsed_ms, resp.size)
        else:
            line = f"[{stamp}] {req['method']} {resp.url or req['url']}  →  ERROR: {resp.error}"
            self.ws.add_history(req, 0, 0, 0)
        self.console.appendPlainText(line)
        for l in out.logs:
            self.console.appendPlainText(f"    console.{l['level']}: {l['text']}")
        if out.tests:
            self.console.appendPlainText(f"    tests: {out.passed} passed, {out.failed} failed")
        for e in out.script_errors:
            self.console.appendPlainText(f"    script error: {e}")
        self.ws.save()
        self.sidebar.refresh()
        if self._index_of(view) < 0:
            return
        view.set_response(resp, out)
        for v in self.views():
            v.refresh_variables()

    # ---- saving --------------------------------------------------------------------------------
    def save_current(self):
        v = self.current_view()
        if v is not None:
            self.save_view(v)

    def save_view(self, view):
        req = view.collect()
        cid = view.collection_id
        if not view.persisted or not self.ws.collection(cid):
            dlg = SaveRequestDialog(self.ws, req["name"] if req["name"] != "Untitled request" else
                                    (req["url"] or "Untitled request")[:60], cid, self)
            if not dlg.exec():
                return False
            req["name"] = dlg.name.text().strip()
            cid = dlg.collection_id
        self.ws.save_request(req, cid)
        self.ws.save()
        view.req["name"] = req["name"]
        view.persisted = True
        view.set_collection(cid, self.ws.collection(cid)["name"])
        view.mark_saved()
        self._view_changed(view)
        self.sidebar.refresh()
        self._flash("Saved")
        return True

    def show_code(self, view):
        values = self.ws.variable_values(view.collection_id)
        CodeDialog(view.collect(), values, self).exec()

    # ---- environments / workspace ------------------------------------------------------------------
    def refresh_environment_box(self):
        self.env_box.blockSignals(True)
        self.env_box.clear()
        active = self.ws.settings.get("active_env", "")
        self.env_box.addItem(dot_icon(theme.FAINT), "No environment", "")
        for e in self.ws.environments:
            self.env_box.addItem(dot_icon(theme.GREEN if e["id"] == active else theme.FAINT), e["name"], e["id"])
        self.env_box.insertSeparator(self.env_box.count())
        self.env_box.addItem("Manage environments…", "__manage__")
        i = self.env_box.findData(active)
        self.env_box.setCurrentIndex(i if i >= 0 else 0)
        self.env_box.blockSignals(False)

    def _env_picked(self, i):
        data = self.env_box.itemData(i)
        if data == "__manage__":
            self.refresh_environment_box()
            self.show_environments()
            return
        self.ws.settings["active_env"] = data or ""
        self.ws.save()
        self.refresh_environment_box()
        self.sidebar.refresh()
        for v in self.views():
            v.refresh_variables()

    def show_environments(self):
        self.env_page.refresh_list()
        self.pages.setCurrentWidget(self.env_page)

    def show_main(self):
        self.pages.setCurrentWidget(self.main_page)

    def _env_changed(self):
        self.refresh_environment_box()
        self.sidebar.refresh()
        for v in self.views():
            v.refresh_variables()

    def _workspace_changed(self):
        # a request was renamed / deleted in the sidebar
        for i in range(self.tab_bar.count()):
            v = self._view_at(i)
            c, r = self.ws.find_request(v.req["id"])
            if v.persisted and r is None:
                v.persisted = False
                v.set_collection("", "")
            elif r is not None:
                v.req["name"] = r["name"]
                v.req["folder"] = r.get("folder", "")
                v.set_collection(c["id"], c["name"])
                v.mark_saved()
            self._decorate_tab(i, v)

    def _update_workspace_label(self):
        self.workspace_btn.setText(self.ws.settings.get("workspace_name", "My Workspace") + "  ")

    def _workspace_menu(self):
        m = QMenu(self)
        m.addAction("Settings…", self.open_settings)
        m.addAction("Environments", self.show_environments)
        m.addAction("Runner…", lambda: self.open_runner())
        m.addAction("Mock server…", self.open_mock)
        m.addAction("Cookies…", lambda: CookieDialog(self.jar, self).exec())
        m.addSeparator()
        m.addAction("Open logs folder", self._open_logs)
        m.addAction("Copy diagnostic info (for bug reports)", self.copy_diagnostics)
        m.addAction("Import…", self.import_file)
        ex = m.addMenu("Export")
        ex.addAction("All collections…", lambda: self.export_what("collections"))
        ex.addAction("All environments…", lambda: self.export_what("environments"))
        ex.addAction("History…", lambda: self.export_what("history"))
        ex.addAction("Open request…", self.export_current_request)
        ex.addSeparator()
        ex.addAction("Full workspace backup…", lambda: self.export_what("workspace"))
        m.addSeparator()
        m.addAction("Help and shortcuts\tF1", lambda: HelpDialog(self).exec())
        m.addAction("About Zapi", self.about)
        m.exec(self.workspace_btn.mapToGlobal(self.workspace_btn.rect().bottomLeft()))

    def open_settings(self):
        dlg = SettingsDialog(self.ws, self)
        if dlg.exec():
            self._update_workspace_label()
            if dlg.look_changed:
                self.apply_appearance()

    def apply_appearance(self):
        """Switch theme / accent / editor font: the UI is rebuilt in a new window, with the same tabs and size."""
        s = self.ws.settings
        self._persist_session()
        theme.apply(s.get("theme", "Dark"), s.get("accent", "Amber"), s.get("code_size", 10))
        app = QApplication.instance()
        app.setStyleSheet(theme.qss())
        fresh = MainWindow(self.ws)
        fresh.setGeometry(self.geometry())
        # background services stay up: the mock server keeps listening and now reports to the new window
        fresh.mock_server = self.mock_server
        fresh.mock_server.on_request = fresh.mock_bridge.request.emit
        fresh._sync_mock_label()
        self._handover = True
        app._zapi_window = fresh
        if self.isMaximized():
            fresh.showMaximized()
        else:
            fresh.show()
        self.close()

    def open_runner(self, collection_id="", folder=""):
        dlg = RunnerDialog(self.ws, collection_id or self._current_collection_id(), folder, self)
        dlg.exec()
        if dlg.changed_variables:
            self._env_changed()

    # ---- copy / tab / console menus ----------------------------------------------------------------
    def copy_current_as_curl(self):
        v = self.current_view()
        if v is not None and hasattr(v, "copy_what"):
            v.copy_what("curl")

    def _tab_menu(self, pos):
        i = self.tab_bar.tabAt(pos)
        if i < 0:
            return
        view = self._view_at(i)
        m = QMenu(self)
        m.addAction("Close", lambda: self.close_tab(self._index_of(view)))
        m.addAction("Close others", lambda: self.close_others(view))
        m.addAction("Close all", self.close_all)
        m.addSeparator()
        m.addAction("Duplicate tab", lambda: self.duplicate_tab(view))
        if hasattr(view, "copy_what"):
            m.addAction("Copy URL", lambda: view.copy_what("url"))
            m.addAction("Copy as cURL", lambda: view.copy_what("curl"))
        m.exec(self.tab_bar.mapToGlobal(pos))

    def close_others(self, keep):
        for v in [x for x in self.views_in_tab_order() if x is not keep]:
            if not self.close_tab(self._index_of(v)):
                break

    def close_all(self):
        for v in self.views_in_tab_order():
            if not self.close_tab(self._index_of(v)):
                break

    def views_in_tab_order(self):
        return [self._view_at(i) for i in range(self.tab_bar.count())]

    def duplicate_tab(self, view):
        req = copy.deepcopy(view.collect())
        req["id"] = store.new_id()
        req["name"] = req["name"] + " copy"
        self._add_view(req, view.collection_id, persisted=False)

    def _console_menu(self, pos):
        m = QMenu(self)
        m.addAction("Copy", self.console.copy).setEnabled(self.console.textCursor().hasSelection())
        m.addAction("Copy all", lambda: QApplication.clipboard().setText(self.console.toPlainText()))
        m.addAction("Select all", self.console.selectAll)
        m.addSeparator()
        m.addAction("Clear", self.console.clear)
        m.exec(self.console.viewport().mapToGlobal(pos))

    def _warn_unreadable_secrets(self):
        n, self.ws.secret_failures = self.ws.secret_failures, 0
        if n and isValid(self):
            QMessageBox.warning(
                self, "Some secrets could not be opened",
                f"{n} saved secret(s) (passwords, tokens or Secret variables) could not be decrypted. Secrets are "
                "encrypted for the Windows account and computer that saved them, so this happens when the workspace "
                "file was copied from somewhere else.\n\nThey were left empty. Enter them again, or restore them "
                "from a full workspace backup (Export → Full workspace backup).")

    def _flash(self, text, ms=5000):
        """Show a message in the status bar for a few seconds. Safe if the window is closed in the meantime."""
        self.status_msg.setText(text)
        QTimer.singleShot(ms, lambda: self._clear_status(text))

    def _clear_status(self, text):
        if isValid(self) and isValid(self.status_msg) and self.status_msg.text() == text:
            self.status_msg.setText("")

    def copy_diagnostics(self):
        from . import diagnostics
        QApplication.clipboard().setText(diagnostics.info(self.ws))
        self._flash("Copied diagnostic info: version, system and settings only, no requests or secrets")

    def _open_logs(self):
        from . import crash
        crash.open_logs_folder()

    def open_mock(self):
        MockDialog(self.ws, self.mock_server, self.mock_bridge, self).exec()
        self._sync_mock_label()

    def _sync_mock_label(self):
        if not isValid(self):
            return
        self.mock_label.setText(f"Mock :{self.mock_server.port}" if self.mock_server.running else "")

    def toggle_console(self):
        self.console.setVisible(not self.console.isVisible())

    def about(self):
        box = QMessageBox(self)
        box.setWindowTitle(f"About {APP_NAME}")
        box.setIconPixmap(theme.logo_pixmap(64))
        box.setText(f"<b>{APP_NAME} {VERSION}</b><br>A fast, local-first API client.<br><br>"
                    "Everything stays on this computer. No account, no telemetry.<br><br>"
                    "Made by Lavati Softwares. MIT License.")
        box.exec()

    # ---- session -----------------------------------------------------------------------------------
    def _restore_tabs(self):
        for t in self.ws.settings.get("open_tabs", []):
            try:
                if t.get("persisted"):
                    c, r = self.ws.find_request(t["req"]["id"])
                    if r is None:
                        continue
                    view = self._add_view(t["req"], c["id"], persisted=True)
                    if not t.get("clean"):
                        # unsaved edits come back as edits: compare against the stored version, not the edited one
                        view.load(r)
                        view.mark_saved()
                        view.load(t["req"])
                        view._edited()
                else:
                    self._add_view(t["req"], t.get("cid", ""), persisted=False)
            except (KeyError, TypeError):
                continue
        n = self.tab_bar.count()
        if n:
            self.tab_bar.setCurrentIndex(min(self.ws.settings.get("active_tab", 0), n - 1))

    def _persist_session(self):
        tabs = []
        for i in range(self.tab_bar.count()):
            v = self._view_at(i)
            tabs.append({"req": v.collect(), "cid": v.collection_id, "persisted": v.persisted,
                         "clean": not v.is_dirty()})
        self.ws.settings["open_tabs"] = tabs
        self.ws.settings["active_tab"] = self.tab_bar.currentIndex()
        self.ws.save()

    def closeEvent(self, e):
        self._persist_session()
        for t in self.threads.values():
            t.cancelled = True
        for v in self.views():
            if hasattr(v, "shutdown"):
                v.shutdown()
        if not getattr(self, "_handover", False):       # a theme switch keeps these running for the new window
            self.mock_server.stop()
            scripting.shutdown()
        super().closeEvent(e)
