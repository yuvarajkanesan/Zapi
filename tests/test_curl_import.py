"""cURL import, checked end to end: each command is parsed, sent to a local server, and compared with what arrived."""
import base64
import email.policy
import json
import os
import tempfile
import threading
import unittest
import urllib.parse
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["ZAPI_HOME"] = tempfile.mkdtemp(prefix="zapi-test-")

from zapi import curl, curl_form, http_client, store  # noqa: E402

BS = chr(92)                      # a backslash, spelled out so the test source stays readable
B = "----WebKitFormBoundaryAbC123"


class Echo(BaseHTTPRequestHandler):
    def _go(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        out = json.dumps({"method": self.command, "ct": self.headers.get("Content-Type", ""),
                          "body": base64.b64encode(body).decode(), "x_a": self.headers.get("X-A", "")}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    do_POST = do_PUT = do_PATCH = do_DELETE = do_GET = _go

    def log_message(self, *a):
        pass


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Echo)
        cls.base = f"http://127.0.0.1:{cls.srv.server_port}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        d = tempfile.mkdtemp()
        cls.file = os.path.join(d, "avatar.png")
        with open(cls.file, "wb") as f:
            f.write(b"\x89PNG-bytes-\x00\x01")
        cls.spaced = os.path.join(d, "my folder")
        os.makedirs(cls.spaced)
        cls.spaced_file = os.path.join(cls.spaced, "report v2.txt")
        with open(cls.spaced_file, "wb") as f:
            f.write(b"hello report")

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def fill(self, text):
        return text.replace("SERVER", self.base).replace("FILE", self.file.replace(BS, "/"))

    def parse(self, text):
        return curl.parse(self.fill(text))

    def sent(self, req):
        """Send `req` for real; returns (what the server got, the Response)."""
        resp = http_client.send(req, {}, timeout=5)
        self.assertTrue(resp.ok, resp.error)
        got = json.loads(resp.body)
        got["raw"] = base64.b64decode(got["body"])
        return got, resp

    @staticmethod
    def parts(got):
        """The multipart parts the server received: [(name, filename, content type, bytes)]."""
        msg = BytesParser(policy=email.policy.default).parsebytes(
            b"Content-Type: " + got["ct"].encode() + b"\r\n\r\n" + got["raw"])
        return [(p.get_param("name", header="content-disposition"), p.get_filename(),
                 p.get_content_type() if p.get("Content-Type") else None, p.get_payload(decode=True))
                for p in msg.iter_parts()]

    @staticmethod
    def form(got):
        return urllib.parse.parse_qsl(got["raw"].decode(), keep_blank_values=True)


class ParseMatrixTests(Base):
    def rows(self, text, which="form"):
        b = self.parse(text)["body"]
        return [(r["key"], r["value"], r.get("type")) for r in b[which]] if which == "form" else \
            [(r["key"], r["value"]) for r in b[which]]

    def test_multipart_fields(self):
        r = self.parse("curl SERVER/up -F 'name=Asha' -F 'avatar=@FILE' -F 'note=a=b'")
        self.assertEqual(r["method"], "POST")
        self.assertEqual(r["body"]["mode"], "form-data")
        self.assertEqual(self.rows("curl SERVER/up -F 'name=Asha' -F 'avatar=@FILE' -F 'note=a=b'"),
                         [("name", "Asha", "text"), ("avatar", self.file.replace(BS, "/"), "file"), ("note", "a=b", "text")])

    def test_modifiers_type_and_filename_are_kept_not_glued_to_the_value(self):
        r = self.parse("curl SERVER/up -F 'f=@FILE;type=image/png;filename=me.png' -F 'meta={\"a\":1};type=application/json'")
        f, meta = r["body"]["form"]
        self.assertEqual((f["value"], f["type"], f["content_type"], f["filename"]),
                         (self.file.replace(BS, "/"), "file", "image/png", "me.png"))
        self.assertEqual((meta["value"], meta["type"], meta["content_type"]), ('{"a":1}', "text", "application/json"))

    def test_quotes_and_literals(self):
        rows = self.rows("curl SERVER -F 'a=\"quoted; value\"' -F 'b=x,y' -F 'c=' -F 'mail=me@x.io' --form-string 'd=@not-a-file'")
        self.assertEqual(rows, [("a", "quoted; value", "text"), ("b", "x,y", "text"), ("c", "", "text"),
                                ("mail", "me@x.io", "text"), ("d", "@not-a-file", "text")])

    def test_all_spellings_of_the_options(self):
        a = self.rows("curl -Fa=1 -Fb=2 SERVER")
        b = self.rows("curl --form a=1 --form=b=2 SERVER")
        self.assertEqual(a, [("a", "1", "text"), ("b", "2", "text")])
        self.assertEqual(a, b)
        r = self.parse("curl --request=PUT --header 'X-A: 1' --url SERVER/x --data a=1")
        self.assertEqual((r["method"], r["headers"][0]["key"], r["url"].endswith("/x")), ("PUT", "X-A", True))
        r = self.parse("curl -XPATCH -H'X-A: 2' SERVER/x -d a=1")
        self.assertEqual((r["method"], r["headers"][0]["value"]), ("PATCH", "2"))
        r = self.parse("curl -sSLk -X POST SERVER/x -d a=1")                   # combined flags are ignored
        self.assertEqual((r["method"], r["body"]["mode"]), ("POST", "x-www-form-urlencoded"))

    def test_urlencoded_variants(self):
        self.assertEqual(self.rows("curl SERVER -d 'user=a&pass=b%20c'", "urlencoded"), [("user", "a"), ("pass", "b c")])
        self.assertEqual(self.rows("curl SERVER -d user=a -d pass=b", "urlencoded"), [("user", "a"), ("pass", "b")])
        self.assertEqual(self.rows("curl SERVER --data-raw 'a=1&b=2'", "urlencoded"), [("a", "1"), ("b", "2")])
        self.assertEqual(self.rows("curl SERVER -d 'a=&b&c=3'", "urlencoded"), [("a", ""), ("b", ""), ("c", "3")])
        self.assertEqual(self.rows("curl SERVER -d 'q=a+b&r=%26&s=%E2%9C%93'", "urlencoded"),
                         [("q", "a b"), ("r", "&"), ("s", "✓")])
        self.assertEqual(self.rows("curl SERVER --data-urlencode 'q=hello world' --data-urlencode 'lang=en'", "urlencoded"),
                         [("q", "hello world"), ("lang", "en")])
        self.assertEqual(self.rows("curl SERVER --data-urlencode 'q=a&b=c'", "urlencoded"), [("q", "a&b=c")])
        self.assertEqual(self.parse("curl SERVER -H 'Content-Type: application/x-www-form-urlencoded' -d a=1")["headers"], [])

    def test_content_type_decides_between_form_and_raw(self):
        r = self.parse("curl SERVER -H 'Content-Type: application/json' -d 'a=1&b=2'")
        self.assertEqual((r["body"]["mode"], r["body"]["raw"]), ("raw", "a=1&b=2"))
        r = self.parse("curl SERVER -H 'Content-Type: application/x-www-form-urlencoded' -d '{\"a\":1}'")
        self.assertEqual((r["body"]["mode"], r["body"]["raw"]), ("raw", '{"a":1}'))
        self.assertEqual(r["headers"][0]["key"], "Content-Type")                   # kept: it is what the server must see
        self.assertEqual(self.parse("curl SERVER -d '{\"a\":1}'")["body"]["raw_type"], "JSON")

    def test_files_as_the_body(self):
        r = self.parse("curl SERVER --data-binary @FILE")
        self.assertEqual((r["body"]["mode"], r["body"]["file"]), ("binary", self.file.replace(BS, "/")))
        r = self.parse("curl SERVER --data-raw '@not-a-file'")                    # --data-raw never reads a file
        self.assertEqual(r["body"]["mode"], "x-www-form-urlencoded")

    def test_get_with_data_goes_to_the_query(self):
        r = self.parse("curl -G SERVER/s -d a=1 -d 'b=x y'")
        self.assertEqual((r["method"], [(p["key"], p["value"]) for p in r["params"]]), ("GET", [("a", "1"), ("b", "x y")]))

    def test_errors(self):
        for bad in ("", "curl", "curl -F", "curl -H"):
            with self.assertRaises(curl.CurlError, msg=bad):
                curl.parse(bad)


class SentBodyTests(Base):
    """What the server really receives for each way of writing the command."""

    def test_form_data_with_a_file_and_modifiers(self):
        req = self.parse("curl SERVER/up -F 'name=Asha' -F 'avatar=@FILE;type=image/png;filename=me.png' "
                         "-F 'meta={\"a\":1};type=application/json'")
        got, _ = self.sent(req)
        self.assertTrue(got["ct"].startswith("multipart/form-data; boundary="))
        parts = {p[0]: p for p in self.parts(got)}
        self.assertEqual(parts["name"][3], b"Asha")
        self.assertEqual((parts["avatar"][1], parts["avatar"][2], parts["avatar"][3]),
                         ("me.png", "image/png", b"\x89PNG-bytes-\x00\x01"))
        self.assertEqual((parts["meta"][2], parts["meta"][3]), ("application/json", b'{"a":1}'))

    def test_urlencoded_is_re_encoded_correctly(self):
        got, _ = self.sent(self.parse("curl SERVER/s -d 'user=a&pass=b%20c&q=x+y&e=%26%3D&u=%E2%9C%93'"))
        self.assertEqual(got["ct"], "application/x-www-form-urlencoded")
        self.assertEqual(self.form(got), [("user", "a"), ("pass", "b c"), ("q", "x y"), ("e", "&="), ("u", "✓")])
        got, _ = self.sent(self.parse("curl SERVER/s --data-urlencode 'q=hello world & more' --data-urlencode 'lang=en'"))
        self.assertEqual(self.form(got), [("q", "hello world & more"), ("lang", "en")])

    def test_binary_file_body_and_method_override(self):
        got, _ = self.sent(self.parse("curl -X PUT SERVER/x --data-binary @FILE"))
        self.assertEqual((got["method"], got["raw"]), ("PUT", b"\x89PNG-bytes-\x00\x01"))

    def test_chrome_bash_urlencoded(self):
        cmd = ("curl 'SERVER/s' " + BS + "\n  -H 'accept: */*' " + BS + "\n  -H 'content-type: application/x-www-form-urlencoded' "
               + BS + "\n  -H 'x-a: from-chrome' " + BS + "\n  --data-raw 'a=1&b=hello%20world&c=%E2%9C%93' --compressed")
        got, _ = self.sent(self.parse(cmd))
        self.assertEqual(self.form(got), [("a", "1"), ("b", "hello world"), ("c", "✓")])
        self.assertEqual(got["x_a"], "from-chrome")

    def multipart_body(self, boundary=B, file_part=True):
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"name\"\r\n\r\nAsha\r\n"
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"note\"\r\n\r\nline1\r\nline2\r\n")
        if file_part:
            body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"avatar\"; filename=\"photo.png\"\r\n"
                     "Content-Type: image/png\r\n\r\nPNGDATA\r\n")
        return body + f"--{boundary}--\r\n"

    def test_chrome_bash_multipart_becomes_form_rows(self):
        cmd = ("curl 'SERVER/up' " + BS + "\n  -H 'content-type: multipart/form-data; boundary=" + B + "' " + BS +
               "\n  --data-raw $'" + self.multipart_body().replace("\r\n", BS + "r" + BS + "n") + "'")
        req = self.parse(cmd)
        self.assertEqual(req["body"]["mode"], "form-data")
        rows = req["body"]["form"]
        self.assertEqual([(r["key"], r["type"]) for r in rows], [("name", "text"), ("note", "text"), ("avatar", "file")])
        self.assertEqual(rows[1]["value"], "line1\r\nline2")
        self.assertEqual((rows[2]["value"], rows[2]["filename"], rows[2]["content_type"]), ("", "photo.png", "image/png"))
        self.assertEqual([h for h in req["headers"] if h["key"].lower() == "content-type"], [])    # Zapi sets its own boundary
        resp = http_client.send(req, {}, timeout=5)
        self.assertFalse(resp.ok)                                              # the file is not in a copied command
        self.assertIn("avatar", resp.error)
        self.assertIn("no file is chosen", resp.error)
        rows[2]["value"] = self.file
        got, _ = self.sent(req)
        parts = {p[0]: p for p in self.parts(got)}
        self.assertEqual(parts["name"][3], b"Asha")
        self.assertEqual(parts["avatar"][1], "photo.png")                       # the original filename is kept
        self.assertEqual(parts["avatar"][3], b"\x89PNG-bytes-\x00\x01")        # ... with the chosen file's bytes

    def test_firefox_multipart_with_data_binary(self):
        boundary = "---------------------------41184676334"
        cmd = ("curl 'SERVER/up' --compressed -X POST -H 'Content-Type: multipart/form-data; boundary=" + boundary + "' "
               "--data-binary $'" + self.multipart_body(boundary, False).replace("\r\n", BS + "r" + BS + "n") + "'")
        req = self.parse(cmd)
        self.assertEqual([(r["key"], r["value"]) for r in req["body"]["form"]], [("name", "Asha"), ("note", "line1\r\nline2")])
        got, _ = self.sent(req)
        self.assertEqual({p[0]: p[3] for p in self.parts(got)}, {"name": b"Asha", "note": b"line1\r\nline2"})

    def test_chrome_windows_cmd_urlencoded(self):
        cmd = ("curl ^\"SERVER/s^\" ^\n  -H ^\"content-type: application/x-www-form-urlencoded^\" ^\n"
               "  --data-raw ^\"a=1^&b=hello%%20world^&c=^%^%^\" ^\n  --compressed").replace("%%", "%")
        cmd = cmd.replace("c=^%^%^\"", "c=3^\"")
        got, _ = self.sent(self.parse(cmd))
        self.assertEqual(self.form(got), [("a", "1"), ("b", "hello world"), ("c", "3")])

    def test_chrome_windows_cmd_multipart_line_breaks(self):
        nl = "^\n\n"                                                  # how cmd writes a line break inside the quoted body
        body = ("--" + B + nl + "Content-Disposition: form-data; name=^" + BS + "^\"name^" + BS + "^\"" + nl + nl + "Asha" + nl +
                "--" + B + nl + "Content-Disposition: form-data; name=^" + BS + "^\"city^" + BS + "^\"" + nl + nl + "Pune" + nl +
                "--" + B + "--" + nl)
        cmd = ("curl ^\"SERVER/up^\" ^\n  -H ^\"content-type: multipart/form-data; boundary=" + B + "^\" ^\n"
               "  --data-raw ^\"" + body + "^\"")
        req = self.parse(cmd)
        self.assertEqual(req["body"]["mode"], "form-data")
        self.assertEqual([(r["key"], r["value"]) for r in req["body"]["form"]], [("name", "Asha"), ("city", "Pune")])
        got, _ = self.sent(req)
        self.assertEqual({p[0]: p[3] for p in self.parts(got)}, {"name": b"Asha", "city": b"Pune"})

    def test_postman_generated_snippet(self):
        cmd = ("curl --location 'SERVER/up' " + BS + "\n--header 'X-A: from-postman' " + BS + "\n--form 'name=\"Asha\"' " + BS +
               "\n--form 'avatar=@\"" + self.spaced_file.replace(BS, "/") + "\"' " + BS + "\n--form 'tag=\"a;b\"'")
        req = self.parse(cmd)
        rows = {r["key"]: r for r in req["body"]["form"]}
        self.assertEqual(rows["name"]["value"], "Asha")                          # Postman quotes values
        self.assertEqual(rows["tag"]["value"], "a;b")
        self.assertEqual(rows["avatar"]["value"], self.spaced_file.replace(BS, "/"))     # path with spaces, no quotes left
        got, _ = self.sent(req)
        parts = {p[0]: p for p in self.parts(got)}
        self.assertEqual((parts["avatar"][1], parts["avatar"][3]), ("report v2.txt", b"hello report"))
        self.assertEqual((parts["tag"][3], got["x_a"]), (b"a;b", "from-postman"))

    def test_insomnia_style(self):
        cmd = ("curl --request POST --url SERVER/up --header 'Content-Type: multipart/form-data' "
               "--form name=Asha --form avatar=@FILE")
        got, _ = self.sent(self.parse(cmd))
        self.assertEqual({p[0]: p[1:] for p in self.parts(got)}["avatar"][0], "avatar.png")
        self.assertIn("boundary=", got["ct"])                                    # a boundary was added to the bare header

    def test_powershell_style(self):
        cmd = ("curl.exe -X POST \"SERVER/s\" `\n  -H \"Content-Type: application/json\" `\n  -d \"{`\"a`\":1,`\"b`\":`\"x y`\"}\"")
        req = self.parse(cmd)
        self.assertEqual((req["body"]["mode"], json.loads(req["body"]["raw"])), ("raw", {"a": 1, "b": "x y"}))
        got, _ = self.sent(req)
        self.assertEqual(json.loads(got["raw"]), {"a": 1, "b": "x y"})
        cmd = "curl.exe -X POST \"SERVER/up\" `\n  -F \"a=1\" `\n  -F \"f=@FILE\""
        got, _ = self.sent(self.parse(cmd))
        self.assertEqual({p[0]: p[3] for p in self.parts(got)}["a"], b"1")

    def test_fetch_with_a_multipart_body(self):
        body = self.multipart_body(file_part=False).replace('"', BS + '"').replace("\r\n", BS + "r" + BS + "n")
        fetch = ("fetch(\"SERVER/up\", {\"headers\": {\"content-type\": \"multipart/form-data; boundary=" + B + "\"}, "
                 "\"body\": \"" + body + "\", \"method\": \"POST\"});")
        kind, req = curl.parse_any(self.fill(fetch))
        self.assertEqual(kind, "request")
        self.assertEqual(req["body"]["mode"], "form-data")
        got, _ = self.sent(req)
        self.assertEqual({p[0]: p[3] for p in self.parts(got)}["name"], b"Asha")

    def test_fetch_urlencoded(self):
        fetch = ("fetch(\"SERVER/s\", {\"headers\": {\"content-type\": \"application/x-www-form-urlencoded\"}, "
                 "\"body\": \"a=1&b=x%20y\", \"method\": \"POST\"});")
        _, req = curl.parse_any(self.fill(fetch))
        got, _ = self.sent(req)
        self.assertEqual(self.form(got), [("a", "1"), ("b", "x y")])


class RoundTripTests(Base):
    def roundtrip(self, req, language="cURL"):
        text = curl.generate(req, {}, language)
        return curl.parse(text), text

    def test_form_data_survives_generate_then_parse(self):
        req = store.new_request(method="POST", url="https://x.io/up")
        req["body"]["mode"] = "form-data"
        req["body"]["form"] = [
            store.kv("name", "Asha", type="text"),
            store.kv("semi", "a;b", type="text"),
            store.kv("quote", 'say "hi"', type="text"),
            store.kv("at", "@literal", type="text"),
            store.kv("meta", '{"a":1}', type="text", content_type="application/json"),
            dict(store.kv("avatar", "/tmp/me.png", type="file"), filename="photo.png", content_type="image/png"),
            store.kv("plain", "/tmp/data.csv", type="file"),
        ]
        back, text = self.roundtrip(req)
        self.assertEqual(back["body"]["mode"], "form-data")
        want = [(r["key"], r["value"], r["type"], r.get("content_type"), r.get("filename")) for r in req["body"]["form"]]
        got = [(r["key"], r["value"], r["type"], r.get("content_type"), r.get("filename")) for r in back["body"]["form"]]
        self.assertEqual(got, want, text)

    def test_urlencoded_survives(self):
        req = store.new_request(method="POST", url="https://x.io/s")
        req["body"]["mode"] = "x-www-form-urlencoded"
        req["body"]["urlencoded"] = [store.kv("q", "a b&c=d"), store.kv("u", "✓"), store.kv("e", "")]
        back, text = self.roundtrip(req)
        self.assertEqual([(r["key"], r["value"]) for r in back["body"]["urlencoded"]],
                         [("q", "a b&c=d"), ("u", "✓"), ("e", "")], text)


class CodeGenerationTests(Base):
    def make(self):
        req = store.new_request(method="POST", url="https://x.io/up")
        req["body"]["mode"] = "form-data"
        req["body"]["form"] = [store.kv("name", "Asha", type="text"),
                               dict(store.kv("avatar", "/tmp/me.png", type="file"), content_type="image/png")]
        return req

    def test_every_language_includes_the_multipart_fields(self):
        req = self.make()
        for lang in curl.LANGUAGES:
            out = curl.generate(req, {}, lang)
            self.assertIn("Asha", out, lang)
            self.assertIn("avatar", out, lang)
            self.assertIn("me.png", out, lang)

    def test_language_specific_shapes(self):
        req = self.make()
        self.assertIn("-F 'avatar=@/tmp/me.png;type=image/png'", curl.generate(req, {}, "cURL"))
        py = curl.generate(req, {}, "Python (requests)")
        self.assertIn("files=files", py)
        self.assertIn('open("/tmp/me.png", "rb"), "image/png"', py)
        self.assertIn('(None, "Asha")', py)
        js = curl.generate(req, {}, "JavaScript (fetch)")
        self.assertIn("const form = new FormData();", js)
        self.assertIn('form.append("name", "Asha");', js)
        self.assertIn("body: form", js)
        self.assertNotIn("Content-Type", js)                                    # the browser adds the boundary itself
        ps = curl.generate(req, {}, "PowerShell")
        self.assertIn("-Form $form", ps)
        self.assertIn("Get-Item -Path '/tmp/me.png'", ps)

    def test_urlencoded_still_works_in_every_language(self):
        req = store.new_request(method="POST", url="https://x.io/s")
        req["body"]["mode"] = "x-www-form-urlencoded"
        req["body"]["urlencoded"] = [store.kv("a", "1"), store.kv("b", "x y")]
        for lang in curl.LANGUAGES:
            out = curl.generate(req, {}, lang)
            self.assertIn("a=1", out, lang)
            self.assertIn("x+y", out.replace("%20", "+"), lang)


class FormTableAndImportTests(Base):
    def setUp(self):
        from PySide6.QtWidgets import QApplication
        from tests.qt_cleanup import purge
        from zapi import theme
        self.app = QApplication.instance() or QApplication([])
        theme.load_fonts()
        self.app.setStyleSheet(theme.qss())
        self.purge = purge
        purge()

    def tearDown(self):
        self.purge()

    def test_content_type_and_filename_survive_the_form_table(self):
        from zapi.request_view import RequestView
        req = self.parse("curl SERVER/up -F 'avatar=@FILE;type=image/png;filename=me.png' -F 'meta={\"a\":1};type=application/json'")
        v = RequestView(req)
        v.show()
        rows = v.collect()["body"]["form"]
        self.assertEqual((rows[0]["filename"], rows[0]["content_type"]), ("me.png", "image/png"))
        self.assertEqual(rows[1]["content_type"], "application/json")
        v.form.cellWidget(0, 2).setText(self.file)                        # editing a value keeps the extras
        self.assertEqual(v.collect()["body"]["form"][0]["filename"], "me.png")
        got, _ = self.sent(self.fill_url(v.collect()))
        self.assertEqual({p[0]: p[1] for p in self.parts(got)}["avatar"], "me.png")

    def fill_url(self, req):
        req = dict(req)
        req["url"] = self.base + "/up"
        return req

    def test_pasting_a_browser_command_selects_the_body_tab_and_asks_for_the_file(self):
        from zapi.app import MainWindow
        ws = store.Workspace(os.path.join(tempfile.mkdtemp(), "w.json"))
        w = MainWindow(ws)
        w.show()
        flashed = []
        w._flash = lambda text, ms=5000: flashed.append(text)
        cmd = ("curl 'SERVER/up' -H 'content-type: multipart/form-data; boundary=" + B + "' --data-raw $'" +
               "--" + B + BS + "r" + BS + "nContent-Disposition: form-data; name=\"avatar\"; filename=\"p.png\"" + BS + "r" + BS + "n" +
               "Content-Type: image/png" + BS + "r" + BS + "n" + BS + "r" + BS + "nDATA" + BS + "r" + BS + "n--" + B + "--" + BS + "r" + BS + "n'")
        self.assertEqual(w.import_pasted(self.fill(cmd)), "")
        v = w.current_view()
        self.assertEqual(v.rtabs.bar.currentIndex(), 3)
        self.assertIn("avatar", flashed[-1])
        self.assertIn("Choose the file", flashed[-1])
        w.import_pasted(self.fill("curl SERVER/s -d a=1"))
        self.assertEqual(flashed[-1], "Imported request")
        w.close()


class FormHelpersTests(unittest.TestCase):
    def test_form_row_edge_cases(self):
        r = curl_form.form_row('f=@"/a b/c.txt";type=text/plain;filename="x y.txt"')
        self.assertEqual((r["value"], r["type"], r["content_type"], r["filename"]),
                         ("/a b/c.txt", "file", "text/plain", "x y.txt"))
        self.assertEqual(curl_form.form_row("a")["value"], "")                   # no = at all
        self.assertEqual(curl_form.form_row('a="unterminated')["value"], '"unterminated')
        self.assertEqual(curl_form.form_row("a=x;notamodifier=1")["value"], "x;notamodifier=1")

    def test_parse_multipart_edge_cases(self):
        self.assertEqual(curl_form.parse_multipart("not multipart", "b"), [])
        self.assertEqual(curl_form.parse_multipart("anything", ""), [])
        lf_only = "--b\nContent-Disposition: form-data; name=\"a\"\n\n1\n--b--\n"
        self.assertEqual([(r["key"], r["value"]) for r in curl_form.parse_multipart(lf_only, "b")], [("a", "1")])
        self.assertEqual(curl_form.boundary_of('multipart/form-data; boundary="x y"'), "x y")
        self.assertEqual(curl_form.boundary_of("multipart/form-data; charset=utf-8; boundary=abc"), "abc")


if __name__ == "__main__":
    unittest.main()


class MoreOptionsTests(Base):
    def test_head_upload_and_auth_flags(self):
        self.assertEqual(self.parse("curl -I SERVER/x")["method"], "HEAD")
        self.assertEqual(self.parse("curl --head -X GET SERVER/x")["method"], "GET")        # an explicit method wins
        r = self.parse("curl -T FILE SERVER/up")
        self.assertEqual((r["method"], r["body"]["mode"], r["body"]["file"]), ("PUT", "binary", self.file.replace(BS, "/")))
        got, _ = self.sent(r)
        self.assertEqual((got["method"], got["raw"]), ("PUT", b"\x89PNG-bytes-\x00\x01"))
        r = self.parse("curl --digest -u me:pw SERVER/x")
        self.assertEqual((r["auth"]["type"], r["auth"]["username"], r["auth"]["password"]), ("digest", "me", "pw"))
        self.assertEqual(self.parse("curl --ntlm -u me:pw SERVER/x")["auth"]["type"], "ntlm")
        self.assertEqual(self.parse("curl -u me:pw SERVER/x")["auth"]["type"], "basic")
        r = self.parse("curl --oauth2-bearer abc123 SERVER/x")
        self.assertEqual((r["auth"]["type"], r["auth"]["token"]), ("bearer", "abc123"))
        r = self.parse("curl -H 'Authorization: Bearer tok.en' SERVER/x")
        self.assertEqual((r["auth"]["type"], r["auth"]["token"], r["headers"]), ("bearer", "tok.en", []))

    def test_header_forms(self):
        r = self.parse("curl -H 'X-Empty;' -H 'X-A: 1' -H 'Accept-Encoding: gzip' -H 'Content-Length: 5' SERVER/x")
        self.assertEqual([(h["key"], h["value"]) for h in r["headers"]], [("X-Empty", ""), ("X-A", "1")])
        r = self.parse("curl -b 'a=1; b=2' -A 'my-agent/1.0' -e 'https://ref.example/' SERVER/x")
        self.assertEqual({h["key"]: h["value"] for h in r["headers"]},
                         {"Cookie": "a=1; b=2", "User-Agent": "my-agent/1.0", "Referer": "https://ref.example/"})

    def test_url_forms(self):
        self.assertEqual(self.parse("curl example.com/a")["url"], "http://example.com/a")
        self.assertEqual(self.parse("curl 'SERVER/s?a=1&b=2'")["params"][1]["key"], "b")
        self.assertEqual(self.parse("curl --url 'SERVER/x#frag'")["url"].endswith("#frag"), True)
        self.assertEqual(self.parse("curl SERVER/x -d a=1 -d b=2 -X DELETE")["method"], "DELETE")
        r = self.parse("curl -X POST SERVER/x")                                          # POST with no body at all
        self.assertEqual((r["method"], r["body"]["mode"]), ("POST", "none"))
        got, _ = self.sent(r)
        self.assertEqual((got["method"], got["raw"]), ("POST", b""))

    def test_windows_paths_in_form_fields(self):
        r = curl.parse("curl http://x.io/up -F \"file=@C:" + BS + "Users" + BS + "me" + BS + "new" + BS + "a.png\"")
        self.assertEqual(r["body"]["form"][0]["value"], "C:" + BS + "Users" + BS + "me" + BS + "new" + BS + "a.png")
        r = curl.parse("curl http://x.io/up -F 'file=@C:" + BS + "Users" + BS + "me" + BS + "a.png;type=image/png'")
        self.assertEqual((r["body"]["form"][0]["value"], r["body"]["form"][0]["content_type"]),
                         ("C:" + BS + "Users" + BS + "me" + BS + "a.png", "image/png"))


class RealPasteTests(Base):
    """The whole path a user takes: clipboard -> URL bar -> import -> the request that is then sent."""
    def test_paste_through_the_clipboard(self):
        from PySide6.QtWidgets import QApplication
        from tests.qt_cleanup import purge
        from zapi import theme
        from zapi.app import MainWindow
        app = QApplication.instance() or QApplication([])
        theme.load_fonts()
        app.setStyleSheet(theme.qss())
        purge()
        ws = store.Workspace(os.path.join(tempfile.mkdtemp(), "w.json"))
        w = MainWindow(ws)
        w.show()
        w._flash = lambda *a, **k: None
        nl = "^\n\n"
        cmd_multipart = ("curl ^\"SERVER/up^\" ^\r\n  -H ^\"content-type: multipart/form-data; boundary=" + B + "^\" ^\r\n"
                         "  --data-raw ^\"--" + B + nl + "Content-Disposition: form-data; name=^" + BS + "^\"city^" + BS + "^\"" +
                         nl + nl + "Pune" + nl + "--" + B + "--" + nl + "^\"")
        bash_urlenc = ("curl 'SERVER/s' " + BS + "\n  -H 'content-type: application/x-www-form-urlencoded' " + BS +
                       "\n  --data-raw 'a=1&b=hello%20world'")
        form_cmd = "curl -X POST SERVER/up " + BS + "\n  -F 'name=Asha' " + BS + "\n  -F 'avatar=@FILE;type=image/png'"
        try:
            for text, expect in ((cmd_multipart, {"city": b"Pune"}), (form_cmd, {"name": b"Asha", "avatar": b"\x89PNG-bytes-\x00\x01"})):
                w.new_request("")
                v = w.current_view()
                v.url.setFocus()
                app.clipboard().setText(self.fill(text))
                v.url.paste()
                v = w.current_view()
                self.assertEqual(v.current_mode(), "form-data", text)
                req = v.collect()
                self.assertTrue(req["url"].startswith(self.base), req["url"])
                got, _ = self.sent(req)
                self.assertEqual({p[0]: p[3] for p in self.parts(got)}, expect)
            w.new_request("")
            v = w.current_view()
            v.url.setFocus()
            app.clipboard().setText(self.fill(bash_urlenc))
            v.url.paste()
            v = w.current_view()
            self.assertEqual(v.current_mode(), "x-www-form-urlencoded")
            self.assertEqual([(r["key"], r["value"]) for r in v.urlenc.rows()], [("a", "1"), ("b", "hello world")])
            got, _ = self.sent(v.collect())
            self.assertEqual(self.form(got), [("a", "1"), ("b", "hello world")])
        finally:
            w.close()
            purge()
