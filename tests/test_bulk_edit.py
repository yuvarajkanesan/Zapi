import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["ZAPI_HOME"] = tempfile.mkdtemp(prefix="zapi-test-")

from PySide6.QtWidgets import QApplication  # noqa: E402

from tests.qt_cleanup import purge  # noqa: E402
from zapi import store, theme  # noqa: E402
from zapi.request_view import RequestView  # noqa: E402
from zapi.widgets import KVEditor, KVTable, format_rows, parse_rows  # noqa: E402

app = QApplication.instance() or QApplication([])
theme.load_fonts()
app.setStyleSheet(theme.qss())


def keys(rows):
    return [(r["key"], r["value"], r["enabled"]) for r in rows]


class FormatTests(unittest.TestCase):
    def test_headers_round_trip_including_disabled_rows_and_colons_in_values(self):
        rows = [store.kv("Accept", "application/json"), store.kv("Referer", "http://example.com:8080/a?b=c"),
                store.kv("X-Off", "1", False), store.kv("Authorization", "Bearer {{token}}")]
        text = format_rows(rows, "header")
        self.assertEqual(text.splitlines()[2], "// X-Off: 1")
        self.assertIn("Referer: http://example.com:8080/a?b=c", text)
        self.assertEqual(keys(parse_rows(text, "header")), keys(rows))

    def test_params_round_trip_with_equals_and_colons_in_values(self):
        rows = [store.kv("page", "1"), store.kv("token", "YWJjZA=="), store.kv("redirect", "http://x.io:80/p"),
                store.kv("debug", "true", False)]
        text = format_rows(rows, "param")
        self.assertEqual(text, "page=1\ntoken=YWJjZA==\nredirect=http://x.io:80/p\n// debug=true")
        self.assertEqual(keys(parse_rows(text, "param")), keys(rows))

    def test_forgiving_parser(self):
        text = "\n  Accept :  */*  \n\n:authority: example.com\n:method: GET\nX-Flag\n//\n// Cache-Control: no-cache\nBad Line Without Sep\n"
        got = keys(parse_rows(text, "header"))
        self.assertEqual(got, [("Accept", "*/*", True), ("X-Flag", "", True), ("Cache-Control", "no-cache", False),
                               ("Bad Line Without Sep", "", True)])
        self.assertEqual(keys(parse_rows("a:1\nb=2\nc", "param")), [("a", "1", True), ("b", "2", True), ("c", "", True)])
        self.assertEqual(parse_rows("", "header"), [])
        self.assertEqual(parse_rows("   \n\n", "param"), [])
        self.assertEqual(keys(parse_rows("=novalue key\n: just a colon", "param")), [("=novalue key", "", True)])

    def test_pasted_browser_header_block(self):
        block = ("accept: text/html,application/xhtml+xml\naccept-encoding: gzip, deflate, br\n"
                 "cookie: sid=abc; theme=dark\nsec-ch-ua: \"Chromium\";v=\"126\"\nuser-agent: Mozilla/5.0 (Windows NT 10.0)\n")
        rows = parse_rows(block, "header")
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[2]["value"], "sid=abc; theme=dark")
        self.assertEqual(rows[3]["value"], '"Chromium";v="126"')


class EditorWidgetTests(unittest.TestCase):
    def tearDown(self):
        purge()

    def make(self, style="header"):
        return KVEditor(KVTable(key_hint="Header", value_hint="Value"), style)

    def test_toggle_shows_the_same_rows_as_text_and_back(self):
        ed = self.make()
        ed.set_rows([store.kv("A", "1"), store.kv("B", "2", False)])
        self.assertFalse(ed.bulk_active)
        self.assertEqual(ed.toggle.text(), "Bulk Edit")
        ed.set_bulk(True)
        self.assertTrue(ed.bulk_active)
        self.assertEqual(ed.text.toPlainText(), "A: 1\n// B: 2")
        self.assertEqual(ed.toggle.text(), "Key-Value Edit")
        self.assertIn("Key: Value", ed.hint.text())
        ed.text.setPlainText("A: 1\nC: 3\n// D: 4")
        ed.set_bulk(False)
        self.assertEqual(keys(ed.table.rows()), [("A", "1", True), ("C", "3", True), ("D", "4", False)])
        self.assertFalse(ed.table.cellWidget(2, 0).findChild(type(ed.table.cellWidget(0, 0).findChild(
            __import__("PySide6.QtWidgets", fromlist=["QCheckBox"]).QCheckBox))).isChecked())   # D shows unchecked

    def test_rows_reflect_the_text_while_bulk_editing(self):
        ed = self.make()
        ed.set_bulk(True)
        ed.text.setPlainText("X-One: 1\nX-Two: 2")
        self.assertEqual([r["key"] for r in ed.rows()], ["X-One", "X-Two"])
        ed.set_rows([store.kv("Z", "9")])                       # loading a request while in bulk mode refreshes the text
        self.assertEqual(ed.text.toPlainText(), "Z: 9")

    def test_changes_are_announced_from_both_modes_but_not_from_the_switch_itself(self):
        ed = self.make()
        seen = []
        ed.changed.connect(lambda: seen.append(1))
        ed.set_rows([store.kv("A", "1")])
        ed.set_bulk(True)
        self.assertEqual(seen, [])                              # switching to text changes nothing
        ed.text.setPlainText("A: 2")
        self.assertEqual(len(seen), 1)
        ed.set_bulk(False)                                      # coming back may have changed the rows
        self.assertEqual(len(seen), 2)
        ed.table.cellWidget(0, 2).setText("3")
        ed.table.cellWidget(0, 2).textEdited.emit("3")
        self.assertEqual(len(seen), 3)

    def test_switching_back_and_forth_is_stable(self):
        ed = self.make()
        original = [store.kv("A", "1"), store.kv("B", "x: y"), store.kv("C", "3", False)]
        ed.set_rows(original)
        for _ in range(4):
            ed.set_bulk(True)
            ed.set_bulk(False)
        self.assertEqual(keys(ed.rows()), keys(original))
        self.assertEqual(ed.table.rowCount(), 4)                # three rows plus the one empty row, never growing
        empty = self.make()
        empty.set_bulk(True)
        empty.set_bulk(False)
        self.assertEqual((empty.rows(), empty.table.rowCount()), ([], 1))


class RequestIntegrationTests(unittest.TestCase):
    def tearDown(self):
        purge()

    def test_headers_params_and_form_bodies_have_bulk_edit(self):
        for name in ("params", "headers", "urlenc"):
            self.assertIsInstance(getattr(RequestView(store.new_request()), name), KVEditor, name)

    def test_bulk_edited_headers_are_what_gets_sent_and_saved(self):
        v = RequestView(store.new_request(url="http://x"))
        v.headers.set_bulk(True)
        v.headers.text.setPlainText("Accept: application/json\nX-Trace: abc\n// X-Skip: no")
        out = v.collect()
        self.assertEqual(keys(out["headers"]), [("Accept", "application/json", True), ("X-Trace", "abc", True),
                                                ("X-Skip", "no", False)])
        self.assertTrue(v.is_dirty())
        self.assertIn("Headers (2)", v.rtabs.bar.tabText(2))    # the tab counts enabled rows while still in bulk mode
        again = RequestView(out)
        self.assertEqual(keys(again.collect()["headers"]), keys(out["headers"]))
        from zapi import http_client
        self.assertEqual([k for k, _ in http_client.build_headers(out, {})], ["Accept", "X-Trace"])

    def test_params_in_bulk_mode_update_the_final_url(self):
        v = RequestView(store.new_request(url="http://x/a"))
        v.params.set_bulk(True)
        v.params.text.setPlainText("page=1\nq=a b\n// off=1")
        from zapi import http_client
        self.assertEqual(http_client.build_url(v.collect(), {}), "http://x/a?page=1&q=a%20b")

    def test_urlencoded_body_bulk_edit(self):
        v = RequestView(store.new_request(url="http://x"))
        v.mode_buttons["x-www-form-urlencoded"].setChecked(True)
        v.urlenc.set_bulk(True)
        v.urlenc.text.setPlainText("grant_type=client_credentials\nscope=read write")
        body = v.collect()["body"]
        self.assertEqual(body["mode"], "x-www-form-urlencoded")
        self.assertEqual([(r["key"], r["value"]) for r in body["urlencoded"]],
                         [("grant_type", "client_credentials"), ("scope", "read write")])

    def test_protocol_tabs_use_it_too(self):
        from zapi.proto_views import GrpcView, StreamView
        self.assertIsInstance(StreamView(store.new_kind_request("websocket")).headers, KVEditor)
        self.assertIsInstance(GrpcView(store.new_kind_request("grpc")).headers, KVEditor)


if __name__ == "__main__":
    unittest.main()
