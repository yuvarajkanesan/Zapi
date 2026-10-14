import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["ZAPI_HOME"] = tempfile.mkdtemp(prefix="zapi-test-")

from PySide6.QtCore import QEvent  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from tests.qt_cleanup import purge  # noqa: E402
from zapi import store, theme  # noqa: E402
from zapi.app import MainWindow  # noqa: E402

app = QApplication.instance() or QApplication([])
theme.load_fonts()


contrast = theme.contrast


def dispose(window):
    """Close and really destroy a window (as the event loop would), so style changes stay fast."""
    window.close()
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)


class PaletteTests(unittest.TestCase):
    def setUp(self):
        purge()

    def tearDown(self):
        theme.apply("Dark", "Amber", 10)
        app.setStyleSheet(theme.qss())

    def test_every_theme_is_readable(self):
        for name in theme.PALETTES:
            theme.apply(name)
            for bg in (theme.BG, theme.PANEL, theme.PANEL2, theme.FIELD):
                self.assertGreaterEqual(contrast(theme.TEXT, bg), 7.0, (name, "text", bg))
                self.assertGreaterEqual(contrast(theme.MUTED, bg), 4.5, (name, "muted", bg))
                for key in ("GREEN", "RED", "BLUE", "SYN_STRING", "SYN_KEY", "SYN_NUMBER", "SYN_LITERAL",
                            "M_WS", "M_SSE", "M_GRPC", "M_SOAP"):
                    self.assertGreaterEqual(contrast(getattr(theme, key), bg), 3.0, (name, key, bg))
            self.assertGreaterEqual(contrast(theme.FAINT, theme.BG), 2.0, (name, "faint"))
            for accent in theme.ACCENTS:
                theme.apply(name, accent)
                self.assertGreaterEqual(contrast(theme.ACCENT_TEXT, theme.ACCENT), 4.5, (name, accent, "button"))
                self.assertGreaterEqual(contrast(theme.ACCENT, theme.BG), 3.0, (name, accent, "accent vs bg"))
                self.assertGreaterEqual(contrast(theme.ACCENT_TEXT, theme.ACCENT_HOVER), 4.5, (name, accent, "hover"))

    def test_text_stays_readable_on_hover_and_selected_rows(self):
        for name in theme.PALETTES:
            theme.apply(name)
            for bg in (theme.HOVER, theme.SELECTED, theme.MENU_SEL, theme.BTN_HOVER):
                self.assertGreaterEqual(contrast(theme.TEXT, bg), 7.0, (name, "text", bg))
                self.assertGreaterEqual(contrast(theme.MUTED, bg), 4.5, (name, "muted", bg))
            for bg in (theme.MENU_SEL, theme.SELECTED):
                for verb in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
                    self.assertGreaterEqual(contrast(theme.method_color(verb), bg), 3.0, (name, verb, bg))
            # a hover must be visible against the panel it appears on
            for bg in (theme.PANEL, theme.PANEL2, theme.BG):
                self.assertGreaterEqual(contrast(theme.MENU_SEL, bg), 1.1, (name, "hover visible", bg))

    def test_high_contrast_meets_aaa(self):
        theme.apply("High contrast")
        self.assertGreaterEqual(contrast(theme.TEXT, theme.BG), 15)
        self.assertGreaterEqual(contrast(theme.MUTED, theme.BG), 7)

    def test_apply_switches_every_derived_value(self):
        theme.apply("Light", "Blue", 12)
        self.assertEqual((theme.BG, theme.CODE_SIZE, theme.THEME_NAME, theme.IS_DARK),
                         ("#f6f7f9", 12, "Light", False))
        self.assertGreaterEqual(contrast(theme.ACCENT, theme.BG), 3.0)
        self.assertEqual(theme.method_color("get"), theme.GREEN)
        self.assertEqual(theme.status_color(404), theme.ACCENT)
        qss = theme.qss()
        self.assertIn("#f6f7f9", qss)
        self.assertNotIn("#0b0d12", qss)                            # nothing left over from the dark palette
        for leftover in ("#5a4a1c", "#161b26", "#232a3b", "#1c2230", "#ffc83d"):
            self.assertNotIn(leftover, qss)
        theme.apply("Dark", "Amber")
        self.assertIn("#0b0d12", theme.qss())

    def test_system_follows_windows_and_unknown_names_fall_back(self):
        theme.apply("System")
        self.assertIn(theme.THEME_NAME, ("Dark", "Light"))
        theme.apply("Nonexistent", "Nonexistent")
        self.assertEqual((theme.THEME_NAME, theme.ACCENT), ("Nonexistent", "#ffc83d"))      # falls back to Dark / Amber
        self.assertEqual(theme.BG, "#0b0d12")

    def test_icons_follow_the_theme(self):
        theme.apply("Light")
        pm = theme.icon("plus", size=16).pixmap(16, 16).toImage()
        px = [QColor(pm.pixel(x, y)) for x in range(pm.width()) for y in range(pm.height()) if QColor(pm.pixel(x, y)).alpha() > 200]
        self.assertTrue(px)
        self.assertLess(px[0].lightness(), 100)                     # the default icon colour is the (dark) Light text colour


class WindowTests(unittest.TestCase):
    def setUp(self):
        purge()

    def tearDown(self):
        theme.apply("Dark", "Amber", 10)
        app.setStyleSheet(theme.qss())

    def test_window_builds_and_renders_under_every_theme(self):
        for name in theme.THEME_CHOICES:
            for accent in ("Amber", "Purple"):
                theme.apply(name, accent)
                app.setStyleSheet(theme.qss())
                ws = store.Workspace(os.path.join(tempfile.mkdtemp(), "w.json"))
                w = MainWindow(ws)
                for kind in ("http", "websocket", "grpc", "mqtt", "sse"):
                    w.new_request("", "", kind)
                w.show()
                img = w.grab().toImage()
                self.assertGreater(img.width(), 100)
                # the window background really is the theme's background
                self.assertEqual(QColor(img.pixel(img.width() - 5, img.height() // 2)).name(),
                                 QColor(theme.BG).name(), (name, accent))
                dispose(w)

    def test_live_switch_keeps_tabs_edits_and_mock_server(self):
        ws = store.Workspace(os.path.join(tempfile.mkdtemp(), "w.json"))
        c = ws.add_collection("C")
        r = store.new_request("Saved", url="http://x/a")
        ws.save_request(r, c["id"])
        w = MainWindow(ws)
        app._zapi_window = w
        w.show()
        w.open_saved_request(r["id"])
        v = w.current_view()
        v.url.setText("http://x/edited")
        v._edited()
        w.new_request("", "", "websocket")
        w.mock_server.start()
        port = w.mock_server.port
        ws.settings.update({"theme": "Light", "accent": "Green", "code_size": 12})
        w.apply_appearance()
        fresh = app._zapi_window
        self.assertIsNot(fresh, w)
        self.assertEqual((theme.THEME_NAME, theme.CODE_SIZE), ("Light", 12))
        self.assertGreaterEqual(contrast(theme.ACCENT, theme.BG), 3.0)
        self.assertEqual(fresh.tab_bar.count(), 2)
        self.assertEqual(fresh.view_stack.count(), 2)
        restored = fresh._view_at(0)
        self.assertEqual(restored.url.text(), "http://x/edited")
        self.assertTrue(restored.is_dirty())                         # still an unsaved edit, not silently "clean"
        self.assertTrue(fresh.mock_server.running)
        self.assertEqual(fresh.mock_server.port, port)
        self.assertEqual(restored.raw.font().pointSize(), 12)
        dispose(fresh)

    def test_settings_dialog_reports_a_look_change(self):
        from zapi.dialogs import SettingsDialog
        ws = store.Workspace(os.path.join(tempfile.mkdtemp(), "w.json"))
        d = SettingsDialog(ws)
        d._ok()
        self.assertFalse(d.look_changed)
        d2 = SettingsDialog(ws)
        d2.theme.setCurrentText("Midnight")
        d2.accent.setCurrentText("Pink")
        d2.code_size.setValue(13)
        d2._ok()
        self.assertTrue(d2.look_changed)
        self.assertEqual((ws.settings["theme"], ws.settings["accent"], ws.settings["code_size"]), ("Midnight", "Pink", 13))


if __name__ == "__main__":
    unittest.main()
