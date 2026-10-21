import copy
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["ZAPI_HOME"] = tempfile.mkdtemp(prefix="zapi-test-")

from zapi import exporters, importers, inherit, loadtest, runner, store, vault, wsfile  # noqa: E402


def kv(k, v, on=True):
    return {"key": k, "value": v, "enabled": on}


def make():
    """A collection with auth, headers and scripts on the collection and on folders 'api' and 'api/admin'."""
    ws = store.Workspace(os.path.join(tempfile.mkdtemp(), "w.json"))
    c = ws.add_collection("Shop")
    c["auth"] = {**store.AUTH_DEFAULTS, "type": "bearer", "token": "COLL-TOKEN"}
    c["headers"] = [kv("X-Team", "shop"), kv("X-Env", "from-collection"), kv("X-Off", "no", False)]
    c["scripts"] = {"pre": "// coll pre", "test": "// coll test"}
    c["folders"] = ["api", "api/admin"]
    c["folder_settings"] = {
        "api": {"headers": [kv("X-Env", "from-api"), kv("X-Api", "1")], "scripts": {"pre": "// api pre", "test": ""}},
        "api/admin": {"auth": {**store.AUTH_DEFAULTS, "type": "basic", "username": "root", "password": "pw"},
                      "scripts": {"pre": "// admin pre", "test": "// admin test"}},
    }
    return ws, c


def request(name, folder="", **kw):
    r = store.new_request(name=name, url="{{base}}/x")
    r["folder"] = folder
    r.update(kw)
    return r


class ResolveTests(unittest.TestCase):
    def setUp(self):
        self.ws, self.c = make()

    def test_new_requests_inherit_by_default(self):
        self.assertEqual(store.new_request()["auth"]["type"], "inherit")
        self.assertEqual(store.new_collection()["auth"], {"type": "none"})

    def test_auth_comes_from_the_nearest_parent(self):
        top = request("top")
        auth, where = inherit.resolve_auth(self.c, top)
        self.assertEqual((auth["type"], auth["token"]), ("bearer", "COLL-TOKEN"))
        self.assertIn("collection", where)
        api = request("api", "api")
        self.assertEqual(inherit.resolve_auth(self.c, api)[0]["type"], "bearer")          # the api folder sets none: skip up
        admin = request("admin", "api/admin")
        auth, where = inherit.resolve_auth(self.c, admin)
        self.assertEqual((auth["type"], auth["username"]), ("basic", "root"))
        self.assertIn("api/admin", where)

    def test_request_own_auth_wins_and_none_means_none(self):
        own = request("own", "api/admin", auth={**store.AUTH_DEFAULTS, "type": "apikey", "key": "K", "value": "V"})
        self.assertEqual(inherit.effective(self.c, own)["auth"]["type"], "apikey")
        none = request("none", "api/admin", auth={**store.AUTH_DEFAULTS, "type": "none"})
        self.assertEqual(inherit.effective(self.c, none)["auth"]["type"], "none")
        self.assertEqual(inherit.resolve_auth(self.c, none)[1], "request")

    def test_a_folder_can_switch_auth_off_for_its_requests(self):
        self.c["folder_settings"]["api"]["auth"] = {"type": "none"}
        self.assertEqual(inherit.effective(self.c, request("a", "api"))["auth"]["type"], "none")
        self.assertEqual(inherit.effective(self.c, request("t"))["auth"]["type"], "bearer")

    def test_nothing_set_means_no_auth(self):
        ws = store.Workspace(os.path.join(tempfile.mkdtemp(), "w.json"))
        c = ws.add_collection("Empty")
        r = request("r")
        self.assertEqual(inherit.effective(c, r)["auth"]["type"], "none")
        self.assertEqual(inherit.resolve_auth(c, r)[1], "")
        self.assertEqual(inherit.effective(None, r)["auth"]["type"], "none")           # not in a collection at all
        self.assertIn("not in a collection", inherit.describe_auth(None, r))
        self.assertIn("Nothing to inherit", inherit.describe_auth(c, r))

    def test_headers_merge_and_the_request_wins(self):
        r = request("r", "api/admin", headers=[kv("x-env", "from-request"), kv("Own", "1")])
        eff = inherit.effective(self.c, r)
        got = {h["key"].lower(): h["value"] for h in eff["headers"]}
        self.assertEqual(got["x-team"], "shop")                                         # from the collection
        self.assertEqual(got["x-api"], "1")                                             # from the folder
        self.assertEqual(got["x-env"], "from-request")                                  # the request overrides both
        self.assertEqual(got["own"], "1")
        self.assertNotIn("x-off", got)                                                  # disabled rows are not inherited
        self.assertEqual(len([h for h in eff["headers"] if h["key"].lower() == "x-env"]), 1)
        # a deeper folder replaces an outer one
        eff2 = inherit.effective(self.c, request("r2", "api"))
        self.assertEqual({h["key"]: h["value"] for h in eff2["headers"]}["X-Env"], "from-api")

    def test_scripts_run_outermost_first(self):
        eff = inherit.effective(self.c, request("r", "api/admin", scripts={"pre": "// own pre", "test": "// own test"}))
        self.assertEqual(eff["scripts"]["pre"].split("\n"), ["// coll pre", "// api pre", "// admin pre", "// own pre"])
        self.assertEqual(eff["scripts"]["test"].split("\n"), ["// coll test", "// admin test", "// own test"])
        self.assertEqual(inherit.effective(self.c, request("t"))["scripts"]["pre"], "// coll pre")

    def test_effective_never_changes_the_original(self):
        r = request("r", "api")
        before = copy.deepcopy(r)
        inherit.effective(self.c, r)
        self.assertEqual(r, before)
        self.assertEqual(r["auth"]["type"], "inherit")

    def test_descriptions_for_the_ui(self):
        r = request("r", "api/admin")
        self.assertIn("Inherited from folder", inherit.describe_auth(self.c, r))
        self.assertIn("Basic auth", inherit.describe_auth(self.c, r))
        info = inherit.describe_parents(self.c, r)
        self.assertIn("X-Team", info["headers"])
        self.assertIn("collection", info["pre"])
        self.assertIn("folder", info["pre"])
        self.assertEqual(self.ws.parents_info(r, self.c["id"])["auth"], inherit.describe_auth(self.c, r))
        self.assertEqual(inherit.describe_parents(None, r), {"headers": "", "pre": "", "test": ""})


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"auth": self.headers.get("Authorization", ""), "team": self.headers.get("X-Team", ""),
                           "env": self.headers.get("X-Env", "")}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class RealRequestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.base = f"http://127.0.0.1:{cls.srv.server_port}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        self.ws, self.c = make()
        self.c["items"] = [request("top"), request("admin", "api/admin")]
        self.scopes = {"environment": {"base": self.base}, "globals": {}, "collection": {}}
        self.opts = {"verify_ssl": True, "follow_redirects": True, "timeout": 5}

    def test_runner_sends_the_inherited_headers_and_auth(self):
        import base64
        seen = []
        summary = runner.run_collection(self.c, self.scopes, self.opts, on_step=seen.append)
        self.assertEqual(summary.requests, 2)
        self.assertEqual(summary.failed_requests, 0)
        from zapi import executor
        out_top = executor.execute(inherit.effective(self.c, self.c["items"][0]), self.scopes, self.opts)
        got = json.loads(out_top.resp.body)
        self.assertEqual(got["auth"], "Bearer COLL-TOKEN")
        self.assertEqual((got["team"], got["env"]), ("shop", "from-collection"))
        out_admin = executor.execute(inherit.effective(self.c, self.c["items"][1]), self.scopes, self.opts)
        got = json.loads(out_admin.resp.body)
        self.assertEqual(got["auth"], "Basic " + base64.b64encode(b"root:pw").decode())
        self.assertEqual(got["env"], "from-api")

    def test_load_test_items_carry_the_inheritance(self):
        items = loadtest.build_items(self.c)
        self.assertEqual([i["auth"]["type"] for i in items], ["bearer", "basic"])
        self.assertTrue(any(h["key"] == "X-Team" for h in items[0]["headers"]))

    def test_workspace_helper_and_sidebar_style_use(self):
        r = self.c["items"][1]
        eff = self.ws.effective_request(r, self.c["id"])
        self.assertEqual(eff["auth"]["type"], "basic")
        self.assertEqual(self.ws.effective_request(r, "")["auth"]["type"], "none")      # unknown collection: just no auth
        from zapi import curl
        text = curl.generate(eff, {"base": self.base}, "cURL")
        self.assertIn("X-Team: shop", text)
        self.assertIn("Authorization: Basic cm9vdDpwdw==", text)                          # base64 of root:pw


class StorageTests(unittest.TestCase):
    def test_settings_survive_save_load_and_folder_changes(self):
        ws, c = make()
        ws.save()
        again = store.Workspace(ws.path)
        got = again.collections[0]
        self.assertEqual(got["auth"]["token"], "COLL-TOKEN")
        self.assertEqual(got["folder_settings"]["api/admin"]["auth"]["username"], "root")
        self.assertEqual(got["headers"][0]["key"], "X-Team")
        again.rename_folder(got["id"], "api", "v2")
        self.assertEqual(sorted(got["folder_settings"]), ["v2", "v2/admin"])
        again.delete_folder(got["id"], "v2/admin")
        self.assertEqual(sorted(got["folder_settings"]), ["v2"])
        again.delete_folder(got["id"], "v2")
        self.assertEqual(got["folder_settings"], {})

    def test_old_workspaces_get_empty_settings(self):
        c = store.Workspace._fix_collection({"id": "x", "name": "Old", "items": [], "folders": []})
        self.assertEqual((c["auth"], c["headers"], c["scripts"], c["folder_settings"]),
                         ({"type": "none"}, [], {"pre": "", "test": ""}, {}))

    def test_whole_file_is_encrypted_and_collection_secrets_are_protected_inside(self):
        if not vault.available():
            self.skipTest("DPAPI needs Windows")
        ws, c = make()
        c["headers"].append(kv("Authorization", "Bearer hand-typed-secret-123"))
        ws.save()
        raw = open(ws.path, "rb").read()
        self.assertNotIn(b"COLL-TOKEN", raw)
        inner = wsfile.unpack_text(raw)
        for secret in ("COLL-TOKEN", "pw-folder", "hand-typed-secret-123"):
            self.assertNotIn(secret, inner)
        self.assertNotIn('"password":"pw"', inner)
        self.assertEqual(store.Workspace(ws.path).collections[0]["auth"]["token"], "COLL-TOKEN")

    def test_shared_exports_leave_collection_credentials_out(self):
        ws, c = make()
        shared = ws.export_collection(c)
        text = json.dumps(shared)
        self.assertNotIn("COLL-TOKEN", text)
        self.assertNotIn('"password": "pw"', text)
        self.assertEqual(shared["auth"]["type"], "bearer")                               # the setup stays, the secret goes
        self.assertEqual(shared["folder_settings"]["api/admin"]["auth"]["username"], "root")
        self.assertIn("COLL-TOKEN", json.dumps(ws.export_collection(c, include_secrets=True)))
        back = importers.import_data(shared)[1]
        self.assertEqual(back["folder_settings"]["api"]["headers"][1]["key"], "X-Api")
        self.assertEqual(back["scripts"]["pre"], "// coll pre")
        self.assertEqual(back["headers"][0]["key"], "X-Team")


class PostmanTests(unittest.TestCase):
    def doc(self):
        return {"info": {"name": "P", "schema": "x"},
                "auth": {"type": "bearer", "bearer": [{"key": "token", "value": "T"}]},
                "event": [{"listen": "prerequest", "script": {"exec": ["console.log('coll')"]}}],
                "item": [{"name": "Admin", "auth": {"type": "basic", "basic": [{"key": "username", "value": "u"},
                                                                                {"key": "password", "value": "p"}]},
                          "event": [{"listen": "test", "script": {"exec": ["// folder test"]}}],
                          "item": [{"name": "In folder", "request": {"method": "GET", "url": "http://x/1"}},
                                   {"name": "Public", "request": {"method": "GET", "url": "http://x/2",
                                                                  "auth": {"type": "noauth"}}},
                                   {"name": "Own", "request": {"method": "GET", "url": "http://x/3", "auth": {
                                       "type": "apikey", "apikey": [{"key": "key", "value": "K"},
                                                                    {"key": "value", "value": "V"}]}}}]},
                         {"name": "Top", "request": {"method": "GET", "url": "http://x/4"}}]}

    def test_import_keeps_the_hierarchy(self):
        _, c = importers.import_data(self.doc())
        self.assertEqual(c["auth"]["type"], "bearer")
        self.assertEqual(c["scripts"]["pre"], "console.log('coll')")
        self.assertEqual(c["folder_settings"]["Admin"]["auth"]["username"], "u")
        self.assertEqual(c["folder_settings"]["Admin"]["scripts"]["test"], "// folder test")
        by = {r["name"]: r for r in c["items"]}
        self.assertEqual(by["In folder"]["auth"]["type"], "inherit")
        self.assertEqual(by["Top"]["auth"]["type"], "inherit")
        self.assertEqual(by["Public"]["auth"]["type"], "none")                             # noauth stays "no auth"
        self.assertEqual(by["Own"]["auth"]["type"], "apikey")
        self.assertEqual(inherit.effective(c, by["In folder"])["auth"]["username"], "u")
        self.assertEqual(inherit.effective(c, by["Top"])["auth"]["token"], "T")
        self.assertEqual(inherit.effective(c, by["Public"])["auth"]["type"], "none")
        self.assertEqual(inherit.effective(c, by["In folder"])["scripts"]["test"], "// folder test")

    def test_export_round_trips(self):
        _, c = importers.import_data(self.doc())
        out, skipped = exporters.to_postman(c)
        self.assertEqual(skipped, 0)
        self.assertEqual(out["auth"]["type"], "bearer")
        self.assertEqual(out["event"][0]["listen"], "prerequest")
        admin = next(i for i in out["item"] if i["name"] == "Admin")
        self.assertEqual(admin["auth"]["type"], "basic")
        self.assertEqual(admin["event"][0]["listen"], "test")
        items = {i["name"]: i for i in admin["item"] + [x for x in out["item"] if "request" in x]}
        self.assertNotIn("auth", items["In folder"]["request"])                            # inherits: no auth key
        self.assertEqual(items["Public"]["request"]["auth"], {"type": "noauth"})            # explicit none survives
        self.assertEqual(items["Own"]["request"]["auth"]["type"], "apikey")
        _, again = importers.import_data(json.loads(json.dumps(out)))
        by = {r["name"]: r for r in again["items"]}
        self.assertEqual(by["Public"]["auth"]["type"], "none")
        self.assertEqual(by["In folder"]["auth"]["type"], "inherit")
        self.assertEqual(again["folder_settings"]["Admin"]["auth"]["password"], "p")

    def test_a_plain_collection_exports_without_new_keys(self):
        ws = store.Workspace(os.path.join(tempfile.mkdtemp(), "w.json"))
        c = ws.add_collection("Plain")
        r = request("r")
        r["auth"]["type"] = "none"
        c["items"] = [r]
        out, _ = exporters.to_postman(c)
        self.assertNotIn("auth", out)
        self.assertNotIn("event", out)
        self.assertNotIn("auth", out["item"][0]["request"])


if __name__ == "__main__":
    unittest.main()
