"""Reusable widgets: code editor with line numbers, JSON/XML colouring, key/value tables."""
import json
import os
import re

from PySide6.QtCore import QRect, QRegularExpression, QSize, QThread, Qt, Signal
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QKeySequence, QPainter, QSyntaxHighlighter, QTextCharFormat,
                           QTextFormat)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QPlainTextEdit, QPushButton, QStackedWidget, QTableWidget, QTextEdit,
                               QToolButton, QVBoxLayout, QWidget)

from . import store, theme, variable_assist


def _fmt(color, bold=False):
    f = QTextCharFormat()
    f.setForeground(QColor(color))
    if bold:
        f.setFontWeight(QFont.Bold)
    return f


class JsonHighlighter(QSyntaxHighlighter):
    """Keys light blue, strings green, numbers amber, literals purple."""

    RULES = None

    def __init__(self, doc):
        super().__init__(doc)
        self.string = _fmt(theme.SYN_STRING)
        self.key = _fmt(theme.SYN_KEY)
        self.number = _fmt(theme.SYN_NUMBER)
        self.literal = _fmt(theme.SYN_LITERAL)
        self.punct = _fmt(theme.MUTED)
        self.var = _fmt(theme.ACCENT, True)

    def highlightBlock(self, text):
        for m in re.finditer(r'"(?:\\.|[^"\\])*"', text):
            tail = text[m.end():].lstrip()
            self.setFormat(m.start(), m.end() - m.start(), self.key if tail.startswith(":") else self.string)
        for m in re.finditer(r'(?<![\w"])-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?(?![\w"])', text):
            if not self._inside_string(text, m.start()):
                self.setFormat(m.start(), m.end() - m.start(), self.number)
        for m in re.finditer(r"\b(?:true|false|null)\b", text):
            if not self._inside_string(text, m.start()):
                self.setFormat(m.start(), m.end() - m.start(), self.literal)
        for m in re.finditer(r"\{\{[^{}]+\}\}", text):
            self.setFormat(m.start(), m.end() - m.start(), self.var)

    @staticmethod
    def _inside_string(text, pos):
        return text[:pos].replace('\\"', "").count('"') % 2 == 1


class MarkupHighlighter(QSyntaxHighlighter):
    def __init__(self, doc):
        super().__init__(doc)
        self.tag = _fmt(theme.SYN_KEY)
        self.attr = _fmt(theme.SYN_NUMBER)
        self.value = _fmt(theme.SYN_STRING)
        self.comment = _fmt(theme.FAINT)
        self.var = _fmt(theme.ACCENT, True)

    def highlightBlock(self, text):
        for m in re.finditer(r"</?[\w:.-]+|/?>", text):
            self.setFormat(m.start(), m.end() - m.start(), self.tag)
        for m in re.finditer(r'([\w:.-]+)=("[^"]*"|\'[^\']*\')', text):
            self.setFormat(m.start(1), m.end(1) - m.start(1), self.attr)
            self.setFormat(m.start(2), m.end(2) - m.start(2), self.value)
        for m in re.finditer(r"<!--.*?-->", text):
            self.setFormat(m.start(), m.end() - m.start(), self.comment)
        for m in re.finditer(r"\{\{[^{}]+\}\}", text):
            self.setFormat(m.start(), m.end() - m.start(), self.var)


class VarHighlighter(QSyntaxHighlighter):
    """Only paints {{variables}}; used for plain text bodies."""

    def __init__(self, doc):
        super().__init__(doc)
        self.var = _fmt(theme.ACCENT, True)

    def highlightBlock(self, text):
        for m in re.finditer(r"\{\{[^{}]+\}\}", text):
            self.setFormat(m.start(), m.end() - m.start(), self.var)


class JsHighlighter(QSyntaxHighlighter):
    KEYWORDS = ("var let const function return if else for while do switch case break continue new try catch finally "
                "throw typeof instanceof in of this null true false undefined async await class").split()

    def __init__(self, doc):
        super().__init__(doc)
        self.kw = _fmt(theme.SYN_LITERAL, True)
        self.string = _fmt(theme.SYN_STRING)
        self.number = _fmt(theme.SYN_NUMBER)
        self.comment = _fmt(theme.FAINT)
        self.api = _fmt(theme.SYN_KEY)
        self._kw_re = re.compile(r"\b(?:%s)\b" % "|".join(self.KEYWORDS))
        self._api_re = re.compile(r"\b(?:pm|postman|console|CryptoJS)\b(?:\.\w+)*")
        self._num_re = re.compile(r"\b\d+(?:\.\d+)?\b")
        self._str_re = re.compile(r"\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|`[^`]*`")
        self._cmt_re = re.compile(r"//.*$")

    def highlightBlock(self, text):
        for rx, fmt in ((self._kw_re, self.kw), (self._api_re, self.api), (self._num_re, self.number),
                        (self._str_re, self.string), (self._cmt_re, self.comment)):
            for m in rx.finditer(text):
                self.setFormat(m.start(), m.end() - m.start(), fmt)


class Worker(QThread):
    """Runs `fn()` off the UI thread; emits done(result, error_text)."""
    done = Signal(object, str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self.fn = fn

    def run(self):
        try:
            self.done.emit(self.fn(), "")
        except Exception as e:  # noqa: BLE001 - shown to the user as text
            self.done.emit(None, str(e) or e.__class__.__name__)


class _Gutter(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self):
        return QSize(self.editor.gutter_width(), 0)

    def paintEvent(self, event):
        self.editor.paint_gutter(event)


class CodeEditor(QPlainTextEdit):
    """Monospace editor with a line-number gutter."""

    def __init__(self, read_only=False, parent=None):
        super().__init__(parent)
        self.setObjectName("code")
        self.setFont(theme.mono(theme.CODE_SIZE))
        self.setReadOnly(read_only)
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.setTabStopDistance(QFontMetrics(self.font()).horizontalAdvance(" ") * 2)
        self._gutter = _Gutter(self)
        self._hl = None
        self.blockCountChanged.connect(self._update_margin)
        self.updateRequest.connect(self._scroll_gutter)
        self.cursorPositionChanged.connect(self._highlight_line)
        self._update_margin()
        self._highlight_line()

    def set_language(self, lang):
        if self._hl is not None:
            self._hl.setDocument(None)
            self._hl = None
        lang = lang.lower()
        if lang.startswith("soap"):
            lang = "xml"
        cls = {"json": JsonHighlighter, "xml": MarkupHighlighter, "html": MarkupHighlighter, "js": JsHighlighter,
               "text": VarHighlighter, "javascript": VarHighlighter}.get(lang)
        if cls:
            self._hl = cls(self.document())

    def gutter_width(self):
        digits = max(2, len(str(max(1, self.blockCount()))))
        return 20 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_margin(self):
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)

    def _scroll_gutter(self, rect, dy):
        if dy:
            self._gutter.scroll(0, dy)
        else:
            self._gutter.update(0, rect.y(), self._gutter.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_margin()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        cr = self.contentsRect()
        self._gutter.setGeometry(QRect(cr.left(), cr.top(), self.gutter_width(), cr.height()))

    def paint_gutter(self, event):
        p = QPainter(self._gutter)
        p.fillRect(event.rect(), QColor(theme.FIELD))
        p.setFont(self.font())
        block = self.firstVisibleBlock()
        n = block.blockNumber()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())
        current = self.textCursor().blockNumber()
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                p.setPen(QColor(theme.MUTED if n == current else theme.FAINT))
                p.drawText(0, top, self._gutter.width() - 10, self.fontMetrics().height(), Qt.AlignRight,
                           str(n + 1))
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            n += 1

    def _highlight_line(self):
        if self.isReadOnly():
            self.setExtraSelections([])
            return
        sel = QTextEdit.ExtraSelection()
        sel.format.setBackground(QColor(theme.LINE_HL))
        sel.format.setProperty(QTextFormat.FullWidthSelection, True)
        sel.cursor = self.textCursor()
        sel.cursor.clearSelection()
        self.setExtraSelections([sel])

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Tab and not self.isReadOnly():
            self.insertPlainText("  ")
            return
        super().keyPressEvent(e)


def pretty_json(text):
    """Indented JSON, or None when `text` is not JSON."""
    try:
        return json.dumps(json.loads(text), indent=2, ensure_ascii=False)
    except ValueError:
        return None


def pretty_xml(text):
    try:
        from xml.dom import minidom
        out = minidom.parseString(text.encode("utf-8")).toprettyxml(indent="  ")
        return "\n".join(l for l in out.splitlines() if l.strip())
    except Exception:  # noqa: BLE001
        return None


# ---- key / value table ----------------------------------------------------------
class KVTable(QTableWidget):
    """Rows of [enabled] key value [type] with an always-present empty row at the bottom."""

    changed = Signal()

    def __init__(self, with_type=False, key_hint="Key", value_hint="Value", parent=None, variables=True):
        super().__init__(0, 5 if with_type else 4, parent)
        self.variables = variables           # hover / completion for {{variables}} in the cells
        self.with_type = with_type
        self.key_hint, self.value_hint = key_hint, value_hint
        self._loading = False
        self.setHorizontalHeaderLabels(([""] + [key_hint, value_hint] + (["Type"] if with_type else []) + [""]))
        h = self.horizontalHeader()
        h.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        h.setSectionResizeMode(0, QHeaderView.Fixed)
        h.setSectionResizeMode(1, QHeaderView.Stretch)
        h.setSectionResizeMode(2, QHeaderView.Stretch)
        last = self.columnCount() - 1
        if with_type:
            h.setSectionResizeMode(3, QHeaderView.Fixed)
            self.setColumnWidth(3, 90)
        h.setSectionResizeMode(last, QHeaderView.Fixed)
        self.setColumnWidth(0, 44)
        self.setColumnWidth(last, 40)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(34)
        self.setShowGrid(False)
        self.setSelectionMode(QAbstractItemView.NoSelection)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.set_rows([])

    # ---- model ----
    def set_rows(self, rows):
        self._loading = True
        self.setRowCount(0)
        for r in rows:
            self._append(r)
        self._append(None)
        self._loading = False

    def rows(self):
        out = []
        for i in range(self.rowCount()):
            key = self._edit(i, 1).text()
            val = self._edit(i, 2).text()
            if key == "" and val == "":
                continue
            row = store.kv(key, val, self._check(i).isChecked())
            extra = self._edit(i, 1).property("extra")        # e.g. a form field's content_type / filename from a cURL import
            if isinstance(extra, dict):
                row.update(extra)
            if self.with_type:
                row["type"] = self._type(i).currentText()
            out.append(row)
        return out

    def _edit(self, row, col):
        return self.cellWidget(row, col)

    def _check(self, row):
        return self.cellWidget(row, 0).findChild(QCheckBox)

    def _type(self, row):
        return self.cellWidget(row, 3)

    def _append(self, r):
        i = self.rowCount()
        self.insertRow(i)
        box = QWidget()
        box.setStyleSheet("background: transparent;")
        lay = QHBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setAlignment(Qt.AlignCenter)
        cb = QCheckBox()
        cb.setChecked(True if r is None else r.get("enabled", True))
        lay.addWidget(cb)
        self.setCellWidget(i, 0, box)
        key = QLineEdit(r["key"] if r else "")
        key.setPlaceholderText(self.key_hint)
        if r:
            extra = {k: v for k, v in r.items() if k not in ("key", "value", "enabled", "type") and isinstance(v, str)}
            if extra:
                key.setProperty("extra", extra)
        val = QLineEdit(r.get("value", "") if r else "")
        val.setPlaceholderText(self.value_hint)
        self.setCellWidget(i, 1, key)
        self.setCellWidget(i, 2, val)
        if self.variables:
            variable_assist.attach(key)
            variable_assist.attach(val)
        if self.with_type:
            combo = QComboBox()
            combo.addItems(["text", "file"])
            if r and r.get("type") == "file":
                combo.setCurrentText("file")
            self.setCellWidget(i, 3, combo)
            choose = val.addAction(theme.icon("folder", theme.MUTED, 16), QLineEdit.TrailingPosition)
            choose.setToolTip("Choose a file to upload")
            val.setProperty("chooseAction", choose)

            def sync(*_, val=val, combo=combo, choose=choose):
                is_file = combo.currentText() == "file"
                choose.setVisible(is_file)
                val.setPlaceholderText("Choose a file…" if is_file else self.value_hint)

            def pick(*_, val=val):
                start = val.text() if os.path.isfile(val.text()) else os.path.expanduser("~")
                path, _ = QFileDialog.getOpenFileName(self, "Choose a file to upload", start)
                if path:
                    val.setText(path)
                    val.setCursorPosition(0)
                    self._edited()
                return bool(path)
            choose.triggered.connect(pick)
            combo.currentIndexChanged.connect(sync)
            combo.activated.connect(lambda *_: combo.currentText() == "file" and not val.text() and pick())   # picked by the user
            combo.currentIndexChanged.connect(lambda *_: self._edited())
            sync()
        rm = QToolButton()
        rm.setIcon(theme.icon("close", theme.MUTED, 14))
        rm.setToolTip("Remove row")
        rm.clicked.connect(lambda _=False, w=key: self._remove(w))
        self.setCellWidget(i, self.columnCount() - 1, rm)
        for w in (key, val):
            w.textEdited.connect(lambda *_: self._edited())
        cb.toggled.connect(lambda *_: self._edited())

    def _remove(self, key_widget):
        for i in range(self.rowCount()):
            if self.cellWidget(i, 1) is key_widget and i != self.rowCount() - 1:
                self.removeRow(i)
                self.changed.emit()
                return

    def _edited(self):
        if self._loading:
            return
        last = self.rowCount() - 1
        if last < 0 or self._edit(last, 1).text() or self._edit(last, 2).text():
            self._loading = True
            self._append(None)
            self._loading = False
        self.changed.emit()

    def sizeHint(self):
        return QSize(400, 200)


# ---- bulk edit: the same rows as plain text ----------------------------------------------------------------
def format_rows(rows, style="header"):
    """Rows as text, one per line: `Key: Value` for headers, `key=value` for query / form parameters.
    A disabled row starts with //."""
    sep = ": " if style == "header" else "="
    out = []
    for r in rows:
        line = f"{r['key']}{sep}{r.get('value', '')}"
        out.append(line if r.get("enabled", True) else "// " + line)
    return "\n".join(out)


def parse_rows(text, style="header"):
    """The inverse of format_rows. Forgiving: blank lines are skipped, a line without a separator is a key with an empty
    value, `Key:Value` works for parameters too, and HTTP/2 pseudo headers (:authority: x) are ignored."""
    rows = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        enabled = True
        if line.startswith("//"):
            enabled, line = False, line[2:].strip()
            if not line:
                continue
        if line.startswith(":"):                             # :authority: example.com
            continue
        if style == "header":
            cut = line.find(":")
        else:
            eq, colon = line.find("="), line.find(":")
            cut = eq if eq >= 0 else colon
        key, value = (line[:cut], line[cut + 1:]) if cut > 0 else (line, "")
        if key.strip():
            rows.append(store.kv(key.strip(), value.strip(), enabled))
    return rows


class KVEditor(QWidget):
    """A KVTable with a Bulk Edit switch that shows the same rows as text. Drop-in for KVTable (rows / set_rows / changed)."""

    changed = Signal()

    def __init__(self, table, style="header", parent=None):
        super().__init__(parent)
        self.table = table
        self.style = style
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 4, 0, 0)
        self.hint = QLabel("")
        self.hint.setObjectName("muted")
        self.toggle = flat(QPushButton("Bulk Edit"))
        self.toggle.setToolTip("Edit all rows as text: paste a block of headers or parameters")
        self.toggle.clicked.connect(lambda: self.set_bulk(not self.bulk_active))
        bar.addWidget(self.hint, 1)
        bar.addWidget(self.toggle)
        lay.addLayout(bar)
        self.stack = QStackedWidget()
        self.stack.addWidget(table)
        self.text = None                      # the text editor is created the first time Bulk Edit is used: a request
        lay.addWidget(self.stack, 1)          # tab with three tables would otherwise carry three extra code editors
        table.changed.connect(self.changed)
        self._syncing = False
        self._update_hint()

    def _ensure_text(self):
        if self.text is None:
            self.text = CodeEditor()
            self.text.set_language("text")
            variable_assist.attach(self.text)
            self.text.setPlaceholderText(
                "Accept: application/json\nAuthorization: Bearer {{token}}\n// X-Disabled: off" if self.style == "header"
                else "page=1\nlimit=20\n// debug=true")
            self.stack.addWidget(self.text)
            self.text.textChanged.connect(self._text_changed)
        return self.text

    @property
    def bulk_active(self):
        return self.stack.currentIndex() == 1

    def _update_hint(self):
        self.hint.setText("" if not self.bulk_active else
                          ("One per line as Key: Value. Start a line with // to turn it off." if self.style == "header"
                           else "One per line as key=value. Start a line with // to turn it off."))
        self.toggle.setText("Key-Value Edit" if self.bulk_active else "Bulk Edit")

    def set_bulk(self, on):
        if on == self.bulk_active:
            return
        self._syncing = True
        try:
            if on:
                self._ensure_text().setPlainText(format_rows(self.table.rows(), self.style))
            else:
                self.table.set_rows(parse_rows(self.text.toPlainText(), self.style))
        finally:
            self._syncing = False
        self.stack.setCurrentIndex(1 if on else 0)
        self._update_hint()
        if not on:
            self.changed.emit()

    def _text_changed(self):
        if not self._syncing:
            self.changed.emit()

    def rows(self):
        return parse_rows(self.text.toPlainText(), self.style) if self.bulk_active else self.table.rows()

    def set_rows(self, rows):
        self.table.set_rows(rows)
        if self.bulk_active:
            self._syncing = True
            self.text.setPlainText(format_rows(self.table.rows(), self.style))
            self._syncing = False


class CopyTable(QTableWidget):
    """Read-only table whose rows can be copied: Ctrl+C, or right-click for value / row / everything."""

    copied = Signal(str)

    def __init__(self, rows=0, cols=0, parent=None):
        super().__init__(rows, cols, parent)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)

    def _cell(self, row, col):
        item = self.item(row, col)
        return item.text() if item is not None else ""

    def row_text(self, row, sep="\t"):
        return sep.join(self._cell(row, c) for c in range(self.columnCount()))

    def selected_rows(self):
        rows = sorted({i.row() for i in self.selectedIndexes()})
        return rows or ([self.currentRow()] if self.currentRow() >= 0 else [])

    def _copy(self, text, what):
        if text:
            QApplication.clipboard().setText(text)
            self.copied.emit(f"Copied {what}")

    def copy_selection(self):
        rows = self.selected_rows()
        self._copy("\n".join(self.row_text(r) for r in rows), f"{len(rows)} row(s)")

    def keyPressEvent(self, e):
        if e.matches(QKeySequence.Copy):
            self.copy_selection()
            return
        super().keyPressEvent(e)

    def _menu(self, pos):
        from PySide6.QtWidgets import QMenu
        idx = self.indexAt(pos)
        m = QMenu(self)
        if idx.isValid():
            m.addAction("Copy value", lambda: self._copy(self._cell(idx.row(), idx.column()), "value"))
            m.addAction("Copy row", lambda: self._copy(self.row_text(idx.row()), "row"))
            if self.columnCount() == 2:
                m.addAction("Copy as “name: value”", lambda: self._copy(self.row_text(idx.row(), ": "), "row"))
        m.addAction("Copy all rows", lambda: self._copy(
            "\n".join(self.row_text(r) for r in range(self.rowCount())), f"{self.rowCount()} row(s)"))
        if self.columnCount() == 2:
            m.addAction("Copy all as “name: value” lines", lambda: self._copy(
                "\n".join(self.row_text(r, ": ") for r in range(self.rowCount())), "all rows"))
        m.exec(self.viewport().mapToGlobal(pos))


def copy_feedback(button, label="Copied", ms=1500):
    """After a copy: the button turns green with a tick and 'Copied', then goes back. Safe to call again while it shows."""
    import shiboken6
    from PySide6.QtCore import QPropertyAnimation, QTimer
    from PySide6.QtWidgets import QGraphicsOpacityEffect
    if button is None or not shiboken6.isValid(button):
        return
    saved = button.property("_copy_saved")
    if saved is None:                                           # first call: remember how the button looked
        saved = (button.text(), button.icon(), button.styleSheet(), button.minimumWidth())
        button.setProperty("_copy_saved", saved)
        button.setMinimumWidth(max(button.width(), button.sizeHint().width()))      # no jumping around
    button.setText("  " + label)
    button.setIcon(theme.icon("check", theme.GREEN, 14))
    button.setStyleSheet(f"QPushButton {{ color: {theme.GREEN}; border: 1px solid {theme.GREEN}; }}")
    fx = QGraphicsOpacityEffect(button)                         # a short pop so it is noticed
    button.setGraphicsEffect(fx)
    anim = QPropertyAnimation(fx, b"opacity", button)
    anim.setDuration(260)
    anim.setStartValue(0.35)
    anim.setEndValue(1.0)
    anim.finished.connect(lambda: shiboken6.isValid(button) and button.setGraphicsEffect(None))
    anim.start(QPropertyAnimation.DeleteWhenStopped)
    old = button.property("_copy_timer")
    if old is not None and shiboken6.isValid(old):
        old.stop()
    timer = QTimer(button)
    timer.setSingleShot(True)

    def restore():
        s = button.property("_copy_saved")
        if s is not None:
            button.setText(s[0])
            button.setIcon(s[1])
            button.setStyleSheet(s[2])
            button.setMinimumWidth(s[3])
            button.setProperty("_copy_saved", None)
    timer.timeout.connect(restore)
    button.setProperty("_copy_timer", timer)
    timer.start(ms)


def copy_text(text, button=None):
    """Put text on the clipboard and show the tick on `button`."""
    QApplication.clipboard().setText(text)
    copy_feedback(button)


def primary(button: QPushButton, radius=""):
    """Amber call-to-action. It carries its own stylesheet because container rules such as
    `#sidebar QWidget {background: transparent}` would otherwise blank it."""
    button.setProperty("primary", True)
    button.setStyleSheet(
        f"QPushButton {{ background: {theme.ACCENT}; color: {theme.ACCENT_TEXT}; border: 1px solid {theme.ACCENT};"
        f" font-weight: 600; {radius} }}"
        f"QPushButton:hover {{ background: {theme.ACCENT_HOVER}; border-color: {theme.ACCENT_HOVER}; }}"
        f"QPushButton:disabled {{ background: {theme.ACCENT_DIS}; border-color: {theme.ACCENT_DIS}; color: {theme.ACCENT_DIS_TEXT}; }}")
    return button


def flat(button: QPushButton):
    button.setProperty("flat", True)
    button.setCursor(Qt.PointingHandCursor)
    return button
