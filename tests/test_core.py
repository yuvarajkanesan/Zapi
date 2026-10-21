import gzip
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from zapi import curl, http_client, importers, store, variables


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _reply(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        if self.path.startswith("/redirect"):
            self.send_response(302)
            self.send_header("Location", "/echo")
            self.end_headers()
            return
        payload = json.dumps({"method": self.command, "path": self.path, "headers": dict(self.headers),
                              "body": body.decode("utf-8", "replace")}).encode()
        status = 201 if self.path.startswith("/created") else 404 if self.path.startswith("/missing") else 200
        if self.headers.get("Accept-Encoding", "").find("gzip") >= 0 and self.path.startswith("/gzip"):
            payload = gzip.compress(payload)
            self.send_response(status)
            self.send_header("Content-Encoding", "gzip")
        else:
            self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Set-Cookie", "sid=abc123; Path=/")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = _reply


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), Handler)
        cls.base = f"http://127.0.0.1:{cls.srv.server_port}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def req(self, **kw):
        r = store.new_request(url="{{base_url}}/echo")
        for k, v in kw.items():
            if k in ("auth", "body"):
                r[k].update(v)
            else:
                r[k] = v
        return r

    def values(self):
        return {"base_url": self.base}

    def test_get_with_params_and_variables(self):
        r = self.req(params=[store.kv("a", "1 2"), store.kv("off", "x", False)])
        resp = http_client.send(r, self.values())
        self.assertTrue(resp.ok)
        self.assertEqual(resp.status, 200)
        self.assertEqual(json.loads(resp.body)["path"], "/echo?a=1%202")
        self.assertGreaterEqual(resp.size, 10)

    def test_post_json_and_status_text(self):
        r = self.req(method="POST", url="{{base_url}}/created",
                     body={"mode": "raw", "raw_type": "JSON", "raw": '{"name": "{{n}}"}'})
        resp = http_client.send(r, {**self.values(), "n": "Asha"})
        self.assertEqual(resp.status, 201)
        echoed = json.loads(resp.body)
        self.assertEqual(json.loads(echoed["body"]), {"name": "Asha"})
        self.assertEqual(echoed["headers"]["Content-Type"], "application/json")

    def test_error_status_is_a_response(self):
        resp = http_client.send(self.req(url="{{base_url}}/missing"), self.values())
        self.assertTrue(resp.ok)
        self.assertEqual(resp.status, 404)

    def test_gzip_and_cookies(self):
        jar = http_client.new_cookie_jar()
        resp = http_client.send(self.req(url="{{base_url}}/gzip"), self.values(), jar=jar)
        self.assertEqual(json.loads(resp.body)["path"], "/gzip")
        self.assertEqual(resp.cookies[0]["name"], "sid")

    def test_redirect_toggle(self):
        r = self.req(url="{{base_url}}/redirect")
        self.assertEqual(http_client.send(r, self.values()).status, 200)
        self.assertEqual(http_client.send(r, self.values(), follow_redirects=False).status, 302)

    def test_auth_variants(self):
        e = json.loads(http_client.send(self.req(auth={"type": "bearer", "token": "T0K"}), self.values()).body)
        self.assertEqual(e["headers"]["Authorization"], "Bearer T0K")
        e = json.loads(http_client.send(self.req(auth={"type": "basic", "username": "u", "password": "p"}),
                                        self.values()).body)
        self.assertEqual(e["headers"]["Authorization"], "Basic dTpw")
        e = json.loads(http_client.send(self.req(auth={"type": "apikey", "key": "X-Key", "value": "k", "in": "header"}),
                                        self.values()).body)
        self.assertEqual(e["headers"]["X-Key"], "k")
        e = json.loads(http_client.send(self.req(auth={"type": "apikey", "key": "key", "value": "k", "in": "query"}),
                                        self.values()).body)
        self.assertEqual(e["path"], "/echo?key=k")

    def test_form_bodies(self):
        r = self.req(method="POST", body={"mode": "x-www-form-urlencoded",
                                          "urlencoded": [store.kv("a", "b c")]})
        e = json.loads(http_client.send(r, self.values()).body)
        self.assertEqual(e["body"], "a=b+c")
        r = self.req(method="POST", body={"mode": "form-data", "form": [store.kv("f", "v", type="text")]})
        e = json.loads(http_client.send(r, self.values()).body)
        self.assertIn('name="f"', e["body"])
        self.assertTrue(e["headers"]["Content-Type"].startswith("multipart/form-data; boundary="))

    def test_failures_do_not_raise(self):
        self.assertIn("URL", http_client.send(self.req(url=""), {}).error)
        self.assertFalse(http_client.send(self.req(url="http://127.0.0.1:1/"), {}, timeout=2).ok)
        self.assertFalse(http_client.send(self.req(url="ftp://x/"), {}).ok)
        bad = self.req(method="POST", body={"mode": "GraphQL", "query": "{a}", "variables": "{nope"})
        self.assertIn("GraphQL", http_client.send(bad, self.values()).error)


class VariableTests(unittest.TestCase):
    def test_substitute(self):
        self.assertEqual(variables.substitute("{{a}}/{{ b }}/{{c}}", {"a": 1, "b": "x"}), "1/x/{{c}}")
        self.assertEqual(variables.unresolved("{{a}}{{c}}{{$guid}}", {"a": 1}), ["c"])
        self.assertEqual(len(variables.substitute("{{$guid}}", {})), 36)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "ws.json")

    def test_round_trip_and_scopes(self):
        ws = store.Workspace(self.path)
        c = ws.add_collection("Users API")
        c["variables"].append(store.kv("v", "coll"))
        r = store.new_request("List", url="{{base_url}}/users")
        ws.save_request(r, c["id"])
        env = ws.add_environment("Staging")
        env["vars"] += [store.new_var("base_url", "init", "cur"), store.new_var("v", "env")]
        ws.globals["vars"].append(store.new_var("g", "glob"))
        ws.settings["active_env"] = env["id"]
        ws.add_history(r, 200, 12, 5)
        ws.save()
        ws2 = store.Workspace(self.path)
        self.assertEqual(ws2.collections[0]["items"][0]["name"], "List")
        self.assertEqual(ws2.variable_values(c["id"]), {"g": "glob", "v": "env", "base_url": "cur"})
        self.assertEqual(len(ws2.history), 1)

    def test_move_and_delete(self):
        ws = store.Workspace(self.path)
        a, b = ws.add_collection("A"), ws.add_collection("B")
        r = store.new_request()
        ws.save_request(r, a["id"])
        ws.save_request(r, b["id"])
        self.assertEqual((len(a["items"]), len(b["items"])), (0, 1))
        dup = ws.duplicate_request(r["id"])
        self.assertEqual(len(b["items"]), 2)
        ws.delete_request(dup["id"])
        self.assertEqual(len(b["items"]), 1)

    def test_secret_not_exported(self):
        ws = store.Workspace(self.path)
        e = ws.add_environment("E")
        e["vars"] += [store.new_var("token", "s3cret", secret=True), store.new_var("x", "1")]
        out = ws.export_environment(e)
        self.assertNotIn("s3cret", json.dumps(out))

    def test_corrupt_file_is_ignored(self):
        with open(self.path, "w") as f:
            f.write("{not json")
        self.assertEqual(store.Workspace(self.path).collections, [])


class CurlTests(unittest.TestCase):
    def test_parse_json_post(self):
        r = curl.parse("""curl -X POST 'https://api.example.com/users?x=1' \\
            -H 'Authorization: Bearer abc' -H 'Content-Type: application/json' \\
            -d '{"name":"Asha"}'""")
        self.assertEqual(r["method"], "POST")
        self.assertEqual(r["url"], "https://api.example.com/users")
        self.assertEqual(r["params"][0]["key"], "x")
        self.assertEqual(r["auth"], {**r["auth"], "type": "bearer", "token": "abc"})
        self.assertEqual(r["body"]["mode"], "raw")
        self.assertEqual(r["body"]["raw_type"], "JSON")
        self.assertEqual(r["headers"], [])

    def test_parse_defaults(self):
        self.assertEqual(curl.parse("curl example.com")["url"], "http://example.com")
        self.assertEqual(curl.parse("curl -d a=1 -d b=2 https://x.io")["body"]["urlencoded"][1]["key"], "b")
        self.assertEqual(curl.parse("curl -u me:pw https://x.io")["auth"]["username"], "me")
        f = curl.parse("curl -F a=1 -F f=@/tmp/x.txt https://x.io")
        self.assertEqual(f["body"]["form"][1]["type"], "file")
        with self.assertRaises(curl.CurlError):
            curl.parse("curl -s")
        with self.assertRaises(curl.CurlError):
            curl.parse("")

    def test_generate(self):
        r = store.new_request(method="POST", url="https://x.io/a")
        r["body"].update({"mode": "raw", "raw": '{"a": "it\'s"}'})
        for lang in curl.LANGUAGES:
            out = curl.generate(r, {}, lang)
            self.assertIn("https://x.io/a", out)
        self.assertIn("-X POST", curl.generate(r, {}, "cURL"))
        # the generated cURL parses back into the same request
        back = curl.parse(curl.generate(r, {}, "cURL"))
        self.assertEqual(back["body"]["raw"], r["body"]["raw"])


class ImportTests(unittest.TestCase):
    def test_postman_collection(self):
        doc = {"info": {"name": "Pets", "schema": "x"}, "variable": [{"key": "host", "value": "h"}],
               "auth": {"type": "bearer", "bearer": [{"key": "token", "value": "tt"}]},
               "item": [{"name": "Folder", "item": [{"name": "Get", "request": {
                   "method": "GET", "header": [{"key": "A", "value": "b"}],
                   "url": {"raw": "{{host}}/p?x=1&y=2"}}}]},
                        {"name": "Post", "request": {"method": "POST", "url": "{{host}}/p", "body": {
                            "mode": "raw", "raw": "{}", "options": {"raw": {"language": "json"}}}}}]}
        kind, c = importers.import_data(doc)
        self.assertEqual(kind, "collection")
        self.assertEqual([i["name"] for i in c["items"]], ["Get", "Post"])
        self.assertEqual(c["items"][0]["folder"], "Folder")
        self.assertEqual(c["folders"], ["Folder"])
        self.assertEqual(c["items"][0]["params"][1]["key"], "y")
        self.assertEqual(c["auth"]["token"], "tt")                                   # kept on the collection ...
        self.assertEqual(c["items"][0]["auth"]["type"], "inherit")                  # ... and inherited by its requests
        from zapi import inherit
        self.assertEqual(inherit.effective(c, c["items"][0])["auth"]["token"], "tt")
        self.assertEqual(c["items"][1]["body"]["raw_type"], "JSON")
        self.assertEqual(c["variables"][0]["key"], "host")

    def test_postman_environment_and_zapi_roundtrip(self):
        kind, e = importers.import_data({"name": "Dev", "values": [
            {"key": "a", "value": "1", "type": "default"}, {"key": "t", "value": "s", "type": "secret"}]})
        self.assertEqual(kind, "environment")
        self.assertTrue(e["vars"][1]["secret"])
        ws = store.Workspace(os.path.join(tempfile.mkdtemp(), "w.json"))
        c = ws.add_collection("C")
        ws.save_request(store.new_request("R", url="u"), c["id"])
        kind, c2 = importers.import_data(ws.export_collection(c))
        self.assertEqual(c2["items"][0]["name"], "R")
        self.assertNotEqual(c2["items"][0]["id"], c["items"][0]["id"])

    def test_openapi(self):
        spec = {"openapi": "3.0.0", "info": {"title": "API"}, "servers": [{"url": "https://a.io/v1/"}],
                "paths": {"/users": {"get": {"summary": "List", "parameters": [
                    {"name": "limit", "in": "query", "schema": {"default": 10}}]},
                    "post": {"requestBody": {"content": {"application/json": {"schema": {
                        "$ref": "#/components/schemas/U"}}}}}}},
                "components": {"schemas": {"U": {"type": "object", "properties": {
                    "name": {"type": "string"}, "age": {"type": "integer", "example": 3}}}}}}
        _, c = importers.import_data(spec)
        self.assertEqual(c["variables"][0]["value"], "https://a.io/v1")
        self.assertEqual(c["items"][0]["params"][0]["value"], "10")
        self.assertEqual(json.loads(c["items"][1]["body"]["raw"]), {"name": "string", "age": 3})

    def test_unrecognised(self):
        with self.assertRaises(importers.ImportError_):
            importers.import_data({"hello": 1})


if __name__ == "__main__":
    unittest.main()
