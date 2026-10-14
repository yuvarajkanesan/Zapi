"""The Environments screen: Globals + named environments with Initial / Current values and secrets."""
import copy
import json

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QFileDialog, QFrame, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QTableWidget,
                               QVBoxLayout, QWidget)

from . import importers, store, theme
from .widgets import flat, primary


class EnvTable(QTableWidget):
    changed = Signal()

    HEADERS = ["", "Variable", "Initial value", "Current value", "Secret"]

    def __init__(self, parent=None):
        super().__init__(0, 5, parent)
        self.setHorizontalHeaderLabels(self.HEADERS)
        h = self.horizontalHeader()
        h.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        h.setSectionResizeMode(0, QHeaderView.Fixed)
        for i in (1, 2, 3):
            h.setSectionResizeMode(i, QHeaderView.Stretch)
        h.setSectionResizeMode(4, QHeaderView.Fixed)
        self.setColumnWidth(0, 44)
        self.setColumnWidth(4, 70)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(44)
        self.setShowGrid(False)
        self.setSelectionMode(QTableWidget.NoSelection)
        self.setEditTriggers(QTableWidget.NoEditTriggers)
        self._loading = False
        self.set_vars([])

    def set_vars(self, vars_):
        self._loading = True
        self.setRowCount(0)
        for v in vars_:
            self._append(v)
        self._append(None)
        self._loading = False

    def vars(self):
        out = []
        for i in range(self.rowCount()):
            key = self.cellWidget(i, 1).text().strip()
            ini, cur = self.cellWidget(i, 2).text(), self.cellWidget(i, 3).text()
            if not key and not ini and not cur:
                continue
            out.append({"key": key, "initial": ini, "current": cur,
                        "secret": self._check(i, 4).isChecked(), "enabled": self._check(i, 0).isChecked()})
        return out

    def _check(self, row, col):
        return self.cellWidget(row, col).findChild(QCheckBox)

    def _cell_check(self, checked):
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setAlignment(Qt.AlignCenter)
        cb = QCheckBox()
        cb.setChecked(checked)
        lay.addWidget(cb)
        return w, cb

    def _append(self, v):
        i = self.rowCount()
        self.insertRow(i)
        w, enabled = self._cell_check(True if v is None else v.get("enabled", True))
        self.setCellWidget(i, 0, w)
        key = QLineEdit(v["key"] if v else "")
        key.setPlaceholderText("Add a variable")
        ini = QLineEdit(v["initial"] if v else "")
        ini.setPlaceholderText("Initial value")
        cur = QLineEdit(v["current"] if v else "")
        cur.setPlaceholderText("Current value")
        if v and v["initial"] != v["current"]:
            cur.setProperty("linked", False)
        for col, e in ((1, key), (2, ini), (3, cur)):
            e.setStyleSheet(f"QLineEdit {{ font-family: '{theme.mono_family()}'; margin: 4px 3px; }}")
            self.setCellWidget(i, col, e)
            e.textEdited.connect(lambda *_, ed=e: self._edited(ed))
        sw, secret = self._cell_check(bool(v and v.get("secret")))
        self.setCellWidget(i, 4, sw)

        def mask(on):
            mode = QLineEdit.Password if on else QLineEdit.Normal
            ini.setEchoMode(mode)
            cur.setEchoMode(mode)
        secret.toggled.connect(lambda on: (mask(on), self._edited(None)))
        enabled.toggled.connect(lambda *_: self._edited(None))
        mask(secret.isChecked())

    def _edited(self, _editor):
        if self._loading:
            return
        # typing an initial value into a row whose current value is untouched keeps them together
        sender = _editor
        if sender is not None:
            for i in range(self.rowCount()):
                if self.cellWidget(i, 2) is sender and self.cellWidget(i, 3).property("linked") is not False:
                    self.cellWidget(i, 3).setText(sender.text())
                elif self.cellWidget(i, 3) is sender:
                    sender.setProperty("linked", False)
        last = self.rowCount() - 1
        if any(self.cellWidget(last, c).text() for c in (1, 2, 3)):
            self._loading = True
            self._append(None)
            self._loading = False
        self.changed.emit()


class EnvironmentsPage(QWidget):
    back = Signal()
    workspaceChanged = Signal()

    def __init__(self, ws, parent=None):
        super().__init__(parent)
        self.ws = ws
        self.current = None           # "globals" or an environment id
        self._dirty = False
        self._build()
        self.refresh_list()

    def _build(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        side = QWidget()
        side.setObjectName("sidebar")
        side.setFixedWidth(260)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(16, 16, 16, 12)
        sl.setSpacing(12)
        head = QHBoxLayout()
        back = flat(QPushButton("‹"))
        back.setToolTip("Back to requests")
        back.setFixedWidth(28)
        back.clicked.connect(self._back)
        title = QLabel("Environments")
        title.setStyleSheet("font-size: 11pt; font-weight: 700;")
        new = primary(QPushButton("+ New"))
        new.clicked.connect(self.new_environment)
        head.addWidget(back)
        head.addWidget(title, 1)
        head.addWidget(new)
        sl.addLayout(head)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search")
        self.search.textChanged.connect(self.refresh_list)
        sl.addWidget(self.search)
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._selected)
        sl.addWidget(self.list, 1)
        brow = QHBoxLayout()
        imp = QPushButton("Import…")
        imp.clicked.connect(self.import_environment)
        exp_all = QPushButton("Export all…")
        exp_all.clicked.connect(self.export_all)
        brow.addWidget(imp, 1)
        brow.addWidget(exp_all, 1)
        sl.addLayout(brow)
        root.addWidget(side)

        main = QWidget()
        ml = QVBoxLayout(main)
        ml.setContentsMargins(28, 22, 28, 18)
        ml.setSpacing(14)
        top = QHBoxLayout()
        left = QVBoxLayout()
        left.setSpacing(2)
        self.name_edit = QLineEdit()
        self.name_edit.setStyleSheet(f"font-size: 15pt; font-weight: 700; color: {theme.TEXT}; border: none; "
                                     "background: transparent; padding: 0;")
        self.name_edit.textEdited.connect(self._mark_dirty)
        sub = QLabel("Variables are available in URLs, headers, params and bodies as double-brace names.")
        sub.setObjectName("muted")
        sub.setWordWrap(True)
        left.addWidget(self.name_edit)
        left.addWidget(sub)
        top.addLayout(left, 1)
        self.active_btn = QPushButton("Set active")
        self.active_btn.clicked.connect(self.set_active)
        self.dup_btn = QPushButton("Duplicate")
        self.dup_btn.clicked.connect(self.duplicate)
        self.del_btn = QPushButton("Delete")
        self.del_btn.setProperty("danger", True)
        self.del_btn.clicked.connect(self.delete)
        self.export_btn = QPushButton("Export")
        self.export_btn.clicked.connect(self.export)
        self.save_btn = primary(QPushButton("Save changes"))
        self.save_btn.clicked.connect(self.save)
        for b in (self.active_btn, self.export_btn, self.dup_btn, self.del_btn, self.save_btn):
            top.addWidget(b)
        ml.addLayout(top)

        frame = QFrame()
        frame.setStyleSheet(f"QFrame#tableFrame {{ background: {theme.PANEL}; border: 1px solid {theme.BORDER}; border-radius: 10px; }}")
        frame.setObjectName("tableFrame")
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(1, 1, 1, 1)
        self.table = EnvTable()
        self.table.changed.connect(self._mark_dirty)
        fl.addWidget(self.table)
        ml.addWidget(frame, 1)

        for html in ("<b>Initial value</b> is what gets exported and shared with a collection.",
                     "<b>Current value</b> stays on this computer and is what requests use when you send them."):
            lb = QLabel(html)
            lb.setTextFormat(Qt.RichText)
            lb.setStyleSheet(f"color: {theme.MUTED};")
            ml.addWidget(lb)
        root.addWidget(main, 1)

    # ---- list ----------------------------------------------------------------------
    def refresh_list(self, *_):
        q = self.search.text().strip().lower()
        keep = self.current
        self.list.blockSignals(True)
        self.list.clear()
        entries = [("globals", "Globals", None)] + [(e["id"], e["name"], e) for e in self.ws.environments]
        active = self.ws.settings.get("active_env", "")
        for eid, name, _e in entries:
            if q and q not in name.lower():
                continue
            it = QListWidgetItem(name)
            it.setData(Qt.UserRole, eid)
            it.setData(Qt.UserRole + 1, name)
            if eid == "globals":
                it.setIcon(theme.icon("globe", theme.MUTED, 16))
            elif eid == active:
                it.setText(name + "      Active")
                it.setIcon(theme.dot_icon(theme.GREEN, 8))
            else:
                it.setIcon(theme.dot_icon(theme.FAINT, 8))
            self.list.addItem(it)
        self.list.blockSignals(False)
        target = keep if keep and self._find_row(keep) >= 0 else (active if self._find_row(active) >= 0 else "globals")
        row = self._find_row(target)
        if row >= 0:
            self.list.setCurrentRow(row)
            self._load(target)

    def _find_row(self, eid):
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.UserRole) == eid:
                return i
        return -1

    def _selected(self, item, _prev):
        if item is None:
            return
        eid = item.data(Qt.UserRole)
        if eid == self.current:
            return
        if not self._confirm_leave():
            self.list.blockSignals(True)
            self.list.setCurrentRow(self._find_row(self.current))
            self.list.blockSignals(False)
            return
        self._load(eid)

    def _env(self):
        return self.ws.globals if self.current == "globals" else self.ws.environment(self.current)

    def _load(self, eid):
        self.current = eid
        env = self._env()
        if env is None:
            return
        self.name_edit.setText(env["name"])
        self.name_edit.setReadOnly(eid == "globals")
        self.table.set_vars(env["vars"])
        is_g = eid == "globals"
        self.dup_btn.setVisible(not is_g)
        self.del_btn.setVisible(not is_g)
        self.active_btn.setVisible(not is_g)
        self.export_btn.setVisible(True)
        self._set_dirty(False)
        self._refresh_active_button()

    def _refresh_active_button(self):
        is_active = self.current == self.ws.settings.get("active_env")
        self.active_btn.setText("Active" if is_active else "Set active")
        self.active_btn.setEnabled(not is_active)

    def _mark_dirty(self, *_):
        self._set_dirty(True)

    def _set_dirty(self, d):
        self._dirty = d
        self.save_btn.setEnabled(d)

    def _confirm_leave(self):
        if not self._dirty:
            return True
        r = QMessageBox.question(self, "Unsaved changes", "Save your changes to this environment first?",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Cancel:
            return False
        if r == QMessageBox.Save:
            self.save()
        return True

    def _back(self):
        if self._confirm_leave():
            self.back.emit()

    # ---- actions -------------------------------------------------------------------
    def save(self):
        env = self._env()
        if env is None:
            return
        if self.current != "globals":
            name = self.name_edit.text().strip() or env["name"]
            env["name"] = name
        env["vars"] = [v for v in self.table.vars() if v["key"]]
        self.ws.save()
        self._set_dirty(False)
        self.refresh_list()
        self.workspaceChanged.emit()

    def new_environment(self):
        if not self._confirm_leave():
            return
        name, ok = QInputDialog.getText(self, "New environment", "Name", text="New environment")
        if ok and name.strip():
            e = self.ws.add_environment(name.strip())
            self.ws.save()
            self.current = e["id"]
            self.refresh_list()
            self.workspaceChanged.emit()

    def duplicate(self):
        if self.current == "globals" or not self._confirm_leave():
            return
        d = self.ws.duplicate_environment(self.current)
        self.ws.save()
        self.current = d["id"]
        self.refresh_list()
        self.workspaceChanged.emit()

    def delete(self):
        env = self._env()
        if self.current == "globals" or env is None:
            return
        if QMessageBox.question(self, "Delete environment", f"Delete “{env['name']}” and its variables?") != QMessageBox.Yes:
            return
        self.ws.delete_environment(self.current)
        self.ws.save()
        self.current = "globals"
        self._dirty = False
        self.refresh_list()
        self.workspaceChanged.emit()

    def set_active(self):
        if self.current == "globals":
            return
        self.ws.settings["active_env"] = self.current
        self.ws.save()
        self.refresh_list()
        self.workspaceChanged.emit()

    def export(self):
        env = self._env()
        path, _ = QFileDialog.getSaveFileName(self, "Export environment", f"{env['name']}.zapi-environment.json",
                                              "JSON (*.json)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.ws.export_environment(env), f, indent=2)

    def import_environment(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Import environments", "", "JSON (*.json)")
        added, errors = [], []
        for path in paths:
            try:
                kind, obj = importers.import_file(path)
            except (OSError, importers.ImportError_) as e:
                errors.append(f"{path}: {e}")
                continue
            if kind == "environment":
                added.append(obj)
            elif kind == "environments":
                added.extend(obj)
            else:
                errors.append(f"{path}: that file is not an environment (use Import in the Collections tab).")
        if errors:
            QMessageBox.warning(self, "Some files were skipped", "\n".join(errors))
        if not added:
            return
        self.ws.environments.extend(added)
        self.ws.save()
        self.current = added[-1]["id"]
        self.refresh_list()
        self.workspaceChanged.emit()

    def export_all(self):
        if not self.ws.environments:
            QMessageBox.information(self, "Export", "There are no environments to export yet.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export all environments", "zapi-environments.json",
                                              "JSON (*.json)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.ws.export_all_environments(), f, indent=2, ensure_ascii=False)
