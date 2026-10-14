import json
import os
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["ZAPI_HOME"] = tempfile.mkdtemp(prefix="zapi-test-")

from PySide6.QtWidgets import QApplication  # noqa: E402

from zapi import exporters, importers, store, theme  # noqa: E402
from zapi.app import MainWindow  # noqa: E402

app = QApplication.instance() or QApplication([])
theme.load_fonts()
app.setStyleSheet(theme.qss())


def sample_workspace():
    ws = store.Workspace(os.path.join(tempfile.mkdtemp(), "w.json"))
    c = ws.add_collection("Shop")
    c["variables"].append(store.kv("base", "https://shop.example"))
    ws.add_folder(c["id"], "Admin/Users")
    r = store.new_request("Create", "POST", "{{base}}/users")
    r.update({"folder": "Admin/Users", "params": [store.kv("page", "1"), store.kv("off", "x", False)],
              "headers": [store.kv("X-A", "1"), store.kv("X-B", "2", False)]})
    r["body"].update({"mode": "raw", "raw_type": "JSON", "raw": '{"a": 1}'})
    r["scripts"] = {"pre": "pm.environment.set('a', 1);\nconsole.log('x');", "test": "pm.test('t', () => {});"}
    r["examples"] = [{"name": "201 Created", "status": 201, "headers": [["Content-Type", "application/json"]],
                      "body": '{"id": 1}'}]
    ws.save_request(r, c["id"])
    top = store.new_request("Top", "GET", "{{base}}/top")
    top["body"].update({"mode": "x-www-form-urlencoded", "urlencoded": [store.kv("k", "v")]})
    ws.save_request(top, c["id"])
    sock = store.new_kind_request("websocket")
    ws.save_request(sock, c["id"])
    e = ws.add_environment("Prod")
    e["vars"] += [store.new_var("base", "https://p", "https://p-cur"), store.new_var("token", "s3cret", secret=True)]
    ws.globals["vars"].append(store.new_var("g", "1"))
    for i in range(3):
        ws.add_history(r, 200, 10 + i, 5)
    ws.settings["mock"] = {"port": 4000, "cors": True, "routes": [
        {"name": "n", "method": "GET", "path": "/x", "status": 200, "delay_ms": 0, "headers": [], "body": "b",
         "enabled": True}]}
    return ws, c


class PostmanExportTests(unittest.TestCase):
    def test_round_trip_keeps_folders_scripts_examples_and_params(self):
        ws, c = sample_workspace()
        doc, skipped = exporters.to_postman(c)
        self.assertEqual(skipped, 1)                               # the WebSocket request has no Postman form
        self.assertEqual(doc["info"]["schema"], exporters.SCHEMA)
        self.assertEqual([i["name"] for i in doc["item"]], ["Admin", "Top"])
        back = importers.from_postman_collection(json.loads(json.dumps(doc)))
        self.assertEqual(back["folders"], ["Admin", "Admin/Users"])
        names = {r["name"]: r for r in back["items"]}
        r = names["Create"]
        self.assertEqual((r["folder"], r["method"], r["url"]), ("Admin/Users", "POST", "{{base}}/users"))
        self.assertEqual([(p["key"], p["enabled"]) for p in r["params"]], [("page", True), ("off", False)])
        self.assertEqual([(h["key"], h["enabled"]) for h in r["headers"]], [("X-A", True), ("X-B", False)])
        self.assertEqual((r["body"]["mode"], r["body"]["raw_type"], r["body"]["raw"]), ("raw", "JSON", '{"a": 1}'))
        self.assertEqual(r["scripts"], {"pre": "pm.environment.set('a', 1);\nconsole.log('x');",
                                        "test": "pm.test('t', () => {});"})
        self.assertEqual((r["examples"][0]["status"], r["examples"][0]["body"]), (201, '{"id": 1}'))
        self.assertEqual(names["Top"]["body"]["urlencoded"][0]["key"], "k")
        self.assertEqual(back["variables"][0]["value"], "https://shop.example")

    def test_every_auth_type_round_trips(self):
        samples = {
            "bearer": {"token": "T"}, "basic": {"username": "u", "password": "p"},
            "digest": {"username": "u", "password": "p"}, "apikey": {"key": "K", "value": "V", "in": "query"},
            "oauth1": {"consumer_key": "ck", "consumer_secret": "cs", "token": "t", "token_secret": "ts",
                       "sig_method": "HMAC-SHA256", "realm": "r"},
            "oauth2": {"token": "AT", "o2_grant": "Password", "o2_token_url": "https://t", "o2_auth_url": "https://a",
                       "o2_client_id": "id", "o2_client_secret": "sec", "o2_scope": "s", "o2_prefix": "Bearer"},
            "aws": {"access_key": "AK", "secret_key": "SK", "session_token": "ST", "region": "eu-west-1",
                    "service": "s3"},
            "hawk": {"hawk_id": "i", "hawk_key": "k", "hawk_algorithm": "sha1", "hawk_ext": "e"},
            "ntlm": {"username": "u", "password": "p", "domain": "D", "workstation": "W"},
        }
        c = store.new_collection("Auth")
        for atype, fields in samples.items():
            r = store.new_request(atype, url="http://x")
            r["auth"].update({"type": atype, **fields})
            c["items"].append(r)
        back = importers.from_postman_collection(json.loads(json.dumps(exporters.to_postman(c)[0])))
        for r in back["items"]:
            want = {"type": r["name"], **samples[r["name"]]}
            got = {k: r["auth"][k] for k in want}
            self.assertEqual(got, want, r["name"])

    def test_form_graphql_and_binary_bodies(self):
        c = store.new_collection("B")
        a = store.new_request("form", "POST", "http://x")
        a["body"].update({"mode": "form-data", "form": [store.kv("f", "v", type="text"), store.kv("up", "/tmp/a", type="file")]})
        g = store.new_request("gql", "POST", "http://x")
        g["body"].update({"mode": "GraphQL", "query": "{ a }", "variables": '{"x": 1}'})
        b = store.new_request("bin", "POST", "http://x")
        b["body"].update({"mode": "binary", "file": "C:/x.bin"})
        c["items"] = [a, g, b]
        back = {r["name"]: r for r in importers.from_postman_collection(
            json.loads(json.dumps(exporters.to_postman(c)[0])))["items"]}
        self.assertEqual([(f["key"], f["type"]) for f in back["form"]["body"]["form"]], [("f", "text"), ("up", "file")])
        self.assertEqual((back["gql"]["body"]["query"], back["gql"]["body"]["variables"]), ("{ a }", '{"x": 1}'))
        self.assertEqual(back["bin"]["body"]["file"], "C:/x.bin")


class WorkspaceTransferTests(unittest.TestCase):
    def test_history_export_import_merges_without_duplicates(self):
        ws, _ = sample_workspace()
        doc = ws.export_history()
        self.assertEqual(doc["zapi"], "history")
        kind, entries = importers.import_data(json.loads(json.dumps(doc)))
        self.assertEqual((kind, len(entries)), ("history", 3))
        other = store.Workspace(os.path.join(tempfile.mkdtemp(), "o.json"))
        self.assertEqual(other.import_history(entries), 3)
        self.assertEqual(other.import_history(entries), 0)             # importing twice adds nothing
        self.assertEqual([h["ms"] for h in other.history], [h["ms"] for h in ws.history])
        other.history[0]["at"] = time.time() + 100
        self.assertEqual(other.import_history([{"id": "new", "at": 1.0, "status": 200,
                                                "request": {"name": "n", "url": "u"}}]), 1)
        self.assertEqual(other.history[-1]["id"], "new")
        self.assertEqual(other.history[-1]["request"]["method"], "GET")  # normalised

    def test_collections_and_environments_multi_export(self):
        ws, c = sample_workspace()
        ws.add_collection("Second")
        k, cols = importers.import_data(json.loads(json.dumps(ws.export_all_collections())))
        self.assertEqual((k, [x["name"] for x in cols]), ("collections", ["Shop", "Second"]))
        self.assertEqual(cols[0]["folders"], ["Admin", "Admin/Users"])
        k, envs = importers.import_data(json.loads(json.dumps(ws.export_all_environments())))
        self.assertEqual(k, "environments")
        self.assertNotIn("s3cret", json.dumps(ws.export_all_environments()))      # shared exports hold no secrets
        self.assertEqual({v["key"] for v in envs[0]["vars"]}, {"base", "token"})

    def test_full_backup_replace_and_merge(self):
        ws, c = sample_workspace()
        doc = json.loads(json.dumps(ws.export_workspace()))
        self.assertIn("s3cret", json.dumps(doc))                       # a backup keeps secrets and current values
        self.assertEqual(importers.import_data(doc)[0], "workspace")
        fresh = store.Workspace(os.path.join(tempfile.mkdtemp(), "f.json"))
        fresh.add_collection("Mine")
        summary = fresh.import_workspace(doc, replace=True)
        self.assertIn("1 collection", summary)
        self.assertEqual([x["name"] for x in fresh.collections], ["Shop"])
        self.assertEqual(len(fresh.collections[0]["items"]), 3)
        self.assertEqual(fresh.environments[0]["vars"][0]["current"], "https://p-cur")
        self.assertEqual(len(fresh.history), 3)
        self.assertEqual(fresh.settings["mock"]["routes"][0]["path"], "/x")
        merged = store.Workspace(os.path.join(tempfile.mkdtemp(), "m.json"))
        merged.add_collection("Mine")
        merged.globals["vars"].append(store.new_var("g", "keep-mine"))
        merged.import_workspace(doc)
        self.assertEqual(sorted(x["name"] for x in merged.collections), ["Mine", "Shop"])
        self.assertEqual(merged.globals["vars"][0]["initial"], "keep-mine")
        merged.import_workspace(doc)                                    # same ids again: overwritten, not duplicated
        self.assertEqual(len(merged.collections), 2)
        self.assertEqual(len(merged.environments), 1)
        merged.save()
        self.assertEqual(len(store.Workspace(merged.path).collections), 2)


class FakeDialog:
    """Stands in for QFileDialog so the export actions run without a window."""
    path = ""

    @staticmethod
    def getSaveFileName(*a, **k):
        return FakeDialog.path, ""


class UiTransferTests(unittest.TestCase):
    def setUp(self):
        self.ws, self.c = sample_workspace()
        self.win = MainWindow(self.ws)
        self.win.show()
        import zapi.app as app_mod
        self.app_mod, self.real = app_mod, app_mod.QFileDialog
        app_mod.QFileDialog = FakeDialog
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        self.app_mod.QFileDialog = self.real
        self.win.close()

    def out(self, name):
        FakeDialog.path = os.path.join(self.tmp, name)
        return FakeDialog.path

    def test_export_actions_write_importable_files(self):
        for what, kind in (("collections", "collections"), ("environments", "environments"), ("history", "history")):
            path = self.out(f"{what}.json")
            self.win.export_what(what)
            self.assertEqual(importers.import_file(path)[0], kind, what)
        # a single open request
        self.win.open_saved_request(self.c["items"][0]["id"])
        path = self.out("req.json")
        self.win.export_current_request()
        kind, coll = importers.import_file(path)
        self.assertEqual((kind, coll["items"][0]["name"]), ("collection", "Create"))

    def test_apply_import_handles_every_kind(self):
        before = len(self.ws.collections)
        msg = self.win.apply_import("collections", [store.new_collection("A"), store.new_collection("B")])
        self.assertIn("2 collection", msg)
        self.assertEqual(len(self.ws.collections), before + 2)
        self.assertIn("1 environment", self.win.apply_import("environments", [store.new_environment("Z")]))
        entries = self.ws.export_history()["entries"][:1]
        entries[0]["id"] = "fresh-id"
        self.assertIn("1 history", self.win.apply_import("history", entries))
        routes = {"zapi": "mock", "routes": [{"method": "GET", "path": "/m", "status": 200, "body": "", "headers": []}]}
        self.assertIn("1 mock", self.win.apply_import("mock", routes))
        self.assertEqual(self.ws.settings["mock"]["routes"][0]["path"], "/m")
        self.assertEqual(self.win.apply_import("nonsense", None), "")

    def test_pasted_history_file_imports_through_the_url_bar(self):
        doc = self.ws.export_history()
        doc["entries"][0]["id"] = "pasted-1"
        self.win.new_request("")
        v = self.win.current_view()
        text = json.dumps(doc)
        v.url.setText(text)
        v._url_typed(text)
        self.assertTrue(any(h["id"] == "pasted-1" for h in self.ws.history))
        self.assertEqual(self.win.tab_bar.count(), 0)

    def test_history_buttons_and_menus_exist(self):
        sb = self.win.sidebar
        self.assertEqual(sb.export_btn.text(), "Export")
        got = []
        sb.exportRequested.connect(got.append)
        sb.history.parent().findChildren(type(sb.export_btn))[0].click()      # History tab: Export
        self.assertEqual(got, ["history"])


if __name__ == "__main__":
    unittest.main()
