import json
import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["ZAPI_HOME"] = tempfile.mkdtemp(prefix="zapi-test-")

from PySide6.QtWidgets import QApplication  # noqa: E402

from zapi import curl, store, theme  # noqa: E402
from zapi.app import MainWindow  # noqa: E402

app = QApplication.instance() or QApplication([])
theme.load_fonts()
app.setStyleSheet(theme.qss())

CHROME_BASH = """curl 'https://jsonplaceholder.typicode.com/posts/1?draft=1' \\
  -H 'accept: */*' \\
  -H 'accept-encoding: gzip, deflate, br' \\
  -H 'content-length: 99' \\
  -H 'content-type: application/json' \\
  -H 'authorization: Bearer abc.def' \\
  -b 'sid=1; theme=dark' \\
  --data-raw $'{"title":"it\\'s","body":"line1\\\\nline2"}' \\
  --compressed"""

CHROME_CMD = """curl "https://jsonplaceholder.typicode.com/posts" ^
  -H "accept: */*" ^
  -H "content-type: application/json" ^
  --data-raw "^{^\\^"title^\\^":^\\^"foo^\\^",^\\^"userId^\\^":1^}"
"""

FETCH = """fetch("https://api.example.com/v1/users?x=1", {
  "headers": {
    "accept": "application/json",
    "content-type": "application/json",
    "x-token": "abc"
  },
  "body": "{\\"name\\":\\"Asha\\"}",
  "method": "POST",
  "mode": "cors",
  "credentials": "include"
});"""


class ParseTests(unittest.TestCase):
    def test_chrome_bash_curl(self):
        r = curl.parse(CHROME_BASH)
        self.assertEqual(r["method"], "POST")
        self.assertEqual(r["url"], "https://jsonplaceholder.typicode.com/posts/1")
        self.assertEqual(r["params"][0]["key"], "draft")
        keys = {h["key"].lower() for h in r["headers"]}
        self.assertNotIn("accept-encoding", keys)           # a stale "br" would make the body undecodable
        self.assertNotIn("content-length", keys)
        self.assertIn("cookie", keys)
        self.assertEqual(r["auth"]["type"], "bearer")
        self.assertEqual(r["auth"]["token"], "abc.def")
        self.assertEqual(json.loads(r["body"]["raw"]), {"title": "it's", "body": "line1\nline2"})

    def test_windows_cmd_curl(self):
        r = curl.parse(CHROME_CMD)
        self.assertEqual(r["method"], "POST")
        self.assertEqual(r["url"], "https://jsonplaceholder.typicode.com/posts")
        self.assertEqual(json.loads(r["body"]["raw"]), {"title": "foo", "userId": 1})
        self.assertEqual(r["body"]["raw_type"], "JSON")

    def test_double_quoted_single_line_like_the_screenshot(self):
        r = curl.parse('curl "https://jsonplaceholder.typicode.com/todos/1"')
        self.assertEqual((r["method"], r["url"]), ("GET", "https://jsonplaceholder.typicode.com/todos/1"))

    def test_fetch(self):
        r = curl.parse_fetch(FETCH)
        self.assertEqual((r["method"], r["url"]), ("POST", "https://api.example.com/v1/users"))
        self.assertEqual(r["params"][0]["key"], "x")
        self.assertEqual({h["key"] for h in r["headers"]}, {"accept", "x-token"})
        self.assertEqual(json.loads(r["body"]["raw"]), {"name": "Asha"})
        # hand-written style: bare keys, single quotes, JSON.stringify, trailing commas, await
        r = curl.parse_fetch("""await fetch('https://x.io/a', {
            method: 'PUT', headers: { 'Content-Type': 'application/json', }, body: JSON.stringify({ id: 1, tags: ['a'] }),
        })""")
        self.assertEqual(r["method"], "PUT")
        self.assertEqual(json.loads(r["body"]["raw"]), {"id": 1, "tags": ["a"]})
        self.assertEqual(curl.parse_fetch('fetch("https://x.io")')["method"], "GET")
        with self.assertRaises(curl.CurlError):
            curl.parse_fetch('fetch("https://x.io", { method: ')

    def test_parse_any_and_detection(self):
        self.assertEqual(curl.parse_any(CHROME_CMD)[0], "request")
        self.assertEqual(curl.parse_any(FETCH)[0], "request")
        kind, c = curl.parse_any(json.dumps({"info": {"name": "P"}, "item": [
            {"name": "a", "request": {"method": "GET", "url": "http://x"}}]}))
        self.assertEqual((kind, c["name"]), ("collection", "P"))
        kind, e = curl.parse_any(json.dumps({"name": "Dev", "values": [{"key": "a", "value": "1"}]}))
        self.assertEqual(kind, "environment")
        for bad in ("hello world", '{"just": "data"}', "[1, 2]"):
            with self.assertRaises(curl.CurlError):
                curl.parse_any(bad)
        self.assertTrue(curl.looks_like_import("curl https://x.io"))
        self.assertTrue(curl.looks_like_import(FETCH))
        self.assertFalse(curl.looks_like_import("https://x.io/curl "))
        self.assertFalse(curl.looks_like_import("{{base_url}}/users"))


class PasteUiTests(unittest.TestCase):
    def setUp(self):
        self.ws = store.Workspace(os.path.join(tempfile.mkdtemp(), "w.json"))
        self.win = MainWindow(self.ws)
        self.win.show()

    def tearDown(self):
        self.win.close()

    def test_pasting_into_the_welcome_box_opens_a_request(self):
        w = self.win.welcome
        w.url.setText(CHROME_CMD)
        w._typed(CHROME_CMD)                       # what textEdited reports after a paste
        self.assertEqual(self.win.tab_bar.count(), 1)
        v = self.win.current_view()
        self.assertEqual(v.method.currentText(), "POST")
        self.assertEqual(v.url.text(), "https://jsonplaceholder.typicode.com/posts")
        self.assertEqual(json.loads(v.raw.toPlainText()), {"title": "foo", "userId": 1})
        self.assertEqual(w.url.text(), "")
        self.assertTrue(w.err.isHidden())

    def test_enter_and_errors_in_the_welcome_box(self):
        w = self.win.welcome
        w.url.setText('curl "https://jsonplaceholder.typicode.com/todos/1"')
        w.send()
        self.assertEqual(self.win.current_view().url.text(), "https://jsonplaceholder.typicode.com/todos/1")
        w.url.setText("curl")
        w._typed("curl " + "x" * 20 + " -H")             # pasted but unreadable
        self.assertFalse(w.err.isHidden())

    def test_pasting_into_a_request_url_bar_replaces_it(self):
        self.win.new_request("")
        v = self.win.current_view()
        old_id = v.req["id"]
        v.url.setText(FETCH)
        v._url_typed(FETCH)
        self.assertEqual(self.win.tab_bar.count(), 1)
        self.assertEqual(v.req["id"], old_id)
        self.assertEqual((v.method.currentText(), v.url.text()), ("POST", "https://api.example.com/v1/users"))
        self.assertEqual(v.params.rows()[0]["key"], "x")
        self.assertTrue(v.is_dirty())

    def test_pasting_a_collection_and_environment_file(self):
        doc = json.dumps({"info": {"name": "Pets"}, "item": [
            {"name": "f", "item": [{"name": "list", "request": {"method": "GET", "url": "http://x/p"}}]}]})
        self.win.new_request("")
        v = self.win.current_view()
        v.url.setText(doc)
        v._url_typed(doc)
        self.assertEqual(self.ws.collections[0]["name"], "Pets")
        self.assertEqual(self.ws.collections[0]["folders"], ["f"])
        self.assertEqual(self.win.tab_bar.count(), 0)         # the empty tab it was pasted into is closed
        env = json.dumps({"name": "Stage", "values": [{"key": "base", "value": "http://s"}]})
        self.assertEqual(self.win.import_pasted(env), "")
        self.assertEqual(self.ws.environments[0]["name"], "Stage")
        self.assertIn("not recognised", self.win.import_pasted("lorem ipsum dolor").lower().replace("recognized", "recognised"))

    def test_typing_a_normal_url_is_not_hijacked(self):
        self.win.new_request("")
        v = self.win.current_view()
        v.url.setText("https://example.com/curl https")
        v._url_typed("https://example.com/curl https")
        self.assertEqual(v.url.text(), "https://example.com/curl https")


if __name__ == "__main__":
    unittest.main()
