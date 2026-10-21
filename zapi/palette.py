"""Command palette (Ctrl+K): type a few letters to jump to a request, switch environment or run an action."""
from PySide6.QtCore import QEvent, QPoint, QSize, Qt
from PySide6.QtWidgets import QDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout

from . import theme

MAX_SHOWN = 12
ROW_HEIGHT = 50


def score(query, text):
    """Fuzzy score of `query` against `text` (higher is better, None = no match). Every query character must appear in
    order; consecutive characters, word starts and a prefix match score more, and shorter texts win ties."""
    q, t = query.lower().strip(), text.lower()
    if not q:
        return 0
    if q in t:                                                   # a plain substring beats scattered letters
        base = 1000 - t.index(q) * 3 - (len(t) - len(q))
        return base + (400 if t.startswith(q) else 0) + (150 if t.index(q) == 0 or t[t.index(q) - 1] in " /-_.:" else 0)
    chars = q.replace(" ", "")
    best = None
    for start in range(len(t)):
        if t[start] != chars[0] or not (start == 0 or t[start - 1] in " /-_.:"):
            continue                                              # a fuzzy match must begin at the start of a word
        pos, total, last, ok = start, 0, -2, True
        for ch in chars:
            i = t.find(ch, pos)
            if i < 0:
                ok = False
                break
            total += 10
            if i == last + 1:
                total += 15                                       # consecutive
            if i == 0 or t[i - 1] in " /-_.:":
                total += 12                                       # start of a word
            last, pos = i, i + 1
        if ok and (best is None or total > best):
            best = total
    return None if best is None else best - len(t) * 0.1


def rank(query, entries):
    """entries: [{"title", "subtitle", "kind", ...}] -> best matches first. Without a query: the given order."""
    if not query.strip():
        return list(entries)
    scored = []
    for n, e in enumerate(entries):
        s = score(query, e["title"])
        if s is None and e.get("subtitle") and query.strip().lower() in e["subtitle"].lower():
            s = score(query, e["subtitle"]) * 0.4               # the details only count for an exact piece of text
        if s is not None:
            scored.append((-s, n, e))
    scored.sort(key=lambda x: (x[0], x[1]))
    return [e for _, _, e in scored]


class PaletteDialog(QDialog):
    def __init__(self, entries, parent=None):
        super().__init__(parent)
        self.entries = entries
        self.chosen = None
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self.setObjectName("palette")
        self.setStyleSheet(f"QDialog#palette {{ background: {theme.PANEL2}; border: 1px solid {theme.BORDER2}; "
                           "border-radius: 10px; }")
        self.setFixedWidth(620)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("Go to request, switch environment or run a command…")
        self.edit.setFixedHeight(38)
        self.edit.textChanged.connect(self._filter)
        self.edit.installEventFilter(self)
        lay.addWidget(self.edit)
        self.list = QListWidget()
        self.list.setObjectName("paletteList")
        self.list.setStyleSheet("QListWidget#paletteList { background: transparent; border: none; outline: 0; }")
        self.list.itemActivated.connect(self._pick)
        self.list.itemClicked.connect(self._pick)
        lay.addWidget(self.list)
        self.empty = QLabel("Nothing matches")
        self.empty.setObjectName("muted")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.hide()
        lay.addWidget(self.empty)
        self._filter("")

    def _filter(self, text):
        found = rank(text, self.entries)[:MAX_SHOWN]
        self.list.clear()
        for e in found:
            it = QListWidgetItem()
            it.setData(Qt.UserRole, e)
            label = QLabel(self._html(e))
            label.setAttribute(Qt.WA_TransparentForMouseEvents)
            label.setStyleSheet("background: transparent; padding: 4px 8px;")
            it.setSizeHint(QSize(0, ROW_HEIGHT))
            self.list.addItem(it)
            self.list.setItemWidget(it, label)
        if self.list.count():
            self.list.setCurrentRow(0)
        self.list.setVisible(bool(found))
        self.empty.setVisible(not found)
        self.list.setFixedHeight(min(len(found), MAX_SHOWN) * ROW_HEIGHT + 6)
        self.adjustSize()

    @staticmethod
    def _html(e):
        import html
        kind = e.get("kind", "")
        tag = f"<span style='color:{theme.ACCENT}; font-size:8pt'>{html.escape(kind.upper())}</span>&nbsp;&nbsp;" if kind else ""
        sub = f"<br><span style='color:{theme.MUTED}; font-size:8pt'>{html.escape(e['subtitle'])}</span>" if e.get("subtitle") else ""
        return f"{tag}<b>{html.escape(e['title'])}</b>{sub}"

    def _pick(self, item):
        self.chosen = item.data(Qt.UserRole)
        self.accept()

    def eventFilter(self, obj, ev):
        if obj is self.edit and ev.type() == QEvent.KeyPress:
            k = ev.key()
            if k in (Qt.Key_Down, Qt.Key_Up) and self.list.count():
                step = 1 if k == Qt.Key_Down else -1
                self.list.setCurrentRow((self.list.currentRow() + step) % self.list.count())
                return True
            if k in (Qt.Key_Return, Qt.Key_Enter):
                if self.list.currentItem() is not None:
                    self._pick(self.list.currentItem())
                return True
            if k == Qt.Key_Escape:
                self.reject()
                return True
        return super().eventFilter(obj, ev)

    def showEvent(self, ev):
        super().showEvent(ev)
        p = self.parentWidget()
        if p is not None:
            origin = p.mapToGlobal(QPoint(0, 0))
            self.move(origin.x() + (p.width() - self.width()) // 2, origin.y() + 90)
        self.edit.setFocus()
        self.edit.selectAll()
