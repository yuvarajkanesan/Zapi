import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["ZAPI_HOME"] = tempfile.mkdtemp(prefix="zapi-test-")

from PySide6.QtWidgets import QApplication  # noqa: E402

from zapi import store, theme  # noqa: E402
from zapi.app import MainWindow  # noqa: E402
from zapi.request_view import RequestView  # noqa: E402

app = QApplication.instance() or QApplication([])
theme.load_fonts()
app.setStyleSheet(theme.qss())


class UiTests(unittest.TestCase):
    def setUp(self):
        self.ws = store.Workspace(os.path.join(tempfile.mkdtemp(), "w.json"))
        self.win = MainWindow(self.ws)

    def tearDown(self):
        self.win.close()

    def test_request_view_round_trip(self):
        r = store.new_request("R", "POST", "https://x.io/a")
        r["headers"] = [store.kv("A", "b")]
        r["body"].update({"mode": "raw", "raw_type": "JSON", "raw": '{"a": 1}'})
        v = RequestView(r)
        out = v.collect()
        self.assertEqual(out["method"], "POST")
        self.assertEqual(out["headers"][0]["key"], "A")
        self.assertEqual(out["body"]["raw"], '{"a": 1}')
        self.assertFalse(v.is_dirty())
        v.url.setText("https://x.io/b")
        v._edited()
        self.assertTrue(v.is_dirty())

    def test_save_new_request_into_collection(self):
        c = self.ws.add_collection("C")
        self.win.new_request(c["id"])
        v = self.win.current_view()
        v.url.setText("https://x.io")
        self.assertFalse(self.win.save_view.__self__ is None)
        self.ws.save_request(v.collect(), c["id"])
        self.assertEqual(len(c["items"]), 1)

    def test_env_picker_and_pages(self):
        e = self.ws.add_environment("Staging")
        self.win.refresh_environment_box()
        self.win._env_picked(self.win.env_box.findData(e["id"]))
        self.assertEqual(self.ws.settings["active_env"], e["id"])
        self.win.show_environments()
        self.assertIs(self.win.pages.currentWidget(), self.win.env_page)

    def test_empty_sidebar_state(self):
        self.assertEqual(self.win.sidebar.stack.currentIndex(), 3)
        self.ws.add_collection("A")
        self.win.sidebar.refresh()
        self.assertEqual(self.win.sidebar.stack.currentIndex(), 0)

    def test_kv_table_keeps_trailing_row(self):
        from zapi.widgets import KVTable
        t = KVTable()
        t.set_rows([store.kv("a", "1")])
        self.assertEqual(t.rowCount(), 2)
        t.cellWidget(1, 1).setText("b")
        t.cellWidget(1, 1).textEdited.emit("b")
        self.assertEqual(t.rowCount(), 3)
        self.assertEqual([r["key"] for r in t.rows()], ["a", "b"])


if __name__ == "__main__":
    unittest.main()
