import json
import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["ZAPI_HOME"] = tempfile.mkdtemp(prefix="zapi-test-")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

from tests.qt_cleanup import purge  # noqa: E402
from tests.test_dev_features import JWT  # noqa: E402
from zapi import http_client, palette, store, theme  # noqa: E402
from zapi.devtools_view import DevToolsDialog  # noqa: E402
from zapi.request_view import RequestView  # noqa: E402

app = QApplication.instance() or QApplication([])
theme.load_fonts()
app.setStyleSheet(theme.qss())


class RankTests(unittest.TestCase):
    ENTRIES = [
        {"title": "Get user profile", "subtitle": "Users / v1  ·  GET /users/1", "kind": "GET"},
        {"title": "Create user", "subtitle": "Users  ·  POST /users", "kind": "POST"},
        {"title": "Delete order", "subtitle": "Shop  ·  DELETE /orders/9", "kind": "DELETE"},
        {"title": "Settings", "subtitle": "Theme", "kind": "command"},
        {"title": "Switch to Staging", "subtitle": "environment", "kind": "environment"},
    ]

    def titles(self, q):
        return [e["title"] for e in palette.rank(q, self.ENTRIES)]

    def test_no_query_keeps_the_given_order(self):
        self.assertEqual(self.titles(""), [e["title"] for e in self.ENTRIES])

    def test_matching(self):
        self.assertEqual(set(self.titles("user")), {"Get user profile", "Create user"})
        self.assertEqual(self.titles("sett")[0], "Settings")                      # prefix
        self.assertEqual(self.titles("gup")[0], "Get user profile")                # scattered letters, word starts
        self.assertEqual(self.titles("DELETE /orders"), ["Delete order"])          # matched through the details
        self.assertEqual(self.titles("zzz"), [])
        self.assertIn("Switch to Staging", self.titles("stag"))

    def test_title_match_beats_detail_match_and_prefix_beats_middle(self):
        entries = [{"title": "Other", "subtitle": "mentions orders here"}, {"title": "Orders list", "subtitle": ""},
                   {"title": "My orders", "subtitle": ""}]
        self.assertEqual([e["title"] for e in palette.rank("orders", entries)], ["Orders list", "My orders", "Other"])
        self.assertIsNone(palette.score("xyz", "orders"))
        self.assertEqual(palette.score("", "anything"), 0)


class PaletteDialogTests(unittest.TestCase):
    def setUp(self):
        purge()
        self.ran = []
        self.entries = [{"title": t, "subtitle": "", "kind": "command", "run": (lambda t=t: self.ran.append(t))}
                        for t in ("Alpha", "Beta", "Gamma", "Alphabet soup")]

    def tearDown(self):
        purge()

    def test_typing_filters_and_enter_chooses(self):
        d = palette.PaletteDialog(self.entries)
        d.show()
        QTest.qWait(50)
        self.assertEqual(d.list.count(), 4)
        QTest.keyClicks(d.edit, "alph")
        self.assertEqual(d.list.count(), 2)
        QTest.keyClick(d.edit, Qt.Key_Down)
        QTest.keyClick(d.edit, Qt.Key_Return)
        self.assertEqual(d.result(), QDialog.Accepted)
        d.chosen["run"]()
        self.assertEqual(self.ran, ["Alphabet soup"])

    def test_escape_and_no_match(self):
        d = palette.PaletteDialog(self.entries)
        d.show()
        QTest.keyClicks(d.edit, "qqq")
        self.assertEqual(d.list.count(), 0)
        self.assertFalse(d.empty.isHidden())
        QTest.keyClick(d.edit, Qt.Key_Return)                                       # nothing to choose: stays open
        self.assertIsNone(d.chosen)
        QTest.keyClick(d.edit, Qt.Key_Escape)
        self.assertEqual(d.result(), QDialog.Rejected)

    def test_list_is_capped_and_wraps(self):
        many = [{"title": f"Item {i}", "subtitle": "", "kind": "x", "run": None} for i in range(50)]
        d = palette.PaletteDialog(many)
        self.assertEqual(d.list.count(), palette.MAX_SHOWN)
        QTest.keyClick(d.edit, Qt.Key_Up)
        self.assertEqual(d.list.currentRow(), palette.MAX_SHOWN - 1)


class MainWindowPaletteTests(unittest.TestCase):
    def setUp(self):
        purge()
        self.ws = store.Workspace(os.path.join(tempfile.mkdtemp(), "w.json"))
        self.env = self.ws.add_environment("Staging")
        c = self.ws.add_collection("Shop")
        self.req = store.new_request(name="List orders", url="https://x.io/orders")
        self.ws.save_request(self.req, c["id"])
        self.ws.add_history(store.new_request(url="https://x.io/old"), 200, 5, 5)

    def tearDown(self):
        purge()

    def window(self):
        from zapi.app import MainWindow
        w = MainWindow(self.ws)
        w.show()
        return w

    def test_entries_cover_requests_environments_history_and_commands(self):
        w = self.window()
        by_kind = {}
        for e in w.palette_entries():
            by_kind.setdefault(e["kind"], []).append(e["title"])
        self.assertIn("List orders", by_kind["GET"])
        self.assertIn("Staging", by_kind["environment"])
        self.assertIn("No environment", by_kind["environment"])
        self.assertIn("https://x.io/old", by_kind["history"])
        self.assertIn("Load test", by_kind["command"])
        self.assertIn("Developer tools: JWT decoder", by_kind["command"])
        w.close()

    def test_choosing_a_request_opens_it_and_an_environment_switches(self):
        w = self.window()
        entries = {e["title"]: e for e in w.palette_entries()}
        entries["List orders"]["run"]()
        self.assertEqual(w.current_view().req["name"], "List orders")
        entries["Staging"]["run"]()
        self.assertEqual(self.ws.settings["active_env"], self.env["id"])
        self.assertEqual(w.env_box.currentText(), "Staging")
        entries["No environment"]["run"]()
        self.assertEqual(self.ws.settings["active_env"], "")
        w.close()

    def test_shortcuts_and_menu_entries(self):
        from PySide6.QtGui import QShortcut
        w = self.window()
        keys = {s.key().toString() for s in w.findChildren(QShortcut)}
        self.assertIn("Ctrl+K", keys)
        self.assertIn("Ctrl+P", keys)
        w.close()


class DevToolsDialogTests(unittest.TestCase):
    def setUp(self):
        purge()

    def tearDown(self):
        purge()

    def test_jwt_tab_decodes_live_and_checks_the_secret(self):
        d = DevToolsDialog(None, "jwt", "Bearer " + JWT)
        d.show()
        self.assertEqual(d.tabs.currentWidget(), d.jwt)
        out = d.jwt.output.toPlainText()
        self.assertIn('"name": "John Doe"', out)
        self.assertIn("Issued at: 2018-01-18", out)
        d.jwt_secret.setText("your-256-bit-secret")
        self.assertIn("valid", d.jwt.status.text())
        d.jwt_secret.setText("nope")
        self.assertIn("does not match", d.jwt.status.text())
        d.jwt.input.setPlainText("garbage")
        self.assertIn("three parts", d.jwt.status.text())
        self.assertEqual(d.jwt.output.toPlainText(), "")
        d.jwt.input.setPlainText("")
        self.assertEqual(d.jwt.status.text(), "")

    def test_base64_url_hash_time_uuid(self):
        d = DevToolsDialog(None, "base64", "héllo")
        d.show()
        d.b64.options.itemAt(0).widget().click()                                   # Encode
        self.assertEqual(d.b64.output.toPlainText(), "aMOpbGxv")
        d.b64.input.setPlainText(d.b64.output.toPlainText())
        d.b64.options.itemAt(1).widget().click()                                   # Decode
        self.assertEqual(d.b64.output.toPlainText(), "héllo")
        d.b64.input.setPlainText("%%%")
        d.b64.options.itemAt(1).widget().click()
        self.assertIn("not valid Base64", d.b64.status.text())
        d.url.input.setPlainText("a b&c")
        d.url.options.itemAt(0).widget().click()
        self.assertEqual(d.url.output.toPlainText(), "a%20b%26c")
        d.hash.input.setPlainText("abc")
        self.assertEqual(d.hash.output.toPlainText(),
                         "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
        d.hash_algo.setCurrentText("MD5")
        self.assertEqual(d.hash.output.toPlainText(), "900150983cd24fb0d6963f7d28e17f72")
        d.time_in.setText("1700000000")
        self.assertEqual(d.time_out["iso_utc"].text(), "2023-11-14T22:13:20Z")
        d.time_in.setText("garbage")
        self.assertIn("Enter epoch", d.time_status.text())
        self.assertEqual(len(d.uuid.output.toPlainText().splitlines()), 5)
        d.uuid_count.setValue(2)
        d.uuid.options.itemAt(2).widget().click()
        self.assertEqual(len(d.uuid.output.toPlainText().splitlines()), 2)


class ResponseFilterTests(unittest.TestCase):
    def setUp(self):
        purge()

    def tearDown(self):
        purge()

    def view_with(self, body, content_type="application/json"):
        v = RequestView(store.new_request(url="http://x"))
        v.resize(900, 600)
        v.show()
        v.response = http_client.Response(ok=True, status=200, reason="OK", body=body.encode(), size=len(body),
                                          elapsed_ms=5, headers=[("Content-Type", content_type)], url="http://x")
        v._render_body()
        return v

    DOC = json.dumps({"items": [{"id": 1, "name": "a", "price": 5}, {"id": 2, "name": "b", "price": 15}], "total": 2})

    def test_filter_shows_only_the_selected_part(self):
        v = self.view_with(self.DOC)
        self.assertIn('"total"', v.view.toPlainText())
        v.path_filter.setText("$.items[?(@.price < 10)].name")
        self.assertEqual(v.view.toPlainText(), "a")
        v.path_filter.setText("$.items[*].id")
        self.assertEqual(json.loads(v.view.toPlainText()), [1, 2])
        v.path_filter.setText("$.items[1]")
        self.assertEqual(json.loads(v.view.toPlainText()), {"id": 2, "name": "b", "price": 15})
        v.path_filter.setText("$.nothing")
        self.assertIn("No match", v.view.toPlainText())
        v.path_filter.setText("$.items[")
        self.assertIn("Missing", v.view.toPlainText())
        self.assertIn(theme.RED, v.path_filter.styleSheet())                       # a broken path is marked
        v.path_filter.setText("")
        self.assertIn('"total"', v.view.toPlainText())                             # clearing restores the full body
        self.assertEqual(v.path_filter.styleSheet(), "")

    def test_copy_still_copies_the_whole_body_and_non_json_is_untouched(self):
        v = self.view_with(self.DOC)
        v.path_filter.setText("$.total")
        self.assertEqual(json.loads(v.copy_text_for("body"))["total"], 2)
        t = self.view_with("plain words", "text/plain")
        t.path_filter.setText("$.a")
        self.assertEqual(t.view.toPlainText(), "plain words")


if __name__ == "__main__":
    unittest.main()
