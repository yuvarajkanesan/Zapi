"""Load test window: pick requests, choose a profile, watch response times, throughput and errors live."""
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QFrame, QGridLayout, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from . import loadtest, runner, theme, variables
from .loadtest import hosts_of, with_collection_variables  # noqa: F401  (kept importable from here)
from .widgets import primary

NOTICE = "Only load-test servers you own or have permission to test. Requests are sent from this computer."


class LoadChart(QWidget):
    """Average response time (line), active users (dashed) and errors (red marks) per second."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.buckets = []
        self.setMinimumHeight(190)

    def set_buckets(self, buckets):
        self.buckets = buckets
        self.update()

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        left, right, top, bottom = 52, 46, 12, 24
        plot = QRectF(left, top, max(10, w - left - right), max(10, h - top - bottom))
        p.fillRect(self.rect(), QColor(theme.PANEL))
        p.setPen(QPen(QColor(theme.BORDER), 1))
        p.drawRect(plot)
        font = QFont(self.font())
        font.setPointSizeF(8)
        p.setFont(font)
        if not self.buckets:
            p.setPen(QColor(theme.MUTED))
            p.drawText(plot, Qt.AlignCenter, "The chart fills in while the test runs")
            return
        n = max(b["t"] for b in self.buckets) + 1
        max_ms = max(10.0, max(b["avg_ms"] for b in self.buckets)) * 1.15
        max_vu = max(1, max(b["vus"] for b in self.buckets))

        def x(t):
            return plot.left() + plot.width() * (t + 0.5) / max(n, 2)

        def y_ms(v):
            return plot.bottom() - plot.height() * min(1.0, v / max_ms)

        def y_vu(v):
            return plot.bottom() - plot.height() * min(1.0, v / (max_vu * 1.15))
        p.setPen(QPen(QColor(theme.BORDER), 1, Qt.DotLine))
        for i in range(1, 4):
            gy = plot.top() + plot.height() * i / 4
            p.drawLine(QPointF(plot.left(), gy), QPointF(plot.right(), gy))
        # errors: red marks along the bottom, taller with more errors
        worst = max(1, max(b["errors"] for b in self.buckets))
        for b in self.buckets:
            if b["errors"]:
                p.fillRect(QRectF(x(b["t"]) - 3, plot.bottom() - 4 - 26 * b["errors"] / worst, 6, 4 + 26 * b["errors"] / worst),
                           QColor(theme.RED))
        # users: dashed step line
        pen = QPen(QColor(theme.MUTED), 1.4, Qt.DashLine)
        p.setPen(pen)
        pts = [QPointF(x(b["t"]), y_vu(b["vus"])) for b in self.buckets]
        for a, c in zip(pts, pts[1:]):
            p.drawLine(a, QPointF(c.x(), a.y()))
            p.drawLine(QPointF(c.x(), a.y()), c)
        # response time: solid accent line with a soft fill
        line = [QPointF(x(b["t"]), y_ms(b["avg_ms"])) for b in self.buckets]
        fill = QColor(theme.ACCENT)
        fill.setAlpha(40)
        p.setPen(Qt.NoPen)
        p.setBrush(fill)
        p.drawPolygon(QPolygonF([QPointF(line[0].x(), plot.bottom())] + line + [QPointF(line[-1].x(), plot.bottom())]))
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(theme.ACCENT), 2))
        for a, c in zip(line, line[1:]):
            p.drawLine(a, c)
        if len(line) == 1:
            p.setBrush(QColor(theme.ACCENT))
            p.drawEllipse(line[0], 3, 3)
        # axes labels
        p.setPen(QColor(theme.MUTED))
        p.drawText(QRectF(0, plot.top() - 6, left - 6, 14), Qt.AlignRight | Qt.AlignVCenter, f"{max_ms:.0f} ms")
        p.drawText(QRectF(0, plot.bottom() - 7, left - 6, 14), Qt.AlignRight | Qt.AlignVCenter, "0")
        p.drawText(QRectF(plot.right() + 6, plot.top() - 6, right - 6, 14), Qt.AlignLeft | Qt.AlignVCenter,
                   f"{max_vu} VU")
        p.drawText(QRectF(plot.left(), plot.bottom() + 4, 60, 16), Qt.AlignLeft, "0 s")
        p.drawText(QRectF(plot.right() - 60, plot.bottom() + 4, 60, 16), Qt.AlignRight, f"{n} s")


class _Tile(QFrame):
    def __init__(self, title):
        super().__init__()
        self.setObjectName("tile")
        self.setStyleSheet(f"QFrame#tile {{ background: {theme.PANEL}; border: 1px solid {theme.BORDER}; border-radius: 8px; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(0)
        self.value = QLabel("-")
        self.value.setStyleSheet("font-size: 15pt; font-weight: 700; background: transparent; border: none;")
        cap = QLabel(title)
        cap.setStyleSheet(f"color: {theme.MUTED}; font-size: 8pt; background: transparent; border: none;")
        lay.addWidget(self.value)
        lay.addWidget(cap)

    def set(self, text, color=None):
        self.value.setText(text)
        self.value.setStyleSheet(f"font-size: 15pt; font-weight: 700; color: {color or theme.TEXT}; "
                                 "background: transparent; border: none;")


class LoadTestDialog(QDialog):
    def __init__(self, ws, collection_id="", folder="", parent=None):
        super().__init__(parent)
        self.ws = ws
        self.test = None
        self.stats = None
        self.cfg = None
        self.data = None
        self._confirmed = set()
        self.setWindowTitle("Load test")
        self.resize(980, 760)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(8)
        row = QHBoxLayout()
        self.combo = QComboBox()
        for c in ws.collections:
            self.combo.addItem(f"{c['name']} ({len(c['items'])})", c["id"])
        i = self.combo.findData(collection_id)
        if i >= 0:
            self.combo.setCurrentIndex(i)
        self.combo.currentIndexChanged.connect(self._fill_folders)
        self.folder = QComboBox()
        row.addWidget(self.combo, 1)
        row.addWidget(self.folder, 1)
        form.addRow("Requests", row)
        r2 = QHBoxLayout()
        self.profile = QComboBox()
        self.profile.addItems(loadtest.PROFILES)
        self.profile.currentTextChanged.connect(self._profile_changed)
        self.vus = QSpinBox()
        self.vus.setRange(1, loadtest.MAX_VUS)
        self.vus.setValue(10)
        self.duration = QSpinBox()
        self.duration.setRange(1, loadtest.MAX_DURATION_S)
        self.duration.setValue(30)
        self.duration.setSuffix(" s")
        self.think = QSpinBox()
        self.think.setRange(0, 60000)
        self.think.setSuffix(" ms")
        for label, w in (("Load profile", self.profile), ("Virtual users", self.vus), ("Duration", self.duration),
                         ("Think time", self.think)):
            r2.addWidget(QLabel(label))
            r2.addWidget(w)
            r2.addSpacing(8)
        r2.addStretch(1)
        form.addRow("Load", r2)
        self.profile_help = QLabel(loadtest.PROFILE_HELP["Fixed"])
        self.profile_help.setObjectName("muted")
        self.profile_help.setWordWrap(True)
        form.addRow("", self.profile_help)
        r3 = QHBoxLayout()
        self.data_edit = QLineEdit()
        self.data_edit.setReadOnly(True)
        self.data_edit.setPlaceholderText("Optional CSV or JSON file: each request uses the next row's columns as variables")
        pick = QPushButton("Select file…")
        pick.clicked.connect(self._pick_data)
        clear = QPushButton("Clear")
        clear.clicked.connect(self._clear_data)
        r3.addWidget(self.data_edit, 1)
        r3.addWidget(pick)
        r3.addWidget(clear)
        form.addRow("Data file", r3)
        self.scripts = QCheckBox("Run pre-request and test scripts (much slower: each one starts a helper process)")
        form.addRow("", self.scripts)
        lay.addLayout(form)
        note = QLabel(NOTICE)
        note.setStyleSheet(f"color: {theme.ACCENT}; font-size: 8pt;")
        lay.addWidget(note)
        btns = QHBoxLayout()
        self.run_btn = primary(QPushButton("Start"))
        self.run_btn.clicked.connect(self._toggle)
        self.export_btn = QPushButton("Export results…")
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self._export)
        self.status = QLabel("")
        self.status.setObjectName("muted")
        btns.addWidget(self.run_btn)
        btns.addWidget(self.export_btn)
        btns.addWidget(self.status, 1)
        lay.addLayout(btns)
        grid = QGridLayout()
        grid.setSpacing(8)
        self.tiles = {}
        for k, (key, title) in enumerate((("requests", "Requests"), ("rps", "Requests / second"), ("avg", "Average (ms)"),
                                          ("p95", "95th percentile (ms)"), ("errors", "Errors"), ("users", "Virtual users"),
                                          ("time", "Elapsed"))):
            t = _Tile(title)
            self.tiles[key] = t
            grid.addWidget(t, 0, k)
        lay.addLayout(grid)
        self.chart = LoadChart()
        lay.addWidget(self.chart)
        legend = QLabel(f"<span style='color:{theme.ACCENT}'>━</span> average response time &nbsp; "
                        f"<span style='color:{theme.MUTED}'>╌</span> virtual users &nbsp; "
                        f"<span style='color:{theme.RED}'>▮</span> errors")
        legend.setObjectName("muted")
        lay.addWidget(legend)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Request", "Requests", "Errors", "Average", "95th %", "Max"])
        h = self.table.horizontalHeader()
        h.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setMaximumHeight(170)
        lay.addWidget(self.table)
        self.timer = QTimer(self)
        self.timer.setInterval(400)
        self.timer.timeout.connect(self._tick)
        self._fill_folders(folder)
        if not ws.collections:
            self.run_btn.setEnabled(False)
            self.status.setText("Create a collection with some requests first.")

    # ---- inputs ----
    def _fill_folders(self, keep=""):
        self.folder.clear()
        self.folder.addItem("All requests", "")
        c = self.ws.collection(self.combo.currentData())
        for f in (c or {}).get("folders", []):
            self.folder.addItem(f"Folder: {f}", f)
        if isinstance(keep, str) and keep:
            i = self.folder.findData(keep)
            if i >= 0:
                self.folder.setCurrentIndex(i)

    def _profile_changed(self, name):
        self.profile_help.setText(loadtest.PROFILE_HELP.get(name, ""))

    def _pick_data(self):
        path, _ = QFileDialog.getOpenFileName(self, "Test data", "", "Data files (*.csv *.json);;All files (*)")
        if not path:
            return
        try:
            self.data = runner.load_iteration_data(path)
        except (OSError, ValueError) as e:
            self.status.setText(f"Could not read the data file: {e}")
            return
        self.data_edit.setText(f"{path}  ({len(self.data)} rows)")

    def _clear_data(self):
        self.data = None
        self.data_edit.clear()

    # ---- running ----
    def _config(self):
        return loadtest.LoadConfig(self.profile.currentText(), self.vus.value(), self.duration.value(),
                                   self.think.value(), self.scripts.isChecked()).clamp()

    def _toggle(self):
        if self.test is not None and self.test.running:
            self.test.stop()
            self.run_btn.setText("Stopping…")
            self.run_btn.setEnabled(False)
            return
        cid = self.combo.currentData()
        c = self.ws.collection(cid)
        folder = self.folder.currentData() or ""
        items = loadtest.build_items(c, folder) if c else []
        if not items:
            self.status.setText("There are no requests to run.")
            return
        scopes = with_collection_variables(c, self.ws.scopes(cid))
        values = variables.merge_scopes(scopes["globals"], scopes["collection"], scopes["environment"])
        hosts = hosts_of(items, values)
        cfg = self._config()
        key = tuple(hosts)
        if key not in self._confirmed:
            ask = QMessageBox.question(
                self, "Start load test",
                f"Up to {cfg.vus} virtual users will send requests to:\n\n" + ("\n".join(hosts) or "(no host found)") +
                f"\n\nfor {cfg.duration_s} seconds.\n\n{NOTICE}\n\nStart?")
            if ask != QMessageBox.Yes:
                return
            self._confirmed.add(key)
        self.cfg = cfg
        self.test = loadtest.LoadTest(items, scopes, self.ws.request_options(), cfg, self.data)
        self.name = c["name"]
        self.test.start()
        self.stats = None
        self.export_btn.setEnabled(False)
        self.run_btn.setText("Stop")
        self.status.setText("Running…")
        for w in (self.combo, self.folder, self.profile, self.vus, self.duration, self.think, self.scripts):
            w.setEnabled(False)
        self.timer.start()
        self._tick()

    def _tick(self):
        if self.test is None:
            return
        s = self.test.snapshot()
        self.stats = s
        self._show(s)
        if not s.running:
            self.timer.stop()
            self._finished()

    def _show(self, s):
        t = self.tiles
        t["requests"].set(f"{s.requests:,}")
        t["rps"].set(f"{s.rps:.1f}")
        t["avg"].set(f"{s.avg_ms:.0f}")
        t["p95"].set(f"{s.p95}")
        t["errors"].set(f"{s.errors:,} ({s.error_rate:.1f}%)", theme.RED if s.errors else theme.GREEN if s.requests else None)
        t["users"].set(f"{s.active_vus} / {self.cfg.vus}" if s.running else f"{self.cfg.vus}")
        t["time"].set(f"{s.elapsed:.0f} s")
        self.chart.set_buckets(s.buckets)
        self.table.setRowCount(len(s.per_request))
        for i, r in enumerate(s.per_request):
            cells = [f"{r['method']}  {r['name']}", f"{r['count']:,}", f"{r['errors']:,}", f"{r['avg_ms']:.0f} ms",
                     f"{r['p95']} ms", f"{r['max_ms']} ms"]
            for j, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if j == 2 and r["errors"]:
                    item.setForeground(QColor(theme.RED))
                self.table.setItem(i, j, item)

    def _finished(self):
        for w in (self.combo, self.folder, self.profile, self.vus, self.duration, self.think, self.scripts):
            w.setEnabled(True)
        self.run_btn.setText("Start")
        self.run_btn.setEnabled(True)
        self.export_btn.setEnabled(self.stats is not None and self.stats.requests > 0)
        s = self.stats
        msg = f"Finished in {s.elapsed:.1f} s · {s.requests:,} requests · {s.error_rate:.1f}% errors"
        if s.top_errors:
            msg += f" · most common problem: {s.top_errors[0][0]} ({s.top_errors[0][1]}×)"
        self.status.setText(msg)

    def _export(self):
        if self.stats is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export load test results", "load-test.json",
                                              "JSON report (*.json);;CSV per second (*.csv)")
        if not path:
            return
        text = loadtest.report_csv(self.stats) if path.lower().endswith(".csv") else \
            loadtest.report_json(self.name, self.cfg, self.stats)
        try:
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(text)
        except OSError as e:
            self.status.setText(f"Could not save: {e}")
            return
        self.status.setText(f"Saved {path}")

    def done(self, result):
        """Closing the window stops a running test."""
        if self.test is not None and self.test.running:
            self.test.stop()
        self.timer.stop()
        super().done(result)
