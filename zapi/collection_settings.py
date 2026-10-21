"""Collection and folder settings: the authorization, headers and scripts that their requests inherit."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QStackedWidget,
                               QTabWidget, QVBoxLayout, QWidget)

from . import store, variable_assist
from .request_view import AUTH_FORMS, AUTH_NOTES, AUTH_TYPES
from .widgets import CodeEditor, KVEditor, KVTable, primary


class AuthForm(QWidget):
    """The same authorization types as a request, for a collection or a folder."""

    def __init__(self, allow_inherit, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 8, 0, 0)
        lay.setSpacing(10)
        self.type = QComboBox()
        for key, title in AUTH_TYPES:
            if key == "inherit" and not allow_inherit:
                continue
            self.type.addItem("Inherit from parent" if key == "inherit" else title, key)
        lay.addWidget(self.type)
        self.stack = QStackedWidget()
        self.widgets = {}                          # (type, field) -> widget
        self._pages = {}
        for i in range(self.type.count()):
            key = self.type.itemData(i)
            page = QWidget()
            pl = QVBoxLayout(page)
            pl.setContentsMargins(0, 0, 0, 0)
            form = QFormLayout()
            form.setContentsMargins(0, 0, 0, 0)
            form.setSpacing(10)
            for k, text, hint, kind in AUTH_FORMS.get(key) or []:
                if isinstance(kind, list):
                    w = QComboBox()
                    for val, label in kind:
                        w.addItem(label, val)
                else:
                    w = QLineEdit()
                    w.setPlaceholderText(hint)
                    if kind == "secret":
                        w.setEchoMode(QLineEdit.Password)
                self.widgets[(key, k)] = w
                form.addRow(QLabel(text), w)
            pl.addLayout(form)
            note = ""
            if key == "inherit":
                note = "Uses the authorization of the folder or collection above."
            elif key == "none":
                note = ("No authorization is added here. Requests that inherit send none."
                        if not allow_inherit else "Requests in this folder send no authorization, whatever the collection sets.")
            elif key in AUTH_NOTES:
                note = AUTH_NOTES[key]
            if key == "oauth2":
                note += "\nPaste an access token here, or use {{variables}}. To fetch a token, open a request and use Get new access token."
            if note:
                lb = QLabel(note.strip())
                lb.setObjectName("muted")
                lb.setWordWrap(True)
                pl.addWidget(lb)
            pl.addStretch(1)
            self.stack.addWidget(page)
            self._pages[key] = i
        lay.addWidget(self.stack, 1)
        self.type.currentIndexChanged.connect(self.stack.setCurrentIndex)

    def set_auth(self, auth):
        a = {**store.AUTH_DEFAULTS, **(auth or {})}
        i = self.type.findData(a.get("type", "none"))
        self.type.setCurrentIndex(max(0, i))
        for (key, k), w in self.widgets.items():
            if isinstance(w, QComboBox):
                w.setCurrentIndex(max(0, w.findData(a.get(k, ""))))
            else:
                w.setText(a.get(k, ""))

    def get_auth(self):
        t = self.type.currentData()
        a = dict(store.AUTH_DEFAULTS)
        a["type"] = t
        for (key, k), w in self.widgets.items():
            if key == t:
                a[k] = w.currentData() if isinstance(w, QComboBox) else w.text()
        return a


class CollectionSettingsDialog(QDialog):
    def __init__(self, ws, collection_id, folder="", parent=None):
        super().__init__(parent)
        self.ws = ws
        self.coll = ws.collection(collection_id)
        self.folder = (folder or "").strip("/")
        self.target = self.coll if not self.folder else (self.coll.setdefault("folder_settings", {}).get(self.folder) or {})
        self.setWindowTitle(("Folder settings" if self.folder else "Collection settings") +
                            f" - {self.coll['name']}" + (f" / {self.folder}" if self.folder else ""))
        self.resize(720, 560)
        lay = QVBoxLayout(self)
        what = f"folder “{self.folder}”" if self.folder else f"collection “{self.coll['name']}”"
        intro = QLabel(f"Set once for the {what}; requests inside inherit it. A request can still set its own "
                       "authorization or header, and a folder overrides its collection.")
        intro.setWordWrap(True)
        intro.setObjectName("muted")
        lay.addWidget(intro)
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs, 1)
        self.auth = AuthForm(allow_inherit=bool(self.folder))
        self.auth.set_auth(self.target.get("auth") or ({"type": "inherit"} if self.folder else {"type": "none"}))
        self.tabs.addTab(self.auth, "Authorization")
        hw = QWidget()
        hl = QVBoxLayout(hw)
        hl.setContentsMargins(0, 8, 0, 0)
        hint = QLabel("Sent with every request here. A header on the request itself replaces one with the same name.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        hl.addWidget(hint)
        self.headers = KVEditor(KVTable(key_hint="Header", value_hint="Value"), "header")
        self.headers.set_rows(self.target.get("headers") or [])
        hl.addWidget(self.headers, 1)
        self.tabs.addTab(hw, "Headers")
        self.pre = self._script("Runs before each request's own pre-request script.", "pre")
        self.test = self._script("Runs before each request's own post-response script (after the response arrives).", "test")
        self.tabs.addTab(self.pre[0], "Pre-request")
        self.tabs.addTab(self.test[0], "Post-response")
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        primary(bb.button(QDialogButtonBox.Save))
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        variable_assist.attach_all(self, skip=(self.pre[1], self.test[1]))

    def _script(self, hint, which):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 8, 0, 0)
        lb = QLabel(hint)
        lb.setObjectName("muted")
        lay.addWidget(lb)
        ed = CodeEditor()
        ed.set_language("js")
        ed.setPlainText((self.target.get("scripts") or {}).get(which, ""))
        lay.addWidget(ed, 1)
        return w, ed

    def _save(self):
        auth = self.auth.get_auth()
        headers = [h for h in self.headers.rows() if h.get("key") or h.get("value")]
        scripts = {"pre": self.pre[1].toPlainText(), "test": self.test[1].toPlainText()}
        if not self.folder:
            self.coll["auth"], self.coll["headers"], self.coll["scripts"] = auth, headers, scripts
        else:
            s = {}
            if auth["type"] != "inherit":
                s["auth"] = auth
            if headers:
                s["headers"] = headers
            if scripts["pre"].strip() or scripts["test"].strip():
                s["scripts"] = scripts
            fs = self.coll.setdefault("folder_settings", {})
            if s:
                fs[self.folder] = s
            else:
                fs.pop(self.folder, None)                  # nothing set: keep the data tidy
        self.ws.save()
        self.accept()

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(ev)

    @staticmethod
    def summary(coll, folder=""):
        """Short text for menus and tooltips: what is set here."""
        t = coll if not folder else (coll.get("folder_settings") or {}).get(folder) or {}
        parts = []
        if (t.get("auth") or {}).get("type") not in (None, "", "none", "inherit"):
            parts.append("auth")
        if t.get("headers"):
            parts.append(f"{len(t['headers'])} header(s)")
        if any(((t.get("scripts") or {}).get(k) or "").strip() for k in ("pre", "test")):
            parts.append("scripts")
        return ", ".join(parts)

