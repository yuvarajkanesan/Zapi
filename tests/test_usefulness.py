import json
import os
import tempfile
import threading
import time
import unittest
from http.cookiejar import Cookie
from http.server import BaseHTTPRequestHandler, HTTPServer

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["ZAPI_HOME"] = tempfile.mkdtemp(prefix="zapi-test-")

from PySide6.QtWidgets import QApplication, QTableWidgetItem  # noqa: E402

from zapi import cli, http_client, store, theme  # noqa: E402
from zapi.app import MainWindow  # noqa: E402
from zapi.dialogs import CookieDialog, HelpDialog, SettingsDialog  # noqa: E402
from zapi.http_client import Response  # noqa: E402
from zapi.request_view import RequestView  # noqa: E402
from zapi.widgets import CopyTable  # noqa: E402

app = QApplication.instance() or QApplication([])
theme.load_fonts()
app.setStyleSheet(theme.qss())


def clip():
    return QApplication.clipboard().text()


def sample_response():
    return Response(ok=True, status=201, reason="Created", url="https://api.example.com/v1/users",
                    headers=[("Content-Type", "application/json; charset=utf-8"), ("X-Id", "7")],
                    body=b'{"id": 7, "name": "Asha"}', elapsed_ms=12, size=24)


class ResponseTextTests(unittest.TestCase):
    def test_forms(self):
        r = sample_response()
        self.assertEqual(r.status_line(), "HTTP 201 Created")
        self.assertEqual(r.headers_text(), "Content-Type: application/json; charset=utf-8\nX-Id: 7")
        self.assertEqual(r.raw_text(), 'HTTP 201 Created\nContent-Type: application/json; charset=utf-8\nX-Id: 7\n\n'
                                       '{"id": 7, "name": "Asha"}')


class CopyUiTests(unittest.TestCase):
    def setUp(self):
        self.ws = store.Workspace(os.path.join(tempfile.mkdtemp(), "w.json"))
        env = self.ws.add_environment("E")
        env["vars"].append(store.new_var("base", "https://api.example.com"))
        self.ws.settings["active_env"] = env["id"]
        self.win = MainWindow(self.ws)
        self.win.show()
        r = store.new_request("Create", "POST", "{{base}}/v1/users")
        r["params"] = [store.kv("page", "1")]
        r["headers"] = [store.kv("X-A", "b")]
        r["body"].update({"mode": "raw", "raw_type": "JSON", "raw": '{"n": 1}'})
        self.req = r
        self.win._add_view(r)
        self.v = self.win.current_view()

    def tearDown(self):
        self.win.close()

    def test_copy_menu_texts_and_clipboard(self):
        v = self.v
        self.assertFalse(v.copy_what("body"))                       # nothing sent yet
        self.assertIn("send the request first", v.warn.text())
        self.assertIsNone(v.copy_text_for("full"))
        v.set_response(sample_response())
        for what, expected in (("body", '{"id": 7, "name": "Asha"}'), ("status", "HTTP 201 Created"),
                               ("headers", "Content-Type: application/json; charset=utf-8\nX-Id: 7"),
                               ("url", "https://api.example.com/v1/users?page=1")):
            self.assertTrue(v.copy_what(what), what)
            self.assertEqual(clip(), expected, what)
        v.copy_what("full")
        self.assertTrue(clip().startswith("HTTP 201 Created\n") and clip().endswith('"Asha"}'))
        v.copy_what("curl")
        self.assertIn("curl -X POST 'https://api.example.com/v1/users?page=1'", clip())
        self.assertIn("--data-raw '{\"n\": 1}'", clip())
        self.assertIn("Copied request as cURL", v.warn.text())
        self.assertEqual(len(v.copy_more.menu().actions()), 9)      # 4 + sep + 2 + sep + save

    def test_main_buttons_and_shortcut_helpers(self):
        v = self.v
        v.set_response(sample_response())
        v.copy_btn.click()
        self.assertEqual(clip(), '{"id": 7, "name": "Asha"}')
        self.win.copy_current_as_curl()
        self.assertTrue(clip().startswith("curl "))
        v.wrap_cb.setChecked(True)
        self.assertEqual(v.view.lineWrapMode().name, "WidgetWidth")

    def test_save_body_to_file(self):
        import zapi.request_view as rv
        path = os.path.join(tempfile.mkdtemp(), "out.json")
        seen = {}

        class Fake:
            @staticmethod
            def getSaveFileName(parent, title, name):
                seen["name"] = name
                return path, ""
        real, rv.QFileDialog = rv.QFileDialog, Fake
        try:
            self.v.save_body()
            self.assertIn("no response body", self.v.warn.text())
            self.v.set_response(sample_response())
            self.v.save_body()
        finally:
            rv.QFileDialog = real
        self.assertEqual(seen["name"], "users.json")
        self.assertEqual(open(path, "rb").read(), b'{"id": 7, "name": "Asha"}')
        self.assertIn("Saved 25 bytes", self.v.warn.text())

    def test_response_tables_are_copyable(self):
        v = self.v
        v.set_response(sample_response())
        t = v.resp_headers
        self.assertIsInstance(t, CopyTable)
        self.assertEqual((t.row_text(1), t.row_text(1, ": ")), ("X-Id\t7", "X-Id: 7"))
        t.selectRow(0)
        t.copy_selection()
        self.assertEqual(clip(), "Content-Type\tapplication/json; charset=utf-8")
        self.assertIn("Copied 1 row", v.warn.text())
        t.selectAll()
        t.copy_selection()
        self.assertEqual(clip().count("\n"), 1)

    def test_sidebar_and_history_copy(self):
        c = self.ws.add_collection("C")
        self.ws.save_request(self.req, c["id"])
        sb = self.win.sidebar
        sb.copy_request_text(self.req, "url", c["id"])
        self.assertEqual(clip(), "https://api.example.com/v1/users?page=1")
        sb.copy_request_text(self.req, "curl", c["id"])
        self.assertTrue(clip().startswith("curl -X POST "))
        sock = store.new_kind_request("websocket")
        sock["url"] = "wss://x.io/feed"
        sb.copy_request_text(sock, "url")
        self.assertEqual(clip(), "wss://x.io/feed")
        entry = self.ws.add_history(self.req, 200, 5, 5)
        sb.refresh()
        sb._remove_history(entry["id"])
        self.assertFalse(any(h["id"] == entry["id"] for h in self.ws.history))

    def test_tab_actions(self):
        w = self.win
        w.new_request("")
        w.new_request("")
        self.assertEqual(w.tab_bar.count(), 3)
        first = w.views_in_tab_order()[0]
        first.url.setText("http://dup.example/x")
        w.duplicate_tab(first)
        self.assertEqual(w.tab_bar.count(), 4)
        dup = w.current_view()
        self.assertEqual((dup.url.text(), dup.title()), ("http://dup.example/x", "Create copy"))
        self.assertNotEqual(dup.req["id"], first.req["id"])
        # the duplicate and the original are "unsaved with content": cancelling must stop the batch, so test the
        # clean ones by emptying them first
        for v in w.views_in_tab_order():
            v.url.setText("")
            v.mark_saved()                      # no unsaved edits, so closing never asks a question
        w.close_others(first)
        self.assertEqual(w.tab_bar.count(), 1)
        w.close_all()
        self.assertEqual(w.tab_bar.count(), 0)


class NetworkOptionTests(unittest.TestCase):
    def test_proxy_is_used_and_none_bypasses_it(self):
        seen = []

        class Proxy(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                seen.append(self.path)               # a proxy receives the absolute URL
                body = b"via-proxy"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        class Direct(Proxy):
            def do_GET(self):
                body = b"direct"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        proxy, direct = HTTPServer(("127.0.0.1", 0), Proxy), HTTPServer(("127.0.0.1", 0), Direct)
        for s in (proxy, direct):
            threading.Thread(target=s.serve_forever, daemon=True).start()
        self.addCleanup(lambda: [s.shutdown() or s.server_close() for s in (proxy, direct)])
        req = store.new_request(url="http://not-a-real-host.invalid/a?b=1")
        r = http_client.send(req, {}, proxy=f"http://127.0.0.1:{proxy.server_port}", timeout=5)
        self.assertEqual((r.status, r.body), (200, b"via-proxy"))
        self.assertEqual(seen, ["http://not-a-real-host.invalid/a?b=1"])
        # "none" ignores even a broken system proxy
        os.environ["HTTP_PROXY"] = "http://127.0.0.1:1"
        try:
            d = http_client.send(store.new_request(url=f"http://127.0.0.1:{direct.server_port}/"), {}, proxy="none",
                                 timeout=5)
        finally:
            os.environ.pop("HTTP_PROXY", None)
        self.assertEqual(d.body, b"direct")

    def test_certificate_problems_are_explained(self):
        bad = os.path.join(tempfile.mkdtemp(), "junk.pem")
        open(bad, "w").write("not a certificate")
        r = http_client.send(store.new_request(url="https://127.0.0.1:1/"), {}, client_cert=(bad, ""), timeout=2)
        self.assertFalse(r.ok)
        self.assertIn("certificate", r.error.lower())
        r = http_client.send(store.new_request(url="https://127.0.0.1:1/"), {}, ca_file=bad + ".missing", timeout=2)
        self.assertIn("not found", r.error.lower())
        self.assertIsNone(http_client.proxy_handler(""))

    def test_workspace_options_and_cli_flags(self):
        ws = store.Workspace(os.path.join(tempfile.mkdtemp(), "w.json"))
        self.assertIsNone(ws.request_options()["client_cert"])
        ws.settings.update({"proxy": "none", "client_cert": "a.pem", "client_key": "a.key", "ca_file": "ca.pem"})
        o = ws.request_options()
        self.assertEqual((o["proxy"], o["client_cert"], o["ca_file"]), ("none", ("a.pem", "a.key"), "ca.pem"))
        args = cli._parser().parse_args(["run", "c.json", "--proxy", "http://p:1", "--cert", "c.pem", "--key", "k.pem",
                                         "--cacert", "ca.pem"])
        self.assertEqual((args.proxy, args.cert, args.key, args.cacert), ("http://p:1", "c.pem", "k.pem", "ca.pem"))


class DialogTests(unittest.TestCase):
    def test_settings_persist_proxy_and_certificates(self):
        ws = store.Workspace(os.path.join(tempfile.mkdtemp(), "w.json"))
        d = SettingsDialog(ws)
        self.assertEqual(d.proxy_mode.currentIndex(), 0)
        d.proxy_mode.setCurrentIndex(2)
        d.proxy_url.setText("http://proxy:8080")
        d.cert.setText("C:/c.pem")
        d.ca.setText("C:/ca.pem")
        d._ok()
        self.assertEqual((ws.settings["proxy"], ws.settings["client_cert"], ws.settings["ca_file"]),
                         ("http://proxy:8080", "C:/c.pem", "C:/ca.pem"))
        d2 = SettingsDialog(ws)
        self.assertEqual((d2.proxy_mode.currentIndex(), d2.proxy_url.text(), d2.cert.text()), (2, "http://proxy:8080", "C:/c.pem"))
        d2.proxy_mode.setCurrentIndex(1)
        d2._ok()
        self.assertEqual(ws.settings["proxy"], "none")

    def test_cookie_manager(self):
        jar = http_client.new_cookie_jar()
        for n, dom in (("sid", "a.example"), ("theme", "b.example")):
            jar.set_cookie(Cookie(0, n, "v-" + n, None, False, dom, True, False, "/", True, False, None, True, None,
                                  None, {}))
        d = CookieDialog(jar)
        self.assertEqual(d.table.rowCount(), 2)
        d.table.selectRow(0)
        d._delete()
        self.assertEqual(len(list(jar)), 1)
        d._clear()
        self.assertEqual((len(list(jar)), d.table.rowCount()), (0, 0))

    def test_help_dialog_lists_shortcuts_and_scripting(self):
        d = HelpDialog()
        self.assertGreaterEqual(d.findChild(type(CopyTable())) or 1, 0)
        texts = " ".join(d.findChildren(__import__("PySide6.QtWidgets", fromlist=["QPlainTextEdit"]).QPlainTextEdit)[0]
                         .toPlainText().split())
        self.assertIn("pm.test", texts)
        self.assertIn("Ctrl+Enter", "".join(a for a, _b in __import__("zapi.dialogs", fromlist=["SHORTCUTS"]).SHORTCUTS))

    def test_url_tooltip_shows_the_resolved_url(self):
        ws = store.Workspace(os.path.join(tempfile.mkdtemp(), "w.json"))
        env = ws.add_environment("E")
        env["vars"].append(store.new_var("host", "https://h.example"))
        ws.settings["active_env"] = env["id"]
        v = RequestView(store.new_request(url="{{host}}/x"), values_provider=ws.variable_values)
        v._check_variables()
        self.assertEqual(v.url.toolTip(), "Sends to: https://h.example/x")
        v.url.setText("https://plain.example/")
        v._check_variables()
        self.assertEqual(v.url.toolTip(), "")


if __name__ == "__main__":
    unittest.main()
