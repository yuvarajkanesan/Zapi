import io
import json
import os
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["ZAPI_HOME"] = tempfile.mkdtemp(prefix="zapi-test-")

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from tests.qt_cleanup import purge  # noqa: E402
from tests.test_loadtest import LoadTestBase, req  # noqa: E402
from zapi import cli, load_view, loadtest, store, theme  # noqa: E402

app = QApplication.instance() or QApplication([])
theme.load_fonts()
app.setStyleSheet(theme.qss())


class DialogTests(LoadTestBase):
    def setUp(self):
        super().setUp()
        purge()
        self.ws = store.Workspace(os.path.join(tempfile.mkdtemp(), "w.json"))
        env = self.ws.add_environment("Dev")
        env["vars"].append(store.new_var("base", self.base))
        self.ws.settings["active_env"] = env["id"]
        self.c = self.ws.add_collection("Shop")
        for r in (req("/ok", "list"), req("/slow", "detail"), req("/boom", "broken")):
            self.ws.save_request(r, self.c["id"])
        self.asked = []
        self.real_q = QMessageBox.question
        QMessageBox.question = staticmethod(lambda *a, **k: (self.asked.append(a[2]) or QMessageBox.Yes))

    def tearDown(self):
        QMessageBox.question = self.real_q
        purge()

    def spin(self, ms):
        """A real event loop: unlike QTest.qWait it lets other threads run (the real app is in app.exec())."""
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    def wait_done(self, d, seconds=30):
        end = time.time() + seconds
        while time.time() < end and (d.test is None or d.test.running or d.timer.isActive()):
            self.spin(50)
        self.assertFalse(d.timer.isActive(), "the dialog never noticed the test had ended")

    def test_run_from_the_dialog_shows_live_stats_and_exports(self):
        d = load_view.LoadTestDialog(self.ws, self.c["id"])
        d.show()
        d.vus.setValue(4)
        d.duration.setValue(2)
        d.run_btn.click()
        self.assertEqual(d.run_btn.text(), "Stop")
        self.assertFalse(d.vus.isEnabled())                                         # settings are locked while running
        self.assertEqual(len(self.asked), 1)
        self.assertIn("127.0.0.1", self.asked[0])                                   # the confirmation names the target host
        self.assertIn("permission", self.asked[0])
        self.wait_done(d)
        self.assertEqual(d.run_btn.text(), "Start")
        self.assertTrue(d.vus.isEnabled())
        self.assertTrue(d.export_btn.isEnabled())
        s = d.stats
        self.assertGreater(s.requests, 5)
        self.assertEqual(d.tiles["requests"].value.text(), f"{s.requests:,}")
        self.assertIn("%", d.tiles["errors"].value.text())
        self.assertGreater(s.errors, 0)                                             # the /boom request fails
        self.assertEqual(d.table.rowCount(), 3)
        self.assertEqual(d.chart.buckets, s.buckets)
        self.assertIn("Finished", d.status.text())
        self.assertIn("HTTP 500", d.status.text())                                  # the most common problem is named
        d.chart.resize(600, 200)
        self.assertFalse(d.chart.grab().isNull())                                   # the chart paints with data
        # a second start with the same host does not ask again
        d.duration.setValue(1)
        d.run_btn.click()
        self.assertEqual(len(self.asked), 1)
        self.wait_done(d)
        # exports
        from zapi import load_view as lv
        out = os.path.join(tempfile.mkdtemp(), "r.json")
        real = lv.QFileDialog.getSaveFileName
        lv.QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (out, ""))
        try:
            d._export()
            data = json.load(open(out, encoding="utf-8"))
            self.assertEqual(data["collection"], "Shop")
            self.assertEqual(data["config"]["virtualUsers"], 4)
            csv_path = out.replace(".json", ".csv")
            lv.QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (csv_path, ""))
            d._export()
            self.assertTrue(open(csv_path, encoding="utf-8").read().startswith("second,requests"))
        finally:
            lv.QFileDialog.getSaveFileName = real
        d.close()

    def test_declining_the_confirmation_starts_nothing(self):
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)
        d = load_view.LoadTestDialog(self.ws, self.c["id"])
        d.run_btn.click()
        self.assertIsNone(d.test)
        self.assertEqual(Handler_hits(), 0)

    def test_stop_button_and_closing_stop_the_test(self):
        d = load_view.LoadTestDialog(self.ws, self.c["id"])
        d.show()
        d.duration.setValue(120)
        d.run_btn.click()
        self.spin(700)
        d.run_btn.click()                                                           # Stop
        self.assertEqual(d.run_btn.text(), "Stopping…")
        started = time.time()
        self.wait_done(d, 15)
        self.assertLess(time.time() - started, 10)
        self.assertGreater(d.stats.requests, 0)
        d.duration.setValue(120)
        d.run_btn.click()
        self.spin(300)
        test = d.test
        d.close()                                                                   # closing the window must not leave users running
        self.assertTrue(test.wait(10))

    def test_folder_choice_limits_the_requests_and_empty_collections_are_refused(self):
        sub = store.new_request(name="in folder", url="{{base}}/ok")
        sub["folder"] = "Admin"
        self.ws.save_request(sub, self.c["id"])
        self.c["folders"] = ["Admin"]
        d = load_view.LoadTestDialog(self.ws, self.c["id"], "Admin")
        d.duration.setValue(1)
        d.run_btn.click()
        self.assertEqual([r["name"] for r in d.test.items], ["in folder"])
        self.wait_done(d)
        empty = store.Workspace(os.path.join(tempfile.mkdtemp(), "e.json"))
        d2 = load_view.LoadTestDialog(empty)
        self.assertFalse(d2.run_btn.isEnabled())
        self.assertIn("Create a collection", d2.status.text())

    def test_profile_text_and_limits(self):
        d = load_view.LoadTestDialog(self.ws, self.c["id"])
        d.profile.setCurrentText("Spike")
        self.assertIn("burst", d.profile_help.text())
        self.assertEqual(d.vus.maximum(), loadtest.MAX_VUS)
        self.assertEqual(d.duration.maximum(), loadtest.MAX_DURATION_S)

    def test_entry_points_exist(self):
        from zapi.app import MainWindow
        w = MainWindow(self.ws)
        opened = []
        import zapi.load_view as lv
        real = lv.LoadTestDialog
        lv.LoadTestDialog = lambda *a: type("D", (), {"exec": lambda s: opened.append(a[1:])})()
        try:
            w.open_load_test()
            w.sidebar.loadTest.emit(self.c["id"], "Admin")
        finally:
            lv.LoadTestDialog = real
        self.assertEqual(opened[1][:2], (self.c["id"], "Admin"))
        self.assertTrue(any(b.text() == "Load test" for b in w.findChildren(type(w.console_btn))))
        w.close()

    def test_chart_and_helpers(self):
        chart = load_view.LoadChart()
        chart.resize(500, 200)
        self.assertFalse(chart.grab().isNull())                                      # empty state paints
        chart.set_buckets([{"t": 0, "count": 5, "avg_ms": 30.0, "errors": 0, "vus": 1}])
        self.assertFalse(chart.grab().isNull())                                      # a single point paints
        self.assertEqual(loadtest.hosts_of([{"url": "https://{{h}}/x"}, {"url": "http://a.b:81/"}, {"url": ""}], {"h": "api.io"}),
                         ["api.io", "a.b"])


def Handler_hits():
    from tests.test_loadtest import Handler
    return sum(Handler.hits.values())


class CliTests(LoadTestBase):
    def collection_file(self):
        ws = store.Workspace(os.path.join(tempfile.mkdtemp(), "w.json"))
        c = ws.add_collection("Shop")
        for r in (req("/ok", "list"), req("/slow", "detail")):
            ws.save_request(r, c["id"])
        path = os.path.join(tempfile.mkdtemp(), "shop.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(ws.export_collection(c), f)
        return path

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["load", *args])
        return code, out.getvalue(), err.getvalue()

    def test_load_command_reports_and_exports(self):
        path = self.collection_file()
        j = os.path.join(tempfile.mkdtemp(), "r.json")
        c = os.path.join(os.path.dirname(j), "r.csv")
        code, out, err = self.run_cli(path, "--env-var", f"base={self.base}", "--vus", "3", "--duration", "2",
                                      "--json-export", j, "--csv-export", c)
        self.assertEqual(code, 0, err)
        self.assertIn("load test: Shop", out)
        self.assertIn("127.0.0.1", out)
        self.assertIn("response time", out)
        data = json.load(open(j, encoding="utf-8"))
        self.assertGreater(data["summary"]["requests"], 5)
        self.assertEqual(data["config"]["virtualUsers"], 3)
        self.assertTrue(open(c, encoding="utf-8").read().startswith("second,requests"))

    def test_thresholds_set_the_exit_code(self):
        path = self.collection_file()
        code, _, err = self.run_cli(path, "--env-var", f"base={self.base}", "--vus", "2", "--duration", "1",
                                    "--max-p95", "1", "--silent")
        self.assertEqual(code, 1)
        self.assertIn("p95", err)
        code, _, err = self.run_cli(path, "--env-var", "base=http://127.0.0.1:9", "--vus", "2", "--duration", "1",
                                    "--max-error-rate", "5", "--silent", "--timeout-request", "500")
        self.assertEqual(code, 1)                                                    # nothing reachable: errors or no completions

    def test_bad_input_is_a_clean_error(self):
        code, _, err = self.run_cli(os.path.join(tempfile.gettempdir(), "nope.json"))
        self.assertEqual(code, 2)
        path = self.collection_file()
        code, _, err = self.run_cli(path, "--profile", "wobble")
        self.assertEqual(code, 2)
        self.assertIn("--profile", err)


if __name__ == "__main__":
    unittest.main()
