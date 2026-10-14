"""Variable help inside every text field that accepts {{variables}}:

* hover a {{name}} to see its value and where it comes from (secrets are masked, undefined ones say so);
* type {{ to get a list of the existing variables, narrowed as you type (Up / Down, Enter or Tab to accept, Esc to close);
* Ctrl+Space lists the variables anywhere in a field.

`set_provider()` tells it where the variables come from; `attach(widget)` switches it on for a QLineEdit or a QPlainTextEdit.
The catalog is {name: {"value", "scope", "secret", "dynamic"}} (see Workspace.variable_catalog).
"""
import html
import re

import shiboken6
from PySide6.QtCore import QEvent, QObject, QPoint, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QLabel, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QToolTip

from . import theme

MAX_ROWS = 8
_NAME = r"[\w$.\-]*"
_provider = lambda: {}                       # noqa: E731 - replaced by the main window
_popup = None


def set_provider(fn):
    """`fn()` returns the catalog for whatever the user is working on right now."""
    global _provider
    _provider = fn


def catalog():
    try:
        return _provider() or {}
    except Exception:  # noqa: BLE001 - a help feature must never break typing
        return {}


# ---- pure helpers (tested directly) ---------------------------------------------------------------------------
_TOKEN = re.compile(r"\{\{\s*([^{}\s]+)\s*\}\}")


def token_at(text, pos):
    """(start, end, name) of the {{name}} the character boundary `pos` touches, or None."""
    for m in _TOKEN.finditer(text):
        if m.start() < pos <= m.end():
            return m.start(), m.end(), m.group(1)
    return None


def completion_context(text, cursor):
    """If the cursor sits right after '{{' plus part of a name: (start, end, partial, has_closing).
    [start, end) is what accepting a suggestion replaces; has_closing says '}}' already follows."""
    before = text[:cursor]
    m = re.search(r"\{\{\s*(" + _NAME + r")$", before)
    if not m:
        return None
    tail = re.match(_NAME, text[cursor:]).group(0)
    end = cursor + len(tail)
    rest = text[end:].lstrip(" ")
    return m.start(1), end, m.group(1), rest.startswith("}}")


def suggestions(cat, partial):
    """Variable names matching what was typed: names that start with it first, then names that contain it."""
    p = partial.strip().lower()
    names = sorted(cat, key=lambda n: (n.startswith("$"), n.lower()))       # dynamic ($guid ...) after the real ones
    starts = [n for n in names if n.lower().startswith(p)]
    contains = [n for n in names if p in n.lower() and n not in starts]
    return starts + contains


def mask_value(entry):
    if entry.get("secret"):
        return "••••••••"
    v = entry.get("value", "")
    if v == "":
        return "(empty)"
    return v if len(v) <= 300 else v[:300] + "…"


def describe_html(name, cat):
    """The hover text for {{name}}."""
    c = theme
    e = cat.get(name)
    title = f"<b>{{{{{html.escape(name)}}}}}</b>"
    if e is None:
        return (f"{title}<br><span style='color:{c.RED}'>Not defined</span><br>"
                f"<span style='color:{c.MUTED}'>Pick an environment (top right) or add it under Variables.</span>")
    if e.get("dynamic"):
        return (f"{title}<br><span style='color:{c.MUTED}'>Generated again every time the request is sent."
                f"</span>")
    value = html.escape(mask_value(e)).replace("\n", "<br>")
    secret = " <span style='color:%s'>(secret)</span>" % c.MUTED if e.get("secret") else ""
    return (f"{title}<br><span style='color:{c.ACCENT}'>{value}</span>{secret}<br>"
            f"<span style='color:{c.MUTED}'>{html.escape(e.get('scope', ''))}</span>")


def show_tip(global_pos, text, widget):
    """Separate so tests can see what would be shown."""
    QToolTip.showText(global_pos, text, widget, widget.rect(), 12000)


# ---- the suggestion popup ---------------------------------------------------------------------------------------
class _Popup(QListWidget):
    """A list that never takes keyboard focus: typing carries on in the field while it is open."""

    def __init__(self):
        super().__init__()
        self.setObjectName("varPopup")
        self.setWindowFlags(Qt.ToolTip | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.NoFocus)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setMouseTracking(True)
        self.owner = None

    def fill(self, rows, selectable):
        """rows: [(name, html)]. A row without a name is an information line that cannot be chosen."""
        self.clear()
        for name, text in rows:
            item = QListWidgetItem()
            item.setData(Qt.UserRole, name)
            if name is None:
                item.setFlags(Qt.NoItemFlags)
            label = QLabel(text)
            label.setAttribute(Qt.WA_TransparentForMouseEvents)
            label.setStyleSheet("background: transparent; padding: 3px 6px;")
            item.setSizeHint(label.sizeHint())
            self.addItem(item)
            self.setItemWidget(item, label)
        if selectable and self.count():
            self.setCurrentRow(0)
        width = max([self.itemWidget(self.item(i)).sizeHint().width() for i in range(self.count())] + [260])
        self.setFixedSize(min(width + 16, 560), min(self.count(), MAX_ROWS) * 28 + 10)

    def current_name(self):
        item = self.currentItem()
        return item.data(Qt.UserRole) if item is not None else None


def _live():
    """The shared popup, or None if it was never made or its C++ object was destroyed (e.g. by a window teardown)."""
    global _popup
    if _popup is not None and not shiboken6.isValid(_popup):
        _popup = None
    return _popup


def _get_popup():
    global _popup
    if _live() is None:
        _popup = _Popup()
        _popup.itemClicked.connect(lambda item: _popup.owner._clicked(item) if _popup.owner is not None else None)
    return _popup


def hide_popup():
    pop = _live()
    if pop is not None:
        pop.owner = None
        pop.hide()


# ---- the per-field helper ------------------------------------------------------------------------------------------
class _Assist(QObject):
    def __init__(self, widget):
        super().__init__(widget)
        self.w = widget
        self.is_line = isinstance(widget, QLineEdit)
        self.ctx = None                      # (start, end, partial, has_closing) while the popup is open
        self.full = False                    # opened with Ctrl+Space outside {{ }}: accepting inserts the braces too
        widget.installEventFilter(self)
        if not self.is_line:
            widget.viewport().installEventFilter(self)          # tooltips are delivered to the viewport
            widget.textChanged.connect(self._edited)
        else:
            widget.textEdited.connect(self._edited)
        widget.cursorPositionChanged.connect(self._moved)

    # -- text access --
    def _text(self):
        return self.w.text() if self.is_line else self.w.toPlainText()

    def _cursor(self):
        return self.w.cursorPosition() if self.is_line else self.w.textCursor().position()

    def _replace(self, start, end, new):
        if self.is_line:
            self.w.setSelection(start, end - start)
            self.w.insert(new)                                  # behaves like typing: textEdited fires, so the tab notices
        else:
            c = self.w.textCursor()
            c.setPosition(start)
            c.setPosition(end, QTextCursor.KeepAnchor)
            c.insertText(new)
            self.w.setTextCursor(c)

    def _anchor(self):
        """Where the popup goes: just under the caret."""
        if self.is_line:
            r = self.w.cursorRect()
            return self.w.mapToGlobal(QPoint(r.x(), self.w.height()))
        r = self.w.cursorRect()
        return self.w.viewport().mapToGlobal(r.bottomLeft())

    # -- completion --
    def _edited(self, *_):
        self.full = False
        self._update()

    def _moved(self, *_):
        if _live() is not None and _popup.isVisible() and _popup.owner is self:
            self._update()

    def _update(self, force=False):
        if not (force or self.w.hasFocus()):
            return self._close()
        ctx = completion_context(self._text(), self._cursor())
        if ctx is None and not self.full:
            return self._close()
        if ctx is None:
            ctx = (self._cursor(), self._cursor(), "", False)
        self.ctx = ctx
        self._show(ctx[2])

    def _show(self, partial):
        cat = catalog()
        c = theme
        names = suggestions(cat, partial)
        rows = []
        for n in names:
            e = cat[n]
            preview = "generated when sent" if e.get("dynamic") else mask_value(e).replace("\n", " ")
            if len(preview) > 40:
                preview = preview[:40] + "…"
            rows.append((n, f"<b>{html.escape(n)}</b> <span style='color:{c.MUTED}'>{html.escape(preview)}  ·  "
                            f"{html.escape(e.get('scope', ''))}</span>"))
        if not rows:
            if not cat:
                msg = "No variables yet. Pick an environment, or add variables under Variables."
            else:
                msg = f"No variable matches “{partial.strip()}”"
            rows = [(None, f"<span style='color:{c.MUTED}'>{html.escape(msg)}</span>")]
        pop = _get_popup()
        pop.owner = self
        pop.fill(rows, selectable=bool(names))
        pop.move(self._anchor())
        pop.show()

    def _clicked(self, item):
        name = item.data(Qt.UserRole)
        if name is not None and self.ctx is not None:
            self._accept(name)
            self.w.setFocus()

    def _close(self):
        if _live() is not None and _popup.owner is self:
            hide_popup()
        self.ctx = None
        self.full = False

    def _accept(self, name):
        start, end, _partial, has_closing = self.ctx
        was_full = self.full
        self._close()
        text = name if (has_closing and not was_full) else name + "}}"
        if was_full:
            text = "{{" + name + "}}"
        self._replace(start, end, text)

    # -- events --
    def eventFilter(self, obj, ev):
        t = ev.type()
        if obj is self.w:
            if t == QEvent.KeyPress:
                k = ev.key()
                active = _live() is not None and _popup.isVisible() and _popup.owner is self
                if active:
                    if k in (Qt.Key_Down, Qt.Key_Up) and _popup.count():
                        step = 1 if k == Qt.Key_Down else -1
                        _popup.setCurrentRow((_popup.currentRow() + step) % _popup.count())
                        return True
                    if k in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Tab):
                        name = _popup.current_name()
                        if name is not None:
                            self._accept(name)
                            return True
                        self._close()                           # only the "no match" line: let Enter do its normal job
                        return False
                    if k == Qt.Key_Escape:
                        self._close()
                        return True
                if k == Qt.Key_Space and ev.modifiers() & Qt.ControlModifier:
                    self.full = completion_context(self._text(), self._cursor()) is None
                    self._update(force=True)
                    return True
            elif t == QEvent.FocusOut:
                self._close()
            elif t == QEvent.ToolTip and self.is_line:
                return self._tooltip(ev)
        elif t == QEvent.ToolTip:                              # the viewport of a text edit
            return self._tooltip(ev)
        return False

    # -- hover --
    def _boundary_at(self, pos):
        """Character boundary under the pointer, or None when the pointer is past the end of the text."""
        text = self._text()
        if self.is_line:
            idx = self.w.cursorPositionAt(pos)
            if idx >= len(text) and text:
                lo, hi = 0, self.w.width()                      # x of the middle of the last character
                while lo < hi:
                    mid = (lo + hi) // 2
                    if self.w.cursorPositionAt(QPoint(mid, pos.y())) >= len(text):
                        hi = mid
                    else:
                        lo = mid + 1
                if pos.x() > lo + self.w.fontMetrics().averageCharWidth():
                    return None
            return idx
        cur = self.w.cursorForPosition(pos)
        if cur.position() >= len(text) or text[cur.position():cur.position() + 1] in ("", "\n"):
            if pos.x() > self.w.cursorRect(cur).x() + 2:
                return None
        return cur.position()

    def _tooltip(self, ev):
        idx = self._boundary_at(ev.pos())
        tok = token_at(self._text(), idx) if idx is not None else None
        if tok is None:
            return False
        show_tip(ev.globalPos(), describe_html(tok[2], catalog()), self.w)
        return True


def attach(widget):
    """Switch variable hover and completion on for a QLineEdit / QPlainTextEdit (once; read-only fields are skipped)."""
    if not isinstance(widget, (QLineEdit, QPlainTextEdit)):
        return None
    if widget.property("noVariables") or widget.isReadOnly():
        return None
    existing = widget.findChild(_Assist)
    if existing is not None:
        return existing
    return _Assist(widget)


def attach_all(container, skip=()):
    """Attach to every text field inside `container` that is not in `skip`."""
    for w in container.findChildren(QLineEdit) + container.findChildren(QPlainTextEdit):
        if w not in skip:
            attach(w)
