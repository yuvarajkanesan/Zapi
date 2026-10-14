"""Tabs for the non-HTTP protocols: WebSocket, Server-Sent Events, MQTT (StreamView) and gRPC (GrpcView)."""
import copy
import html
import json
import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFormLayout, QFrame, QHBoxLayout, QHeaderView,
                               QInputDialog, QLabel, QLineEdit, QPushButton, QSplitter, QStackedWidget, QTabBar,
                               QTableWidget, QTableWidgetItem, QTextBrowser, QVBoxLayout, QWidget)

from . import grpc_client, protocols, store, theme, variables
from .widgets import CodeEditor, KVTable, Worker, flat, pretty_json, primary


def _esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class ProtoBase(QWidget):
    """What the main window expects from every tab (see RequestView): save / dirty / crumb handling."""
    sendRequested = Signal(object)
    saveRequested = Signal(object)
    codeRequested = Signal(object)
    changed = Signal(object)

    def __init__(self, request, collection_id="", values_provider=None, settings_provider=None, parent=None):
        super().__init__(parent)
        self.req = store.normalize_request(request)
        self.collection_id = collection_id
        self.collection_name = ""
        self.persisted = False
        self.values_provider = values_provider or (lambda cid: {})
        self.settings_provider = settings_provider or (lambda: {"verify_ssl": True})
        self.url = QLineEdit()                  # the main window focuses / reads view.url
        self._loading = True
        self._saved = None
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        top = QWidget()
        tl = QHBoxLayout(top)
        tl.setContentsMargins(18, 14, 18, 10)
        self.crumb = QLabel()
        self.crumb.setObjectName("crumb")
        self.crumb.setTextFormat(Qt.RichText)
        self.crumb.setToolTip("Double-click to rename")
        self.crumb.mouseDoubleClickEvent = lambda e: self.rename()
        tl.addWidget(self.crumb, 1)
        save = QPushButton("Save")
        save.clicked.connect(lambda: self.saveRequested.emit(self))
        tl.addWidget(save)
        root.addWidget(top)
        self.warn = QLabel()
        self.warn.setContentsMargins(18, 0, 18, 4)
        self.warn.setWordWrap(True)
        self.warn.hide()
        root.addWidget(self.warn)
        self.content = QVBoxLayout()
        self.content.setContentsMargins(18, 0, 18, 12)
        self.content.setSpacing(8)
        self._build(self.content)
        root.addLayout(self.content, 1)
        self.load(self.req)
        self.mark_saved()
        self._loading = False

    # subclasses
    def _build(self, layout):
        raise NotImplementedError

    def load(self, req):
        raise NotImplementedError

    def collect(self):
        raise NotImplementedError

    def primary_action(self):
        raise NotImplementedError

    def shutdown(self):
        """Called when the tab closes: drop connections."""

    # ---- state shared with RequestView ----
    def _resolve(self, text):
        return variables.substitute(text, self.values_provider(self.collection_id))

    def mark_saved(self):
        self._saved = json.dumps(self.collect(), sort_keys=True)
        self.update_crumb()

    def is_dirty(self):
        return json.dumps(self.collect(), sort_keys=True) != self._saved

    def title(self):
        return self.req.get("name") or "Untitled"

    def update_crumb(self):
        dirty = self.is_dirty() if self._saved is not None else False
        head = f"{self.collection_name} / " if self.collection_name else "Unsaved / "
        folder = self.req.get("folder", "")
        if folder and self.collection_name:
            head += folder.replace("/", " / ") + " / "
        dot = f" <span style='color:{theme.ACCENT}'>•</span>" if dirty else ""
        self.crumb.setText(f"{head}<b style='color:{theme.TEXT}'>{_esc(self.title())}</b>{dot}")

    def rename(self):
        name, ok = QInputDialog.getText(self, "Rename", "Name", text=self.title())
        if ok and name.strip():
            self.req["name"] = name.strip()
            self._edited()

    def set_collection(self, cid, name):
        self.collection_id, self.collection_name = cid, name
        self.update_crumb()

    def refresh_variables(self):
        pass

    def _check_variables(self):
        pass

    def _split_query_from_url(self):
        pass

    def focus_url(self):
        w = getattr(self, "url_edit", None)
        if w is not None:
            w.setFocus()

    def _edited(self, *_):
        if self._loading:
            return
        self.update_crumb()
        self.changed.emit(self)

    def show_error(self, text, ok=False):
        self.warn.setStyleSheet(f"color: {theme.GREEN if ok else theme.RED}; font-size: 8pt;")
        self.warn.setText(text)
        self.warn.setVisible(bool(text))

    def _tabs(self, titles):
        stack = QStackedWidget()
        bar = QTabBar()
        bar.setDrawBase(False)
        bar.setExpanding(False)
        for t in titles:
            bar.addTab(t)
        bar.currentChanged.connect(stack.setCurrentIndex)
        return bar, stack


class MessageLog(QWidget):
    """Timestamped, colour-coded message list with a filter and Copy / Clear."""
    ARROWS = {"sent": "↑", "recv": "↓", "info": "•", "error": "!"}

    @staticmethod
    def _color(kind):
        return {"sent": theme.ACCENT, "recv": theme.GREEN, "info": theme.MUTED, "error": theme.RED}[kind]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.entries = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        row = QHBoxLayout()
        title = QLabel("Messages")
        title.setStyleSheet("font-weight: 600;")
        self.count = QLabel("")
        self.count.setObjectName("muted")
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter messages")
        self.filter.setClearButtonEnabled(True)
        self.filter.setFixedWidth(200)
        self.filter.textChanged.connect(self._render)
        copy_btn = QPushButton("Copy")
        copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(
            "\n".join(f"[{t}] {d} {x}" for t, d, x, _k in self.entries)))
        clear = QPushButton("Clear")
        clear.clicked.connect(self.clear)
        row.addWidget(title)
        row.addWidget(self.count)
        row.addStretch(1)
        row.addWidget(self.filter)
        row.addWidget(copy_btn)
        row.addWidget(clear)
        lay.addLayout(row)
        self.view = QTextBrowser()
        self.view.setObjectName("code")
        self.view.setFont(theme.mono(9))
        self.view.setOpenLinks(False)
        lay.addWidget(self.view, 1)

    def add(self, kind, text):
        entry = (time.strftime("%H:%M:%S"), self.ARROWS[kind], text, kind)
        self.entries.append(entry)
        if len(self.entries) > 5000:
            del self.entries[:500]
            self._render()
            return
        if self._match(entry):
            self.view.append(self._html(entry))
            self.view.verticalScrollBar().setValue(self.view.verticalScrollBar().maximum())
        self._count()

    def clear(self):
        self.entries.clear()
        self.view.clear()
        self._count()

    def _match(self, entry):
        f = self.filter.text().lower()
        return not f or f in entry[2].lower()

    def _html(self, e):
        t, arrow, text, kind = e
        c = self._color(kind)
        return (f"<span style='color:{theme.FAINT}'>{t}</span> <b style='color:{c}'>{arrow}</b> "
                f"<span style='color:{c if kind in ('error', 'info') else theme.TEXT}; white-space: pre-wrap'>"
                f"{html.escape(text)}</span>")

    def _render(self, *_):
        self.view.clear()
        for e in self.entries:
            if self._match(e):
                self.view.append(self._html(e))
        self._count()

    def _count(self):
        sent = sum(1 for e in self.entries if e[3] == "sent")
        recv = sum(1 for e in self.entries if e[3] == "recv")
        self.count.setText(f"{sent} sent · {recv} received" if self.entries else "")


class StreamView(ProtoBase):
    """WebSocket, SSE and MQTT: connect, watch messages, send / publish."""
    logSignal = Signal(str, str)
    stateSignal = Signal(str)

    def __init__(self, request, collection_id="", values_provider=None, settings_provider=None, parent=None):
        self.kind = request.get("kind", "websocket")
        self.client = None
        self._worker = None
        self._state = "disconnected"
        super().__init__(request, collection_id, values_provider, settings_provider, parent)
        self.logSignal.connect(self.log.add)
        self.stateSignal.connect(self._set_state)
        self._set_state("disconnected")

    # ---- UI ----
    def _build(self, lay):
        bar = QFrame()
        bar.setObjectName("urlbar")
        bar.setStyleSheet(f"QFrame#urlbar {{ background: {theme.FIELD}; border: 1px solid {theme.BORDER}; border-radius: 8px; }}"
                          f"QFrame#urlbar QWidget {{ background: transparent; }}")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(0)
        self.method = QComboBox()
        self.method.setObjectName("methodBox")
        self.method.addItems(["GET", "POST"])
        self.method.setFixedHeight(40)
        self.method.currentTextChanged.connect(self._edited)
        self.url_edit = QLineEdit()
        self.url_edit.setObjectName("urlEdit")
        self.url_edit.setFixedHeight(40)
        self.url_edit.textEdited.connect(self._edited)
        self.url_edit.returnPressed.connect(self.primary_action)
        self.port_edit = QLineEdit()
        self.port_edit.setObjectName("urlEdit")
        self.port_edit.setFixedSize(80, 40)
        self.port_edit.setPlaceholderText("1883")
        self.port_edit.textEdited.connect(self._edited)
        self.tls = QCheckBox("TLS")
        self.tls.toggled.connect(self._edited)
        self.connect_btn = primary(QPushButton("Connect"), "border-top-left-radius: 0; border-bottom-left-radius: 0;")
        self.connect_btn.setFixedSize(110, 40)
        self.connect_btn.clicked.connect(self.primary_action)
        if self.kind == "sse":
            bl.addWidget(self.method)
        else:
            lead = QLabel(store.badge(self.req))
            lead.setStyleSheet(f"color: {theme.method_color(store.badge(self.req))}; font-weight: 700; "
                               f"font-family: '{theme.mono_family()}'; padding: 0 16px;")
            bl.addWidget(lead)
        bl.addWidget(self.url_edit, 1)
        if self.kind == "mqtt":
            bl.addWidget(self.port_edit)
            bl.addWidget(self.tls)
            bl.addSpacing(10)
        bl.addWidget(self.connect_btn)
        lay.addWidget(bar)
        self.url_edit.setPlaceholderText({"websocket": "wss://echo.websocket.org  (use {{variables}})",
                                          "sse": "https://example.com/events",
                                          "mqtt": "broker.example.com"}[self.kind])
        self.status = QLabel("Disconnected")
        self.status.setObjectName("muted")
        lay.addWidget(self.status)

        split = QSplitter(Qt.Vertical)
        split.setChildrenCollapsible(False)
        split.addWidget(self._build_options())
        self.log = MessageLog()
        split.addWidget(self.log)
        split.setSizes([260, 320])
        lay.addWidget(split, 1)

    def _build_options(self):
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        titles = {"websocket": ["Message", "Headers", "Settings"], "sse": ["Headers", "Body"],
                  "mqtt": ["Publish", "Subscribe", "Connection"]}[self.kind]
        bar, stack = self._tabs(titles)
        self.headers = KVTable(key_hint="Header", value_hint="Value")
        self.headers.changed.connect(self._edited)
        if self.kind == "websocket":
            stack.addWidget(self._ws_message())
            stack.addWidget(self.headers)
            stack.addWidget(self._ws_settings())
        elif self.kind == "sse":
            stack.addWidget(self.headers)
            self.sse_body = CodeEditor()
            self.sse_body.setPlaceholderText("Request body (only sent when the method is POST)")
            self.sse_body.textChanged.connect(self._edited)
            stack.addWidget(self.sse_body)
        else:
            stack.addWidget(self._mqtt_publish())
            stack.addWidget(self._mqtt_subscribe())
            stack.addWidget(self._mqtt_connection())
        lay.addWidget(bar)
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background: {theme.BORDER};")
        lay.addWidget(line)
        lay.addWidget(stack, 1)
        return box

    def _ws_message(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 8, 0, 0)
        row = QHBoxLayout()
        row.addWidget(QLabel("Send as"))
        self.send_as = QComboBox()
        self.send_as.addItems(["Text", "JSON", "Binary (hex)"])
        self.send_as.currentIndexChanged.connect(self._edited)
        row.addWidget(self.send_as)
        row.addStretch(1)
        self.send_btn = primary(QPushButton("Send"))
        self.send_btn.clicked.connect(self._ws_send)
        row.addWidget(self.send_btn)
        lay.addLayout(row)
        self.message = CodeEditor()
        self.message.setPlaceholderText("Message to send. Ctrl+Enter connects or sends.")
        self.message.textChanged.connect(self._edited)
        lay.addWidget(self.message, 1)
        return w

    def _ws_settings(self):
        w = QWidget()
        form = QFormLayout(w)
        form.setContentsMargins(0, 12, 0, 0)
        self.subprotocols = QLineEdit()
        self.subprotocols.setPlaceholderText("comma separated, e.g. graphql-ws, chat")
        self.subprotocols.textEdited.connect(self._edited)
        form.addRow("Subprotocols", self.subprotocols)
        return w

    def _mqtt_publish(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 8, 0, 0)
        row = QHBoxLayout()
        self.pub_topic = QLineEdit()
        self.pub_topic.setPlaceholderText("Topic, e.g. home/livingroom/light")
        self.pub_topic.textEdited.connect(self._edited)
        self.pub_qos = QComboBox()
        self.pub_qos.addItems(["QoS 0", "QoS 1", "QoS 2"])
        self.pub_qos.currentIndexChanged.connect(self._edited)
        self.retain = QCheckBox("Retain")
        self.retain.toggled.connect(self._edited)
        self.send_btn = primary(QPushButton("Publish"))
        self.send_btn.clicked.connect(self._mqtt_publish_now)
        row.addWidget(self.pub_topic, 1)
        row.addWidget(self.pub_qos)
        row.addWidget(self.retain)
        row.addWidget(self.send_btn)
        lay.addLayout(row)
        self.message = CodeEditor()
        self.message.setPlaceholderText("Payload")
        self.message.textChanged.connect(self._edited)
        lay.addWidget(self.message, 1)
        return w

    def _mqtt_subscribe(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 8, 0, 0)
        row = QHBoxLayout()
        self.sub_topic = QLineEdit()
        self.sub_topic.setPlaceholderText("Topic filter, e.g. home/# or sensors/+/temp")
        self.sub_topic.textEdited.connect(self._edited)
        self.sub_qos = QComboBox()
        self.sub_qos.addItems(["QoS 0", "QoS 1", "QoS 2"])
        self.sub_qos.currentIndexChanged.connect(self._edited)
        sub = primary(QPushButton("Subscribe"))
        sub.clicked.connect(self._mqtt_subscribe_now)
        unsub = QPushButton("Unsubscribe")
        unsub.clicked.connect(self._mqtt_unsubscribe_now)
        self.sub_buttons = (sub, unsub)
        for x in (self.sub_topic, self.sub_qos, sub, unsub):
            row.addWidget(x, 1 if x is self.sub_topic else 0)
        lay.addLayout(row)
        self.subs_label = QLabel("Active subscriptions: none")
        self.subs_label.setObjectName("muted")
        self.subs_label.setWordWrap(True)
        lay.addWidget(self.subs_label)
        lay.addStretch(1)
        self.active_subs = []
        return w

    def _mqtt_connection(self):
        w = QWidget()
        form = QFormLayout(w)
        form.setContentsMargins(0, 12, 0, 0)
        form.setSpacing(10)
        self.client_id = QLineEdit()
        self.client_id.setPlaceholderText("generated when empty")
        self.username = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.keepalive = QLineEdit()
        self.keepalive.setPlaceholderText("30")
        for wdg in (self.client_id, self.username, self.password, self.keepalive):
            wdg.textEdited.connect(self._edited)
        form.addRow("Client ID", self.client_id)
        form.addRow("Username", self.username)
        form.addRow("Password", self.password)
        form.addRow("Keep alive (s)", self.keepalive)
        return w

    # ---- load / collect ----
    def load(self, req):
        self._loading = True
        self.req = store.normalize_request(req)
        p = self.req["proto"]
        self.headers.set_rows(self.req["headers"])
        if self.kind == "websocket":
            self.url_edit.setText(self.req["url"])
            self.message.setPlainText(p.get("message", ""))
            self.send_as.setCurrentText(p.get("send_as", "Text"))
            self.subprotocols.setText(p.get("subprotocols", ""))
        elif self.kind == "sse":
            self.url_edit.setText(self.req["url"])
            self.method.setCurrentText(self.req["method"] if self.req["method"] in ("GET", "POST") else "GET")
            self.sse_body.setPlainText(self.req["body"]["raw"])
        else:
            self.url_edit.setText(p.get("host", ""))
            self.port_edit.setText(p.get("port", "1883"))
            self.tls.setChecked(bool(p.get("tls")))
            self.pub_topic.setText(p.get("pub_topic", ""))
            self.pub_qos.setCurrentIndex(int(p.get("pub_qos", 0)))
            self.retain.setChecked(bool(p.get("retain")))
            self.message.setPlainText(p.get("payload", ""))
            self.sub_topic.setText(p.get("subscribe", "#"))
            self.sub_qos.setCurrentIndex(int(p.get("sub_qos", 0)))
            self.client_id.setText(p.get("client_id", ""))
            self.username.setText(p.get("username", ""))
            self.password.setText(p.get("password", ""))
            self.keepalive.setText(p.get("keepalive", "30"))
        self._loading = False

    def collect(self):
        r = copy.deepcopy(self.req)
        r["headers"] = self.headers.rows()
        if self.kind == "websocket":
            r["url"] = self.url_edit.text()
            r["proto"] = {"message": self.message.toPlainText(), "send_as": self.send_as.currentText(),
                          "subprotocols": self.subprotocols.text()}
        elif self.kind == "sse":
            r["url"] = self.url_edit.text()
            r["method"] = self.method.currentText()
            r["body"] = {**r["body"], "mode": "raw", "raw": self.sse_body.toPlainText()}
        else:
            r["proto"] = {"host": self.url_edit.text(), "port": self.port_edit.text(), "tls": self.tls.isChecked(),
                          "client_id": self.client_id.text(), "username": self.username.text(),
                          "password": self.password.text(), "keepalive": self.keepalive.text(),
                          "subscribe": self.sub_topic.text(), "sub_qos": self.sub_qos.currentIndex(),
                          "pub_topic": self.pub_topic.text(), "pub_qos": self.pub_qos.currentIndex(),
                          "retain": self.retain.isChecked(), "payload": self.message.toPlainText()}
            r["url"] = f"mqtt://{self.url_edit.text()}:{self.port_edit.text() or '1883'}"
        self.req = r
        return r

    # ---- connection state ----
    def primary_action(self):
        if self._state == "disconnected":
            self.connect_now()
        elif self._state == "connected":
            if self.kind == "websocket":
                self._ws_send()
            elif self.kind == "mqtt":
                self._mqtt_publish_now()
            else:
                self.disconnect_now()
        # while connecting, a second press does nothing

    def _set_state(self, state):
        self._state = state
        names = {"disconnected": "Disconnected", "connecting": "Connecting…", "connected": "Connected"}
        color = {"disconnected": theme.MUTED, "connecting": theme.ACCENT, "connected": theme.GREEN}[state]
        self.status.setText(names[state])
        self.status.setStyleSheet(f"color: {color};")
        self.connect_btn.setText("Disconnect" if state == "connected" else "Connect")
        self.connect_btn.setEnabled(state != "connecting")
        live = state == "connected"
        if hasattr(self, "send_btn"):
            self.send_btn.setEnabled(live)
        if hasattr(self, "sub_buttons"):
            for b in self.sub_buttons:
                b.setEnabled(live)
        if not live and self.kind == "mqtt":
            self.active_subs = []
            self._refresh_subs()

    def connect_now(self):
        if self._state != "disconnected":
            return
        self.show_error("")
        self.collect()
        opts = self.settings_provider()
        headers = [(self._resolve(h["key"]), self._resolve(h["value"])) for h in self.req["headers"]
                   if h["enabled"] and h["key"]]
        url = self._resolve(self.req["url"] if self.kind != "mqtt" else self.url_edit.text()).strip()
        if not url:
            self.show_error("Enter the address first.")
            return
        self.stateSignal.emit("connecting")
        self.log.add("info", f"Connecting to {url} …")
        log = self.logSignal.emit
        p = self.req["proto"]
        if self.kind == "websocket":
            subs = [s.strip() for s in p.get("subprotocols", "").split(",") if s.strip()]
            client = protocols.WebSocketClient(url, headers, opts.get("verify_ssl", True), subs,
                                               on_message=self._ws_message_in, on_event=self._ws_event)
            work = lambda: client.connect(15)             # noqa: E731
        elif self.kind == "sse":
            body = self._resolve(self.req["body"]["raw"]) if self.method.currentText() == "POST" else None
            client = protocols.SseClient(url, self.method.currentText(), headers, body.encode() if body else None,
                                         opts.get("verify_ssl", True), on_event=self._sse_in, on_status=self._sse_status)
            work = lambda: client.start(15)               # noqa: E731
        else:
            try:
                port = int(self._resolve(p.get("port", "1883")) or 1883)
                keep = int(self._resolve(p.get("keepalive", "30")) or 30)
            except ValueError:
                self.stateSignal.emit("disconnected")
                self.show_error("Port and keep alive must be numbers.")
                return
            client = protocols.MqttClient(url, port, self._resolve(p.get("client_id", "")),
                                          self._resolve(p.get("username", "")), self._resolve(p.get("password", "")),
                                          keep, bool(p.get("tls")), opts.get("verify_ssl", True),
                                          on_message=self._mqtt_in, on_event=self._mqtt_event)
            work = lambda: client.connect(15)             # noqa: E731
        self.client = client
        self._worker = Worker(work, self)
        self._worker.done.connect(self._connect_done)
        self._worker.start()

    def _connect_done(self, _result, error):
        if error:
            self.client = None
            self.stateSignal.emit("disconnected")
            self.log.add("error", error)
            self.show_error(error)
        else:
            self.stateSignal.emit("connected")
            if self.kind == "mqtt":
                topic = self.sub_topic.text().strip()
                if topic and not self._loading:
                    pass      # subscriptions are explicit: press Subscribe

    def disconnect_now(self):
        c, self.client = self.client, None
        if c is None:
            return
        if self.kind == "websocket":
            c.close()
        elif self.kind == "sse":
            c.stop()
        else:
            c.disconnect()
        self.stateSignal.emit("disconnected")
        self.log.add("info", "Disconnected.")

    def shutdown(self):
        if self.client is not None:
            self.disconnect_now()

    # ---- callbacks (run on client threads; they only emit signals) ----
    def _ws_message_in(self, kind, data):
        if kind == "text":
            self.logSignal.emit("recv", data)
        else:
            self.logSignal.emit("recv", f"[binary {len(data)} B] " + data.hex(" "))

    def _ws_event(self, kind, text):
        if kind == "connected":
            self.logSignal.emit("info", "Connected. " + text)
        elif kind in ("closed", "error"):
            self.logSignal.emit("error" if kind == "error" else "info", text)
            self.client = None
            self.stateSignal.emit("disconnected")
        elif kind == "ping":
            self.logSignal.emit("info", f"ping {text}")
        elif kind == "pong":
            self.logSignal.emit("info", f"pong {text}")

    def _sse_in(self, ev):
        head = ev["event"] + (f" #{ev['id']}" if ev["id"] else "")
        self.logSignal.emit("recv", f"[{head}] {ev['data']}")

    def _sse_status(self, kind, text):
        if kind == "connected":
            self.logSignal.emit("info", "Connected. " + text)
        else:
            self.logSignal.emit("info", text)
            self.client = None
            self.stateSignal.emit("disconnected")

    def _mqtt_in(self, topic, payload, qos, retain):
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            text = f"[binary {len(payload)} B] " + payload.hex(" ")
        self.logSignal.emit("recv", f"{topic}  (QoS {qos}{', retained' if retain else ''})\n{text}")

    def _mqtt_event(self, kind, text):
        if kind in ("closed", "error"):
            self.logSignal.emit("error" if kind == "error" else "info", text)
            self.client = None
            self.stateSignal.emit("disconnected")
        else:
            self.logSignal.emit("info", text)

    # ---- sending ----
    def _ws_send(self):
        if self._state != "connected" or self.client is None:
            self.connect_now()
            return
        text = self._resolve(self.message.toPlainText())
        mode = self.send_as.currentText()
        try:
            if mode.startswith("Binary"):
                data = bytes.fromhex("".join(text.split()))
                self.client.send_binary(data)
                self.log.add("sent", f"[binary {len(data)} B] " + data.hex(" "))
            else:
                if mode == "JSON":
                    pretty = pretty_json(text)
                    if pretty is None:
                        self.show_error("The message is not valid JSON.")
                        return
                self.client.send_text(text)
                self.log.add("sent", text)
            self.show_error("")
        except ValueError:
            self.show_error("Binary messages are hexadecimal bytes, e.g. de ad be ef.")
        except (OSError, ConnectionError) as e:
            self.show_error(str(e))

    def _mqtt_publish_now(self):
        if self.client is None or self._state != "connected":
            return
        topic = self._resolve(self.pub_topic.text()).strip()
        if not topic:
            self.show_error("Enter a topic to publish to.")
            return
        payload = self._resolve(self.message.toPlainText())
        try:
            self.client.publish(topic, payload, self.pub_qos.currentIndex(), self.retain.isChecked())
            self.log.add("sent", f"{topic}  (QoS {self.pub_qos.currentIndex()}"
                                 f"{', retain' if self.retain.isChecked() else ''})\n{payload}")
            self.show_error("")
        except OSError as e:
            self.show_error(str(e))

    def _mqtt_subscribe_now(self):
        topic = self._resolve(self.sub_topic.text()).strip()
        if self.client is None or not topic:
            return
        try:
            self.client.subscribe(topic, self.sub_qos.currentIndex())
        except OSError as e:
            self.show_error(str(e))
            return
        if topic not in self.active_subs:
            self.active_subs.append(topic)
        self._refresh_subs()
        self.log.add("info", f"Subscribing to {topic} …")

    def _mqtt_unsubscribe_now(self):
        topic = self._resolve(self.sub_topic.text()).strip()
        if self.client is None or topic not in self.active_subs:
            return
        try:
            self.client.unsubscribe(topic)
        except OSError as e:
            self.show_error(str(e))
            return
        self.active_subs.remove(topic)
        self._refresh_subs()

    def _refresh_subs(self):
        if hasattr(self, "subs_label"):
            self.subs_label.setText("Active subscriptions: " + (", ".join(self.active_subs) or "none"))


class GrpcView(ProtoBase):
    """gRPC through server reflection: load services, pick a method, send JSON, read JSON."""

    def __init__(self, request, collection_id="", values_provider=None, settings_provider=None, parent=None):
        self.catalog = None
        self._worker = None
        super().__init__(request, collection_id, values_provider, settings_provider, parent)

    def _build(self, lay):
        bar = QFrame()
        bar.setObjectName("urlbar")
        bar.setStyleSheet(f"QFrame#urlbar {{ background: {theme.FIELD}; border: 1px solid {theme.BORDER}; border-radius: 8px; }}"
                          f"QFrame#urlbar QWidget {{ background: transparent; }}")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(0)
        lead = QLabel("gRPC")
        lead.setStyleSheet(f"color: {theme.method_color('GRPC')}; font-weight: 700; "
                           f"font-family: '{theme.mono_family()}'; padding: 0 16px;")
        self.url_edit = QLineEdit()
        self.url_edit.setObjectName("urlEdit")
        self.url_edit.setFixedHeight(40)
        self.url_edit.setPlaceholderText("localhost:50051")
        self.url_edit.textEdited.connect(self._target_changed)
        self.tls = QCheckBox("TLS")
        self.tls.toggled.connect(self._edited)
        self.load_btn = QPushButton("Load services")
        self.load_btn.setToolTip("Ask the server which services it offers (needs server reflection)")
        self.load_btn.clicked.connect(self.load_services)
        self.invoke_btn = primary(QPushButton("Invoke"), "border-top-left-radius: 0; border-bottom-left-radius: 0;")
        self.invoke_btn.setFixedSize(100, 40)
        self.invoke_btn.clicked.connect(self.invoke)
        bl.addWidget(lead)
        bl.addWidget(self.url_edit, 1)
        bl.addWidget(self.tls)
        bl.addSpacing(8)
        bl.addWidget(self.load_btn)
        bl.addSpacing(8)
        bl.addWidget(self.invoke_btn)
        lay.addWidget(bar)

        row = QHBoxLayout()
        self.service = QComboBox()
        self.service.setMinimumWidth(240)
        self.service.currentIndexChanged.connect(self._service_changed)
        self.method = QComboBox()
        self.method.setMinimumWidth(180)
        self.method.currentIndexChanged.connect(self._method_changed)
        self.kind_label = QLabel("")
        self.kind_label.setObjectName("muted")
        row.addWidget(QLabel("Service"))
        row.addWidget(self.service)
        row.addWidget(QLabel("Method"))
        row.addWidget(self.method)
        row.addWidget(self.kind_label)
        row.addStretch(1)
        lay.addLayout(row)
        self.hint = QLabel("Load the server's services to pick a method, or just press Invoke.")
        self.hint.setObjectName("muted")
        lay.addWidget(self.hint)

        split = QSplitter(Qt.Vertical)
        split.setChildrenCollapsible(False)
        # request side
        rbox = QWidget()
        rl = QVBoxLayout(rbox)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        bar_, stack = self._tabs(["Message", "Metadata"])
        msg = QWidget()
        ml = QVBoxLayout(msg)
        ml.setContentsMargins(0, 8, 0, 0)
        top = QHBoxLayout()
        self.stream_note = QLabel("")
        self.stream_note.setObjectName("muted")
        sample = flat(QPushButton("Generate example"))
        sample.clicked.connect(self._sample)
        top.addWidget(self.stream_note, 1)
        top.addWidget(sample)
        ml.addLayout(top)
        self.message = CodeEditor()
        self.message.set_language("json")
        self.message.textChanged.connect(self._edited)
        ml.addWidget(self.message, 1)
        stack.addWidget(msg)
        self.headers = KVTable(key_hint="Metadata key", value_hint="Value")
        self.headers.changed.connect(self._edited)
        stack.addWidget(self.headers)
        rl.addWidget(bar_)
        ln = QFrame()
        ln.setFixedHeight(1)
        ln.setStyleSheet(f"background: {theme.BORDER};")
        rl.addWidget(ln)
        rl.addWidget(stack, 1)
        split.addWidget(rbox)
        # response side
        pbox = QWidget()
        pl = QVBoxLayout(pbox)
        pl.setContentsMargins(0, 8, 0, 0)
        head = QHBoxLayout()
        title = QLabel("Response")
        title.setStyleSheet("font-weight: 600;")
        self.pill = QLabel()
        self.pill.hide()
        self.time_lb = QLabel("")
        self.time_lb.setObjectName("muted")
        copy_btn = QPushButton("Copy")
        copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(self.response.toPlainText()))
        head.addWidget(title)
        head.addWidget(self.pill)
        head.addWidget(self.time_lb)
        head.addStretch(1)
        head.addWidget(copy_btn)
        pl.addLayout(head)
        self.response = CodeEditor(read_only=True)
        self.response.set_language("json")
        pl.addWidget(self.response, 2)
        self.trailers = QTableWidget(0, 2)
        self.trailers.setHorizontalHeaderLabels(["Trailer", "Value"])
        self.trailers.horizontalHeader().setStretchLastSection(True)
        self.trailers.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.trailers.verticalHeader().setVisible(False)
        self.trailers.setEditTriggers(QTableWidget.NoEditTriggers)
        self.trailers.setShowGrid(False)
        self.trailers.setMaximumHeight(110)
        self.trailers.hide()
        pl.addWidget(self.trailers)
        split.addWidget(pbox)
        split.setSizes([260, 300])
        lay.addWidget(split, 1)

    # ---- load / collect ----
    def load(self, req):
        self._loading = True
        self.req = store.normalize_request(req)
        p = self.req["proto"]
        self.url_edit.setText(p.get("target", "localhost:50051"))
        self.tls.setChecked(bool(p.get("tls")))
        self.headers.set_rows(self.req["headers"])
        self.message.setPlainText(p.get("message", "{}"))
        self.service.clear()
        self.method.clear()
        if p.get("service"):
            self.service.addItem(p["service"])
        if p.get("method"):
            self.method.addItem(p["method"])
        self._loading = False

    def collect(self):
        r = copy.deepcopy(self.req)
        r["headers"] = self.headers.rows()
        r["proto"] = {"target": self.url_edit.text(), "tls": self.tls.isChecked(),
                      "service": self.service.currentText(), "method": self.method.currentText(),
                      "message": self.message.toPlainText()}
        r["url"] = "grpc://" + self.url_edit.text()
        self.req = r
        return r

    def primary_action(self):
        self.invoke()

    # ---- catalog ----
    def _target_changed(self, *_):
        self.catalog = None
        self._edited()

    def _resolved_target(self):
        return self._resolve(self.url_edit.text()).strip()

    def _metadata(self):
        return [(self._resolve(h["key"]), self._resolve(h["value"])) for h in self.headers.rows()
                if h["enabled"] and h["key"]]

    def load_services(self):
        if self._busy():
            return
        if not grpc_client.available():
            self.show_error("gRPC support is not installed (grpcio, grpcio-reflection, protobuf).")
            return
        target, tls, md = self._resolved_target(), self.tls.isChecked(), self._metadata()
        self.load_btn.setEnabled(False)
        self.hint.setText("Loading services…")
        self.show_error("")
        self._worker = Worker(lambda: grpc_client.list_services(target, tls, md, 10), self)
        self._worker.done.connect(self._services_loaded)
        self._worker.start()

    def _services_loaded(self, catalog, error):
        self.load_btn.setEnabled(True)
        if error:
            self.hint.setText("")
            self.show_error(error)
            return
        self._apply_catalog(catalog)

    def _apply_catalog(self, catalog):
        self.catalog = catalog
        want_s, want_m = self.req["proto"].get("service", ""), self.req["proto"].get("method", "")
        self._loading = True
        self.service.clear()
        self.service.addItems(list(catalog.services))
        if want_s in catalog.services:
            self.service.setCurrentText(want_s)
        self._fill_methods(want_m)
        self._loading = False
        n = sum(len(v) for v in catalog.services.values())
        self.hint.setText(f"{len(catalog.services)} service(s), {n} method(s).")
        self._edited()

    def _fill_methods(self, keep=""):
        self.method.clear()
        for m in self.catalog.services.get(self.service.currentText(), []):
            self.method.addItem(m.name)
        if keep:
            i = self.method.findText(keep)
            if i >= 0:
                self.method.setCurrentIndex(i)
        self._method_changed()

    def _service_changed(self, *_):
        if self.catalog is not None and not self._loading:
            self._fill_methods()
        self._edited()

    def _method_changed(self, *_):
        if self.catalog is None or not self.method.currentText():
            self.kind_label.setText("")
            self.stream_note.setText("")
            return
        try:
            info = self.catalog.method(self.service.currentText(), self.method.currentText())
        except grpc_client.GrpcError:
            return
        self.kind_label.setText(f"({info.kind})")
        self.stream_note.setText("Client streaming: send a JSON array, one element per message."
                                 if info.client_streaming else f"Request type: {info.input_type}")
        self._edited()

    def _sample(self):
        if self.catalog is None:
            self.show_error("Load the services first.")
            return
        try:
            self.message.setPlainText(grpc_client.sample_request(self.catalog, self.service.currentText(),
                                                                 self.method.currentText()))
        except grpc_client.GrpcError as e:
            self.show_error(str(e))

    def _busy(self):
        return self._worker is not None and self._worker.isRunning()

    # ---- invoke ----
    def invoke(self):
        if self._busy():
            return
        if not grpc_client.available():
            self.show_error("gRPC support is not installed (grpcio, grpcio-reflection, protobuf).")
            return
        service, method = self.service.currentText(), self.method.currentText()
        target, tls, md = self._resolved_target(), self.tls.isChecked(), self._metadata()
        body = self._resolve(self.message.toPlainText())
        catalog = self.catalog
        if not target:
            self.show_error("Enter the server address.")
            return
        self.show_error("")
        self.invoke_btn.setEnabled(False)
        self.pill.hide()
        self.time_lb.setText("")
        self.response.setPlainText("")

        def work():
            cat = catalog or grpc_client.list_services(target, tls, md, 10)
            s, m = service, method
            if not s or not m or s not in cat.services:
                if len(cat.services) == 1 and not s:
                    s = next(iter(cat.services))
                if not m:
                    raise grpc_client.GrpcError("Pick a service and a method first (Load services).")
            return cat, grpc_client.invoke(cat, target, s, m, body, tls, md, 30)
        self._worker = Worker(work, self)
        self._worker.done.connect(self._invoked)
        self._worker.start()

    def _invoked(self, result, error):
        self.invoke_btn.setEnabled(True)
        if error:
            self.show_error(error)
            return
        cat, res = result
        if self.catalog is None:
            self._apply_catalog(cat)
        color = theme.GREEN if res.ok else theme.RED
        self.pill.setText(res.code if res.ok else f"{res.code}")
        self.pill.setStyleSheet(f"color: {color}; border: 1px solid {color}; border-radius: 5px; "
                                "padding: 2px 9px; font-weight: 600;")
        self.pill.show()
        self.time_lb.setText(f"{res.elapsed_ms} ms")
        if res.ok:
            if len(res.messages) == 1:
                text = res.messages[0]
            else:
                text = "[\n" + ",\n".join(res.messages) + "\n]"
        else:
            text = json.dumps({"code": res.code, "details": res.details}, indent=2)
        self.response.setPlainText(text)
        self.trailers.setRowCount(len(res.trailers))
        for i, (k, v) in enumerate(res.trailers):
            self.trailers.setItem(i, 0, QTableWidgetItem(k))
            self.trailers.setItem(i, 1, QTableWidgetItem(v))
        self.trailers.setVisible(bool(res.trailers))
