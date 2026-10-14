"""Left sidebar: Collections tree (with folders), History list and the Variables overview."""
import json
import time

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QFileDialog, QFrame, QHBoxLayout, QHeaderView, QInputDialog,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox, QPushButton,
                               QStackedWidget, QTabBar, QTableWidget, QTableWidgetItem, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from PySide6.QtWidgets import QApplication

from . import curl, http_client, store, theme, vault
from .widgets import primary

ROLE_ID, ROLE_KIND, ROLE_CID = Qt.UserRole, Qt.UserRole + 1, Qt.UserRole + 2


def day_label(ts, now=None):
    """History group heading: Today, Yesterday, the weekday for the last week, then the date."""
    now = time.time() if now is None else now
    day = time.localtime(ts)
    days = (time.mktime(time.localtime(now)[:3] + (0, 0, 0, 0, 0, -1)) -
            time.mktime(day[:3] + (0, 0, 0, 0, 0, -1))) / 86400
    days = round(days)
    if days <= 0:
        return "Today"
    if days == 1:
        return "Yesterday"
    if days < 7:
        return time.strftime("%A", day)
    return time.strftime("%d %b %Y", day).lstrip("0")


def _method_label(text):
    lb = QLabel(text)
    lb.setFixedWidth(52)
    lb.setStyleSheet(f"color: {theme.method_color(text)}; font-family: '{theme.mono_family()}'; "
                     f"font-size: 7.5pt; font-weight: 700; background: transparent;")
    return lb


def _row(badge, text, extra=None):
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(4, 0, 4, 0)
    lay.setSpacing(6)
    lay.addWidget(_method_label(badge))
    name = QLabel(text)
    name.setStyleSheet("background: transparent;")
    lay.addWidget(name, 1)
    if extra:
        lay.addWidget(extra)
    w.setAttribute(Qt.WA_TransparentForMouseEvents, True)
    return w


def _row_size():
    return QSize(0, 28)


class _Tree(QTreeWidget):
    """Drag a request onto a folder / collection to move it; the model is updated, then the tree is rebuilt."""

    moved = Signal(str, str, str)      # request id, target collection id, target folder path

    def dropEvent(self, event):
        src = self.currentItem()
        dst = self.itemAt(event.position().toPoint())
        event.ignore()
        if src is None or dst is None or src.data(0, ROLE_KIND) != "request":
            return
        kind = dst.data(0, ROLE_KIND)
        cid = dst.data(0, ROLE_CID) or dst.data(0, ROLE_ID)
        if kind == "collection":
            folder = ""
        elif kind == "folder":
            folder = dst.data(0, ROLE_ID)
        else:      # dropped on another request: land in the same folder
            parent = dst.parent()
            folder = parent.data(0, ROLE_ID) if parent is not None and parent.data(0, ROLE_KIND) == "folder" else ""
        self.moved.emit(src.data(0, ROLE_ID), cid, folder)


class Sidebar(QWidget):
    openRequest = Signal(str)             # request id
    openHistory = Signal(str)             # history entry id
    newRequest = Signal(str, str, str)    # collection id ("" = unsaved), folder path, kind
    newCollection = Signal()
    pasteCurl = Signal()
    importRequested = Signal()
    manageEnvironments = Signal()
    runCollection = Signal(str, str)      # collection id, folder path
    exportRequested = Signal(str)         # collections | environments | history | workspace
    copied = Signal(str)                  # status message after a copy action
    workspaceChanged = Signal()

    def __init__(self, ws, parent=None):
        super().__init__(parent)
        self.ws = ws
        self.setObjectName("sidebar")
        self.setMinimumWidth(220)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.tabs = QTabBar()
        self.tabs.setExpanding(True)
        self.tabs.setDrawBase(False)
        for t in ("Collections", "History", "Variables"):
            self.tabs.addTab(t)
        lay.addWidget(self.tabs)
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background: {theme.BORDER};")
        lay.addWidget(line)

        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(16, 14, 16, 12)
        bl.setSpacing(12)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search requests")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.refresh)
        bl.addWidget(self.search)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.new_btn = primary(QPushButton("+ New"))
        self.new_btn.clicked.connect(self._new_menu)
        self.import_btn = QPushButton("Import")
        self.import_btn.clicked.connect(self.importRequested.emit)
        self.export_btn = QPushButton("Export")
        self.export_btn.clicked.connect(self._export_menu)
        row.addWidget(self.new_btn, 1)
        row.addWidget(self.import_btn, 1)
        row.addWidget(self.export_btn, 1)
        self.actions = QWidget()
        self.actions.setLayout(row)
        bl.addWidget(self.actions)

        self.stack = QStackedWidget()
        self.tree = _Tree()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(14)
        self.tree.setExpandsOnDoubleClick(False)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_menu)
        self.tree.itemClicked.connect(self._tree_clicked)
        self.tree.setAnimated(False)
        self.tree.setDragEnabled(True)
        self.tree.setAcceptDrops(True)
        self.tree.setDropIndicatorShown(True)
        self.tree.setDragDropMode(QAbstractItemView.DragDrop)
        self.tree.moved.connect(self._moved)
        self.stack.addWidget(self.tree)

        hist = QWidget()
        hl = QVBoxLayout(hist)
        hl.setContentsMargins(0, 0, 0, 0)
        self.history = QListWidget()
        self.history.itemClicked.connect(lambda it: it.data(Qt.UserRole) and self.openHistory.emit(it.data(Qt.UserRole)))
        self.history.setContextMenuPolicy(Qt.CustomContextMenu)
        self.history.customContextMenuRequested.connect(self._history_menu)
        hl.addWidget(self.history, 1)
        hrow = QHBoxLayout()
        hrow.setSpacing(8)
        h_export = QPushButton("Export")
        h_export.clicked.connect(lambda: self.exportRequested.emit("history"))
        h_import = QPushButton("Import")
        h_import.clicked.connect(self.importRequested.emit)
        clear = QPushButton("Clear")
        clear.clicked.connect(self._clear_history)
        for b in (h_export, h_import, clear):
            hrow.addWidget(b, 1)
        hl.addLayout(hrow)
        self.stack.addWidget(hist)

        var = QWidget()
        vl = QVBoxLayout(var)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(10)
        self.var_title = QLabel()
        self.var_title.setStyleSheet("font-weight: 600;")
        vl.addWidget(self.var_title)
        self.var_table = QTableWidget(0, 2)
        self.var_table.setHorizontalHeaderLabels(["Variable", "Value"])
        self.var_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.var_table.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.var_table.verticalHeader().setVisible(False)
        self.var_table.setShowGrid(False)
        self.var_table.setEditTriggers(QTableWidget.NoEditTriggers)
        vl.addWidget(self.var_table, 1)
        manage = QPushButton("Manage environments")
        manage.clicked.connect(self.manageEnvironments.emit)
        vl.addWidget(manage)
        self.stack.addWidget(var)

        empty = QWidget()
        el = QVBoxLayout(empty)
        el.setAlignment(Qt.AlignCenter)
        el.setSpacing(8)
        ic = QLabel()
        ic.setPixmap(theme.icon("folder", theme.FAINT, 30).pixmap(30, 30))
        ic.setAlignment(Qt.AlignCenter)
        t = QLabel("No collections yet")
        t.setStyleSheet("font-weight: 600;")
        t.setAlignment(Qt.AlignCenter)
        d = QLabel("Collections keep related requests together so you can find and re-run them later.")
        d.setObjectName("muted")
        d.setWordWrap(True)
        d.setMinimumHeight(80)
        d.setAlignment(Qt.AlignCenter)
        mk = primary(QPushButton("Create collection"))
        mk.clicked.connect(self.newCollection.emit)
        for x in (ic, t, d):
            el.addWidget(x)
        el.addSpacing(6)
        el.addWidget(mk, 0, Qt.AlignCenter)
        self.stack.addWidget(empty)
        bl.addWidget(self.stack, 1)
        lay.addWidget(body, 1)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.refresh()

    # ---- refresh -------------------------------------------------------------------
    def _tab_changed(self, i):
        self.search.setPlaceholderText(["Search requests", "Search history", "Search variables"][i] if i < 3 else "Search")
        self.refresh()

    def _layout_for_tab(self):
        i = self.tabs.currentIndex()
        empty = i == 0 and not self.ws.collections
        self.stack.setCurrentIndex(3 if empty else i)
        self.actions.setVisible(i == 0 and not empty)
        self.search.setVisible(i in (1, 2) or (i == 0 and not empty))

    def refresh(self, *_):
        self._fill_tree()
        self._fill_history()
        self._fill_variables()
        self._layout_for_tab()

    def _expanded_keys(self):
        keys = {}

        def walk(item):
            keys[(item.data(0, ROLE_KIND), item.data(0, ROLE_CID) or "", item.data(0, ROLE_ID))] = item.isExpanded()
            for i in range(item.childCount()):
                walk(item.child(i))
        for i in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(i))
        return keys

    def _fill_tree(self):
        q = self.search.text().strip().lower() if self.tabs.currentIndex() == 0 else ""
        was = self._expanded_keys()
        self.tree.clear()

        def request_item(parent, r, cid):
            it = QTreeWidgetItem(parent)
            it.setData(0, ROLE_ID, r["id"])
            it.setData(0, ROLE_KIND, "request")
            it.setData(0, ROLE_CID, cid)
            self.tree.setItemWidget(it, 0, _row(store.badge(r), r["name"]))
            it.setSizeHint(0, _row_size())
            return it

        for c in self.ws.collections:
            top = QTreeWidgetItem([c["name"]])
            top.setData(0, ROLE_ID, c["id"])
            top.setData(0, ROLE_KIND, "collection")
            top.setData(0, ROLE_CID, c["id"])
            if q:
                reqs = [r for r in c["items"] if q in r["name"].lower() or q in r["url"].lower()]
                if not reqs and q not in c["name"].lower():
                    continue
                self.tree.addTopLevelItem(top)
                for r in reqs:
                    request_item(top, r, c["id"])
                top.setExpanded(True)
                continue
            self.tree.addTopLevelItem(top)
            folders = {}
            for path in sorted(c["folders"]):
                parent = folders.get("/".join(path.split("/")[:-1]), top)
                it = QTreeWidgetItem(parent, [path.split("/")[-1]])
                it.setIcon(0, theme.icon("folder", theme.MUTED, 14))
                it.setData(0, ROLE_ID, path)
                it.setData(0, ROLE_KIND, "folder")
                it.setData(0, ROLE_CID, c["id"])
                folders[path] = it
            for r in c["items"]:
                request_item(folders.get(r.get("folder", ""), top), r, c["id"])
            for key, it in [(("collection", c["id"], c["id"]), top)] + \
                    [(("folder", c["id"], p), i) for p, i in folders.items()]:
                it.setExpanded(was.get(key, key[0] == "collection" and len(self.ws.collections) <= 3))

    def _fill_history(self):
        q = self.search.text().strip().lower() if self.tabs.currentIndex() == 1 else ""
        self.history.clear()
        last_day = None
        for h in sorted(self.ws.history, key=lambda e: e["at"], reverse=True):
            r = h["request"]
            if q and q not in r["url"].lower() and q not in r["name"].lower():
                continue
            label = day_label(h["at"])
            if label != last_day:
                last_day = label
                head = QListWidgetItem()
                head.setFlags(Qt.NoItemFlags)             # a heading: not clickable, never opens anything
                self.history.addItem(head)
                lb = QLabel(label.upper())
                lb.setStyleSheet(f"color: {theme.MUTED}; font-size: 8pt; font-weight: 700; letter-spacing: 1px; "
                                 "background: transparent; padding: 8px 6px 2px 6px;")
                self.history.setItemWidget(head, lb)
                head.setSizeHint(QSize(10, 30))
            it = QListWidgetItem()
            it.setData(Qt.UserRole, h["id"])
            code = h.get("status", 0)
            badge = QLabel(str(code) if code else "ERR")
            badge.setStyleSheet(f"color: {theme.status_color(code)}; font-size: 8pt; background: transparent;")
            when = time.strftime("%H:%M", time.localtime(h["at"]))
            it.setToolTip(f"{r['method']} {r['url']}\n{when} - {h.get('ms', 0)} ms")
            self.history.addItem(it)
            self.history.setItemWidget(it, _row(store.badge(r), r["url"] or r["name"], badge))
            it.setSizeHint(_row_size())

    def _fill_variables(self):
        env = self.ws.active_environment()
        self.var_title.setText(f"Active: {env['name']}" if env else "No environment selected")
        q = self.search.text().strip().lower() if self.tabs.currentIndex() == 2 else ""
        rows = [("globals", v) for v in self.ws.globals["vars"] if v.get("enabled", True) and v["key"]]
        if env:
            rows += [("env", v) for v in env["vars"] if v.get("enabled", True) and v["key"]]
        if q:
            rows = [(s, v) for s, v in rows if q in v["key"].lower() or (not v.get("secret") and q in v.get("current", "").lower())]
        self.var_table.setRowCount(len(rows))
        for i, (scope, v) in enumerate(rows):
            self.var_table.setItem(i, 0, QTableWidgetItem(v["key"] + ("  (global)" if scope == "globals" else "")))
            self.var_table.setItem(i, 1, QTableWidgetItem("•" * 10 if v.get("secret") else v.get("current", "")))

    # ---- interactions ----------------------------------------------------------------
    def _target_collection(self):
        """The collection a new folder / request should go to: the only one, or the one selected in the tree."""
        cols = self.ws.collections
        if len(cols) == 1:
            return cols[0]["id"]
        item = self.tree.currentItem()
        if item is not None:
            return item.data(0, ROLE_CID) or ""
        return ""

    def _new_menu(self):
        m = QMenu(self)
        m.addAction("HTTP request", lambda: self.newRequest.emit(self._target_collection(), "", "http"))
        for label, kind in (("WebSocket", "websocket"), ("Server-Sent Events", "sse"), ("MQTT", "mqtt"),
                            ("gRPC", "grpc"), ("SOAP request", "soap")):
            m.addAction(label, lambda k=kind: self.newRequest.emit(self._target_collection(), "", k))
        m.addSeparator()
        m.addAction("Collection", self.newCollection.emit)
        m.addAction("Folder…", lambda: self._new_folder(self._target_collection(), ""))
        m.addAction("Paste cURL…", self.pasteCurl.emit)
        m.exec(self.new_btn.mapToGlobal(self.new_btn.rect().bottomLeft()))

    def _export_menu(self):
        m = QMenu(self)
        m.addAction("All collections…", lambda: self.exportRequested.emit("collections"))
        m.addAction("All environments…", lambda: self.exportRequested.emit("environments"))
        m.addAction("History…", lambda: self.exportRequested.emit("history"))
        m.addSeparator()
        m.addAction("Full workspace backup…", lambda: self.exportRequested.emit("workspace"))
        m.exec(self.export_btn.mapToGlobal(self.export_btn.rect().bottomLeft()))

    def _tree_clicked(self, item, _col):
        kind = item.data(0, ROLE_KIND)
        if kind in ("collection", "folder"):
            item.setExpanded(not item.isExpanded())
        elif kind == "request":
            self.openRequest.emit(item.data(0, ROLE_ID))

    def _tree_menu(self, pos):
        item = self.tree.itemAt(pos)
        if item is None:
            return
        kind = item.data(0, ROLE_KIND)
        ident = item.data(0, ROLE_ID)
        cid = item.data(0, ROLE_CID)
        m = QMenu(self)
        if kind == "collection":
            c = self.ws.collection(ident)
            add = m.addMenu("New request")
            for label, k in (("HTTP", "http"), ("WebSocket", "websocket"), ("Server-Sent Events", "sse"),
                             ("MQTT", "mqtt"), ("gRPC", "grpc"), ("SOAP", "soap")):
                add.addAction(label, lambda kk=k: self.newRequest.emit(ident, "", kk))
            m.addAction("New folder…", lambda: self._new_folder(ident, ""))
            m.addAction("Run collection…", lambda: self.runCollection.emit(ident, ""))
            m.addSeparator()
            m.addAction("Rename", lambda: self._rename_collection(c))
            m.addAction("Variables…", lambda: self._collection_variables(c))
            ex = m.addMenu("Export")
            ex.addAction("Zapi JSON…", lambda: self._export_collection(c, "zapi"))
            ex.addAction("Postman collection v2.1…", lambda: self._export_collection(c, "postman"))
            m.addSeparator()
            m.addAction("Delete", lambda: self._delete_collection(c))
        elif kind == "folder":
            m.addAction("New request here", lambda: self.newRequest.emit(cid, ident, "http"))
            m.addAction("New subfolder…", lambda: self._new_folder(cid, ident))
            m.addAction("Run folder…", lambda: self.runCollection.emit(cid, ident))
            m.addSeparator()
            m.addAction("Rename", lambda: self._rename_folder(cid, ident))
            m.addAction("Delete", lambda: self._delete_folder(cid, ident))
        elif kind == "request":
            _c, r = self.ws.find_request(ident)
            m.addAction("Open", lambda: self.openRequest.emit(ident))
            m.addAction("Rename", lambda: self._rename_request(r))
            m.addAction("Duplicate", lambda: self._duplicate(ident))
            m.addAction("Copy URL", lambda: self.copy_request_text(r, "url", cid))
            if r.get("kind", "http") in ("http", "soap"):
                m.addAction("Copy as cURL", lambda: self.copy_request_text(r, "curl", cid))
            move = m.addMenu("Move to")
            col = self.ws.collection(cid)
            move.addAction("(collection root)", lambda: self._moved(ident, cid, ""))
            for f in col["folders"] if col else []:
                move.addAction(f, lambda path=f: self._moved(ident, cid, path))
            m.addSeparator()
            m.addAction("Delete", lambda: self._delete_request(r))
        else:
            return
        m.exec(self.tree.viewport().mapToGlobal(pos))

    def copy_request_text(self, req, what, collection_id=""):
        """Put a request's URL (variables filled in from the active environment) or its cURL command on the clipboard."""
        values = self.ws.variable_values(collection_id)
        try:
            text = curl.generate(req, values, "cURL") if what == "curl" else http_client.build_url(req, values) \
                if req.get("kind", "http") in ("http", "soap") else req.get("url", "")
        except (ValueError, OSError) as e:
            text = ""
            self.copied.emit(f"Could not copy: {e}")
            return ""
        QApplication.clipboard().setText(text)
        self.copied.emit("Copied cURL command" if what == "curl" else "Copied URL")
        return text

    def _history_menu(self, pos):
        item = self.history.itemAt(pos)
        if item is None:
            return
        hid = item.data(Qt.UserRole)
        entry = next((h for h in self.ws.history if h["id"] == hid), None)
        if entry is None:
            return
        req = entry["request"]
        m = QMenu(self)
        m.addAction("Open", lambda: self.openHistory.emit(hid))
        m.addAction("Copy URL", lambda: self.copy_request_text(req, "url"))
        if req.get("kind", "http") in ("http", "soap"):
            m.addAction("Copy as cURL", lambda: self.copy_request_text(req, "curl"))
        m.addSeparator()
        m.addAction("Remove from history", lambda: self._remove_history(hid))
        m.exec(self.history.viewport().mapToGlobal(pos))

    def _remove_history(self, hid):
        self.ws.history = [h for h in self.ws.history if h["id"] != hid]
        self._changed()

    def _changed(self):
        self.ws.save()
        self.refresh()
        self.workspaceChanged.emit()

    def _moved(self, rid, cid, folder):
        _c, r = self.ws.find_request(rid)
        if r is None:
            return
        r["folder"] = store.clean_folder(folder)
        self.ws.save_request(r, cid)
        self.ws.add_folder(cid, r["folder"])
        self._changed()

    def _new_folder(self, cid, parent):
        if not cid:
            if not self.ws.collections:
                return
            cid = self.ws.collections[0]["id"]
        name, ok = QInputDialog.getText(self, "New folder", "Name", text="New folder")
        name = (name or "").strip().replace("/", "-")
        if ok and name:
            self.ws.add_folder(cid, (parent + "/" if parent else "") + name)
            self._changed()

    def _rename_folder(self, cid, path):
        old = path.split("/")[-1]
        name, ok = QInputDialog.getText(self, "Rename folder", "Name", text=old)
        name = (name or "").strip().replace("/", "-")
        if ok and name and name != old:
            self.ws.rename_folder(cid, path, "/".join(path.split("/")[:-1] + [name]))
            self._changed()

    def _delete_folder(self, cid, path):
        c = self.ws.collection(cid)
        n = sum(1 for r in c["items"] if r["folder"] == path or r["folder"].startswith(path + "/"))
        if QMessageBox.question(self, "Delete folder", f"Delete “{path}” and the {n} request(s) inside it?") == QMessageBox.Yes:
            self.ws.delete_folder(cid, path)
            self._changed()

    def _rename_collection(self, c):
        name, ok = QInputDialog.getText(self, "Rename collection", "Name", text=c["name"])
        if ok and name.strip():
            c["name"] = name.strip()
            self._changed()

    def _rename_request(self, r):
        name, ok = QInputDialog.getText(self, "Rename request", "Name", text=r["name"])
        if ok and name.strip():
            r["name"] = name.strip()
            self._changed()

    def _duplicate(self, rid):
        self.ws.duplicate_request(rid)
        self._changed()

    def _delete_request(self, r):
        if QMessageBox.question(self, "Delete request", f"Delete “{r['name']}”?") == QMessageBox.Yes:
            self.ws.delete_request(r["id"])
            self._changed()

    def _delete_collection(self, c):
        n = len(c["items"])
        if QMessageBox.question(self, "Delete collection",
                                f"Delete “{c['name']}” and its {n} request{'s' if n != 1 else ''}?") == QMessageBox.Yes:
            self.ws.delete_collection(c["id"])
            self._changed()

    def _collection_variables(self, c):
        text = "\n".join(f"{v['key']}={v['value']}" for v in c.get("variables", []))
        from PySide6.QtWidgets import QDialog, QDialogButtonBox, QPlainTextEdit
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Variables - {c['name']}")
        dlg.resize(460, 300)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel("One per line as name=value. Environment variables override these."))
        edit = QPlainTextEdit(text)
        edit.setFont(theme.mono(10))
        lay.addWidget(edit)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        if dlg.exec():
            c["variables"] = [store.kv(k.strip(), v) for k, _, v in
                              (ln.partition("=") for ln in edit.toPlainText().splitlines()) if k.strip()]
            self._changed()

    def _export_collection(self, c, fmt="zapi"):
        suffix = ".postman_collection.json" if fmt == "postman" else ".zapi-collection.json"
        path, _ = QFileDialog.getSaveFileName(self, "Export collection", f"{c['name']}{suffix}", "JSON (*.json)")
        if not path:
            return
        skipped = 0
        if fmt == "postman":
            from . import exporters
            doc, skipped = exporters.to_postman(vault.strip_collection(c))     # no credentials in shared files
        else:
            doc = self.ws.export_collection(c)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=2, ensure_ascii=False)
        if skipped:
            QMessageBox.information(self, "Export", f"{skipped} request(s) (WebSocket, SSE, MQTT or gRPC) have no "
                                    "Postman equivalent and were left out.")

    def _clear_history(self):
        if self.ws.history and QMessageBox.question(self, "Clear history", "Remove all history entries?") == QMessageBox.Yes:
            self.ws.clear_history()
            self._changed()
