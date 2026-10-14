import copy
import json
import os
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["ZAPI_HOME"] = tempfile.mkdtemp(prefix="zapi-test-")

from PySide6.QtWidgets import QApplication, QDialogButtonBox  # noqa: E402

from zapi import curl, importers, store, theme, vault  # noqa: E402

app = QApplication.instance() or QApplication([])
theme.load_fonts()
app.setStyleSheet(theme.qss())

REAL = ["Bearer-hand-typed-AAA111", "api-key-hand-typed-BBB222", "cookie-hand-typed-CCC333", "param-token-DDD444",
        "form-password-EEE555", "url-token-FFF666", "userinfo-pass-GGG777", "script-secret-HHH888",
        "body-password-III999", "var-api-key-JJJ000", "client-secret-KKK111"]


def tricky_request():
    r = store.new_request("Tricky", "POST", "https://api.example.com/v1/x?access_token=url-token-FFF666&page=2")
    r["headers"] = [store.kv("Authorization", "Bearer-hand-typed-AAA111"), store.kv("X-API-Key", "api-key-hand-typed-BBB222"),
                    store.kv("Cookie", "sid=cookie-hand-typed-CCC333"), store.kv("X-Trace", "visible-trace-id"),
                    store.kv("X-Ref", "{{token}}"), store.kv("Authorization-Hint", "{{hint}}")]
    r["params"] = [store.kv("api_key", "param-token-DDD444"), store.kv("page", "2")]
    r["body"].update({"mode": "x-www-form-urlencoded", "urlencoded": [store.kv("password", "form-password-EEE555"),
                                                                       store.kv("username", "alice")],
                      "raw": '{"user": "alice", "password": "body-password-III999"}'})
    r["body"]["form"] = [store.kv("client_secret", "client-secret-KKK111", type="text"),
                         store.kv("upload", "C:/secret/file.bin", type="file")]
    r["scripts"] = {"pre": "const apiSecret = 'script-secret-HHH888';\npm.environment.set('x', 1);",
                    "test": "pm.test('ok', () => pm.response.to.have.status(200));"}
    return r


def workspace_with(req):
    ws = store.Workspace(os.path.join(tempfile.mkdtemp(), "w.json"))
    c = ws.add_collection("C")
    ws.save_request(req, c["id"])
    env = ws.add_environment("E")
    env["vars"] += [store.new_var("api_key", "var-api-key-JJJ000"),            # not marked Secret, name gives it away
                    store.new_var("host", "https://prod.example.com"), store.new_var("api_key_ref", "{{host}}"),
                    store.new_var("token_short", "abc")]
    return ws, c


@unittest.skipUnless(vault.available(), "DPAPI needs Windows")
class HandTypedSecretTests(unittest.TestCase):
    def test_everything_sensitive_is_encrypted_on_disk_and_comes_back_exactly(self):
        ws, _ = workspace_with(tricky_request())
        ws.save()
        raw = open(ws.path, encoding="utf-8").read()
        for secret in REAL:
            self.assertNotIn(secret, raw, secret)
        for readable in ("visible-trace-id", "{{token}}", "{{hint}}", "alice", "C:/secret/file.bin", "https://prod.example.com",
                         "{{host}}", "token_short", '"page"', "status(200)"):
            self.assertIn(readable, raw, readable)                      # only credentials are hidden
        again = store.Workspace(ws.path)
        self.assertEqual(again.secret_failures, 0)
        self.assertEqual(json.dumps(again.collections, sort_keys=True), json.dumps(ws.collections, sort_keys=True))
        self.assertEqual(again.environments[0]["vars"][0]["initial"], "var-api-key-JJJ000")

    def test_clean_scripts_stay_readable_and_secret_ones_are_encrypted_whole(self):
        r = tricky_request()
        ws, c = workspace_with(r)
        ws.save()
        raw = open(ws.path, encoding="utf-8").read()
        self.assertIn("pm.test('ok'", raw)                              # the tests script has no credential
        self.assertNotIn("apiSecret", raw)                              # the pre-request script does: unreadable
        loaded = store.Workspace(ws.path)
        self.assertEqual(loaded.collections[0]["items"][0]["scripts"]["pre"], r["scripts"]["pre"])

    def test_history_and_restored_tabs_are_covered_too(self):
        r = tricky_request()
        ws, c = workspace_with(r)
        ws.add_history(r, 200, 5, 5)
        ws.settings["open_tabs"] = [{"req": copy.deepcopy(r), "cid": c["id"], "persisted": False, "clean": True}]
        ws.save()
        raw = open(ws.path, encoding="utf-8").read()
        for secret in REAL:
            self.assertNotIn(secret, raw, secret)
        again = store.Workspace(ws.path)
        self.assertEqual(again.history[0]["request"]["headers"][0]["value"], "Bearer-hand-typed-AAA111")
        self.assertEqual(again.settings["open_tabs"][0]["req"]["params"][0]["value"], "param-token-DDD444")


class ShareTests(unittest.TestCase):
    def test_strip_removes_credentials_but_keeps_the_request_usable(self):
        r = tricky_request()
        s = vault.strip_request(r)
        flat = json.dumps(s)
        for secret in REAL[:9] + ["client-secret-KKK111"]:
            self.assertNotIn(secret, flat, secret)
        self.assertEqual([h["value"] for h in s["headers"]], ["", "", "", "visible-trace-id", "{{token}}", "{{hint}}"])
        self.assertEqual(s["url"], "https://api.example.com/v1/x?access_token=&page=2")
        self.assertEqual(s["params"][1]["value"], "2")
        self.assertIn("pm.environment.set('x', 1);", s["scripts"]["pre"])      # the code is kept, the literal is not
        self.assertIn("const apiSecret = ''", s["scripts"]["pre"])
        self.assertEqual(json.loads(s["body"]["raw"]), {"user": "alice", "password": ""})
        self.assertEqual(s["body"]["form"][1]["value"], "C:/secret/file.bin")
        self.assertEqual(r["headers"][0]["value"], "Bearer-hand-typed-AAA111")   # the original is untouched

    def test_environment_export_blanks_secret_looking_variables(self):
        ws, _ = workspace_with(tricky_request())
        out = ws.export_environment(ws.environments[0])
        by = {v["key"]: v for v in out["vars"]}
        self.assertEqual((by["api_key"]["value"], by["api_key"]["secret"]), ("", True))
        self.assertEqual(by["host"]["value"], "https://prod.example.com")
        self.assertEqual(by["api_key_ref"]["value"], "{{host}}")                 # a reference is not a secret
        self.assertEqual(by["token_short"]["value"], "abc")                      # too short to be a credential

    def test_shared_history_and_collections_carry_no_credentials(self):
        ws, c = workspace_with(tricky_request())
        ws.add_history(c["items"][0], 200, 1, 1)
        for doc in (ws.export_history(), ws.export_collection(c), ws.export_all_collections()):
            for secret in REAL[:9]:
                self.assertNotIn(secret, json.dumps(doc), secret)
        self.assertIn("Bearer-hand-typed-AAA111", json.dumps(ws.export_history(include_secrets=True)))

    def test_detection_rules(self):
        yes = ['pm.environment.set("token", "abc123xyz789")', "const apiKey = 'sk_live_51Habcdefgh'",
               "headers: {'Authorization': 'Bearer abcdefghijklmnop'}", '{"password": "hunter2hunter2"}',
               'const k = "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6"']
        no = ['pm.environment.get("token")', 'pm.environment.set("token", pm.response.json().token)',
              '{"password": "{{pw}}"}', 'const id = "550e8400-e29b-41d4-a716-446655440000"',
              "pm.test('Status is 200', () => {})", 'const name = "Asha"', "const token = '{{t}}'", 'x = "short"']
        for t in yes:
            self.assertTrue(vault.contains_secret_literal(t), t)
        for t in no:
            self.assertFalse(vault.contains_secret_literal(t), t)
        self.assertTrue(vault.url_has_secret("https://u:p@x.io/"))
        self.assertTrue(vault.url_has_secret("https://x.io/a?sig=abcdef&b=1"))
        self.assertFalse(vault.url_has_secret("{{base}}/a?token={{t}}"))
        self.assertFalse(vault.url_has_secret("https://x.io/a?page=1"))
        for n in ("Authorization", "x-api-key", "client_secret", "Set-Cookie", "key", "access_token", "Password"):
            self.assertTrue(vault.is_sensitive_name(n), n)
        for n in ("Content-Type", "Accept", "page", "name", "keyboard"):
            self.assertFalse(vault.is_sensitive_name(n), n)


class BackupCryptoTests(unittest.TestCase):
    DOC = {"zapi": "workspace", "secret": "tok-123", "unicode": "päss ✓ 日本", "big": ["x" * 100] * 50}

    def test_round_trip_and_nothing_readable_in_the_file(self):
        enc = vault.encrypt_backup(self.DOC, "correct horse battery staple", n=2 ** 14)
        text = json.dumps(enc)
        self.assertEqual(enc["zapi"], "encrypted-backup")
        for needle in ("tok-123", "workspace", "päss", "xxxxxxxx"):
            self.assertNotIn(needle, text)
        self.assertEqual(vault.decrypt_backup(json.loads(text), "correct horse battery staple"), self.DOC)
        self.assertNotEqual(enc["data"], vault.encrypt_backup(self.DOC, "correct horse battery staple", n=2 ** 14)["data"])
        self.assertEqual(vault.decrypt_backup(vault.encrypt_backup(self.DOC, "päss✓日本pässword", n=2 ** 14), "päss✓日本pässword"), self.DOC)

    def test_wrong_passphrase_and_tampering_are_rejected(self):
        enc = vault.encrypt_backup(self.DOC, "right-passphrase", n=2 ** 14)
        with self.assertRaises(vault.BackupError):
            vault.decrypt_backup(enc, "wrong-passphrase")
        with self.assertRaises(vault.BackupError):
            vault.decrypt_backup(enc, "")
        import base64
        flipped = dict(enc)
        raw = bytearray(base64.b64decode(enc["data"]))
        raw[len(raw) // 2] ^= 1
        flipped["data"] = base64.b64encode(bytes(raw)).decode()
        with self.assertRaises(vault.BackupError):
            vault.decrypt_backup(flipped, "right-passphrase")
        for field, value in (("n", 2 ** 15), ("salt", enc["nonce"]), ("version", 2)):
            bad = dict(enc)
            bad[field] = value
            with self.assertRaises(vault.BackupError):
                vault.decrypt_backup(bad, "right-passphrase")
        truncated = {k: v for k, v in enc.items() if k != "mac"}
        with self.assertRaises(vault.BackupError):
            vault.decrypt_backup(truncated, "right-passphrase")
        with self.assertRaises(vault.BackupError):
            vault.decrypt_backup({"zapi": "other"}, "x")

    def test_hostile_cost_parameters_are_refused_before_any_work_is_done(self):
        enc = vault.encrypt_backup(self.DOC, "pass-phrase-1", n=2 ** 14)
        for n in (2 ** 30, 3, 1):
            bad = dict(enc)
            bad["n"] = n
            t = time.time()
            with self.assertRaises(vault.BackupError):
                vault.decrypt_backup(bad, "pass-phrase-1")
            self.assertLess(time.time() - t, 1.0)

    def test_large_backup(self):
        big = {"history": [{"id": str(i), "request": {"url": "https://x.io/" + "a" * 200}} for i in range(5000)]}
        t = time.time()
        enc = vault.encrypt_backup(big, "pass-phrase-2", n=2 ** 14)
        self.assertEqual(vault.decrypt_backup(enc, "pass-phrase-2"), big)
        self.assertLess(time.time() - t, 20)


class UiFlowTests(unittest.TestCase):
    def setUp(self):
        from zapi.app import MainWindow
        import zapi.app as app_mod
        self.app_mod = app_mod
        self.ws, self.c = workspace_with(tricky_request())
        self.win = MainWindow(self.ws)
        self.win.show()
        self.saved = {n: getattr(app_mod, n) for n in ("PassphraseDialog", "QFileDialog", "QMessageBox")}
        self.tmp = tempfile.mkdtemp()
        self.answers = []
        self.messages = []
        outer = self

        class FakePass:
            def __init__(self, parent=None, confirm=True, allow_plain=False, **kw):
                self.confirm, self.allow_plain = confirm, allow_plain
                self.plain, self.passphrase = False, ""

            def exec(self):
                if not outer.answers:
                    return False
                a = outer.answers.pop(0)
                if a is None:
                    return False
                self.plain, self.passphrase = (True, "") if a == "<plain>" else (False, a)
                return True

        class FakeFile:
            @staticmethod
            def getSaveFileName(*a, **k):
                return os.path.join(outer.tmp, "backup.json"), ""

        class FakeBox:
            AcceptRole, DestructiveRole, Cancel, Yes, No = 0, 1, 2, 3, 4

            def __init__(self, *a):
                self.buttons = []

            def setWindowTitle(self, *a): pass
            def setText(self, *a): pass
            def setInformativeText(self, *a): pass

            def addButton(self, b, role=None):
                self.buttons.append(b)
                return b if not isinstance(b, str) else b

            def exec(self): pass

            def clickedButton(self):
                return self.buttons[0]           # "Merge"

            @staticmethod
            def warning(parent, title, text):
                outer.messages.append((title, text))

            @staticmethod
            def question(*a):
                return FakeBox.Yes

            @staticmethod
            def information(*a):
                outer.messages.append(("info", a[-1]))
        app_mod.PassphraseDialog, app_mod.QFileDialog, app_mod.QMessageBox = FakePass, FakeFile, FakeBox
        self.FakeBox = FakeBox

    def tearDown(self):
        for n, v in self.saved.items():
            setattr(self.app_mod, n, v)
        self.win.close()

    def test_backup_with_a_passphrase_is_encrypted_and_restores(self):
        self.answers = ["my backup passphrase"]
        self.win.export_what("workspace")
        path = os.path.join(self.tmp, "backup.json")
        raw = open(path, encoding="utf-8").read()
        for secret in REAL:
            self.assertNotIn(secret, raw)
        kind, doc = importers.import_file(path)
        self.assertEqual(kind, "encrypted")
        # wrong, then right passphrase: restoring into a fresh workspace
        from zapi.app import MainWindow
        fresh = store.Workspace(os.path.join(tempfile.mkdtemp(), "f.json"))
        win2 = MainWindow(fresh)
        self.answers = ["not the passphrase", "my backup passphrase"]
        msg = win2.apply_import(kind, doc)
        win2.close()
        self.assertIn("workspace backup", msg)
        self.assertEqual(fresh.collections[0]["items"][0]["headers"][0]["value"], "Bearer-hand-typed-AAA111")
        self.assertEqual(fresh.environments[0]["vars"][0]["initial"], "var-api-key-JJJ000")

    def test_cancelling_or_failing_three_times_imports_nothing(self):
        doc = vault.encrypt_backup(self.ws.export_all_collections(include_secrets=True), "good-passphrase-1", n=2 ** 14)
        before = len(self.ws.collections)
        self.answers = [None]
        self.assertEqual(self.win.apply_import("encrypted", doc), "")
        self.answers = ["bad-1-passphrase", "bad-2-passphrase", "bad-3-passphrase"]
        self.assertEqual(self.win.apply_import("encrypted", doc), "")
        self.assertTrue(any("Wrong passphrase" in m[1] for m in self.messages))
        self.assertEqual(len(self.ws.collections), before)
        self.answers = ["good-passphrase-1"]
        self.assertIn("collection", self.win.apply_import("encrypted", doc))
        self.assertEqual(len(self.ws.collections), before + 1)

    def test_readable_backup_still_possible_after_a_warning(self):
        self.answers = ["<plain>"]
        self.win.export_what("workspace")
        raw = open(os.path.join(self.tmp, "backup.json"), encoding="utf-8").read()
        self.assertIn("Bearer-hand-typed-AAA111", raw)
        self.assertEqual(importers.import_data(json.loads(raw))[0], "workspace")

    def test_pasting_an_encrypted_backup_is_recognised(self):
        doc = vault.encrypt_backup({"zapi": "history", "entries": []}, "pass-phrase-3", n=2 ** 14)
        self.assertEqual(curl.parse_any(json.dumps(doc))[0], "encrypted")

    def test_passphrase_dialog_validation(self):
        from zapi.dialogs import PassphraseDialog
        ok = lambda d: d.bb.button(QDialogButtonBox.Ok).isEnabled()   # noqa: E731
        d = PassphraseDialog(confirm=True, allow_plain=True)
        self.assertFalse(ok(d))
        d.first.setText("short")
        self.assertFalse(ok(d))
        self.assertIn("at least 8", d.err.text())
        d.first.setText("long enough passphrase")
        d.second.setText("different")
        self.assertFalse(ok(d))
        self.assertIn("different", d.err.text())
        d.second.setText("long enough passphrase")
        self.assertTrue(ok(d))
        d._ok()
        self.assertEqual((d.passphrase, d.plain), ("long enough passphrase", False))
        p = PassphraseDialog(confirm=True, allow_plain=True)
        p.plain_box.setChecked(True)
        self.assertTrue(ok(p))
        p._ok()
        self.assertEqual((p.passphrase, p.plain), ("", True))
        e = PassphraseDialog(confirm=False)
        self.assertFalse(ok(e))
        e.first.setText("x")
        self.assertTrue(ok(e))
        self.assertTrue(e.second.isHidden())

    def test_editor_warns_about_secret_literals(self):
        from zapi.request_view import RequestView
        v = RequestView(tricky_request(), values_provider=lambda cid: {"token": "t", "hint": "h"})
        v._check_variables()
        self.assertIn("pre-request script", v.warn.text())
        self.assertIn("body", v.warn.text())
        self.assertIn("encrypted on disk", v.warn.text())
        clean = RequestView(store.new_request(url="http://x"), values_provider=lambda cid: {})
        clean._check_variables()
        self.assertTrue(clean.warn.isHidden())


if __name__ == "__main__":
    unittest.main()
