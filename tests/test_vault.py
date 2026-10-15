import copy
import json
import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["ZAPI_HOME"] = tempfile.mkdtemp(prefix="zapi-test-")

from zapi import exporters, store, vault, wsfile  # noqa: E402

SECRETS = ["s3cret-env-initial", "s3cret-env-current", "pw-basic-9x", "tok-bearer-7q", "oauth-client-secret-1",
           "refresh-token-2", "aws-secret-key-3", "hawk-key-4", "mqtt-pass-5", "apikey-value-6",
           "history-token-8", "tab-token-9", "proxy-pass-0"]


def build():
    ws = store.Workspace(os.path.join(tempfile.mkdtemp(), "w.json"))
    c = ws.add_collection("Shop")
    r = store.new_request("Create", "POST", "https://shop.example/users")
    r["auth"].update({"type": "basic", "username": "alice", "password": "pw-basic-9x", "token": "tok-bearer-7q",
                      "o2_client_secret": "oauth-client-secret-1", "o2_refresh_token": "refresh-token-2",
                      "secret_key": "aws-secret-key-3", "hawk_key": "hawk-key-4", "value": "apikey-value-6"})
    ref = store.new_request("Uses variables", "GET", "https://shop.example/me")
    ref["auth"].update({"type": "bearer", "token": "{{token}}", "password": "{{ pw }}"})
    mq = store.new_kind_request("mqtt")
    mq["proto"]["password"] = "mqtt-pass-5"
    for x in (r, ref, mq):
        ws.save_request(x, c["id"])
    env = ws.add_environment("Prod")
    env["vars"] += [store.new_var("token", "s3cret-env-initial", "s3cret-env-current", secret=True),
                    store.new_var("host", "https://prod.example.com"), store.new_var("alias", "{{host}}", secret=True)]
    ws.globals["vars"].append(store.new_var("g_secret", "s3cret-env-initial", secret=True))
    h = copy.deepcopy(r)
    h["auth"]["token"] = "history-token-8"
    ws.add_history(h, 200, 5, 5)
    tab = copy.deepcopy(r)
    tab["auth"]["token"] = "tab-token-9"
    ws.settings["open_tabs"] = [{"req": tab, "cid": c["id"], "persisted": False, "clean": True}]
    ws.settings["proxy"] = "http://user:proxy-pass-0@proxy.corp:8080"
    return ws


@unittest.skipUnless(vault.available(), "DPAPI needs Windows")
class VaultTests(unittest.TestCase):
    def test_round_trip_and_tokens_hide_the_text(self):
        for text in ("hello", "p@ss wörd ✓ 日本", "x" * 5000):
            token = vault.protect(text)
            self.assertTrue(token.startswith("dpapi:"))
            self.assertNotIn(text[:8], token)
            self.assertEqual(vault.unprotect(token), text)
        self.assertEqual(vault.unprotect("plain"), "plain")             # not a token: returned as is
        self.assertEqual(vault.protect("same"), vault.protect("same"))  # stable within a session

    def test_garbage_and_foreign_tokens_are_rejected(self):
        for bad in ("dpapi:!!!notbase64", "dpapi:" + "QUJD" * 10, "dpapi:"):
            with self.assertRaises(vault.VaultError):
                vault.unprotect(bad)

    def test_file_on_disk_contains_no_secrets_but_loads_back_exactly(self):
        ws = build()
        ws.save()
        raw = wsfile.read_text(ws.path)
        for secret in SECRETS:
            self.assertNotIn(secret, raw, secret)
        for kept in ("alice", "https://shop.example/users", "https://prod.example.com", "Prod", "{{token}}", "{{ pw }}",
                     "{{host}}"):
            self.assertIn(kept, raw, kept)                  # names, URLs and {{references}} stay readable
        self.assertGreaterEqual(raw.count("dpapi:"), len(SECRETS) - 1)
        again = store.Workspace(ws.path)
        self.assertEqual(again.secret_failures, 0)
        self.assertEqual(json.dumps([again.collections, again.environments, again.globals, again.history],
                                    sort_keys=True),
                         json.dumps([ws.collections, ws.environments, ws.globals, ws.history], sort_keys=True))
        self.assertEqual(again.settings["proxy"], "http://user:proxy-pass-0@proxy.corp:8080")
        self.assertEqual(again.settings["open_tabs"][0]["req"]["auth"]["token"], "tab-token-9")
        # in memory nothing was changed by saving
        self.assertEqual(ws.environments[0]["vars"][0]["current"], "s3cret-env-current")

    def test_non_secret_variables_stay_plain_and_secret_flag_matters(self):
        ws = store.Workspace(os.path.join(tempfile.mkdtemp(), "w.json"))
        e = ws.add_environment("E")
        e["vars"] += [store.new_var("open", "visible-value"), store.new_var("hidden", "hide-me", secret=True)]
        ws.save()
        raw = wsfile.read_text(ws.path)
        self.assertIn("visible-value", raw)
        self.assertNotIn("hide-me", raw)
        e["vars"][1]["secret"] = False                          # un-marking a secret writes it as plain text again
        ws.save()
        self.assertIn("hide-me", wsfile.read_text(ws.path))

    def test_old_plain_text_files_are_migrated_on_the_next_save(self):
        ws = build()
        ws.settings["encrypt_secrets"] = False
        ws.save()
        raw = wsfile.read_text(ws.path)
        self.assertIn("pw-basic-9x", raw)                       # the old, unencrypted format
        loaded = store.Workspace(ws.path)
        self.assertEqual(loaded.collections[0]["items"][0]["auth"]["password"], "pw-basic-9x")
        loaded.settings["encrypt_secrets"] = True
        loaded.save()
        self.assertNotIn("pw-basic-9x", wsfile.read_text(ws.path))
        self.assertEqual(store.Workspace(ws.path).collections[0]["items"][0]["auth"]["password"], "pw-basic-9x")

    def test_unreadable_secrets_become_empty_and_are_counted(self):
        ws = build()
        ws.save()
        data = json.loads(wsfile.read_text(ws.path))
        item = data["collections"][0]["items"][0]
        item["auth"]["password"] = "dpapi:" + "AAAA" * 8                # as if saved by another Windows account
        data["environments"][0]["vars"][0]["current"] = "dpapi:not-base64!!"
        json.dump(data, open(ws.path, "w", encoding="utf-8"))
        loaded = store.Workspace(ws.path)
        self.assertEqual(loaded.secret_failures, 2)
        self.assertEqual(loaded.collections[0]["items"][0]["auth"]["password"], "")
        self.assertEqual(loaded.environments[0]["vars"][0]["current"], "")
        self.assertEqual(loaded.collections[0]["items"][0]["auth"]["token"], "tok-bearer-7q")   # the rest is intact
        self.assertEqual(loaded.environments[0]["vars"][0]["initial"], "s3cret-env-initial")

    def test_sharing_exports_leave_credentials_out_but_keep_references(self):
        ws = build()
        c = ws.collections[0]
        shared = json.dumps(ws.export_collection(c))
        for secret in ("pw-basic-9x", "tok-bearer-7q", "oauth-client-secret-1", "aws-secret-key-3", "mqtt-pass-5",
                       "apikey-value-6"):
            self.assertNotIn(secret, shared, secret)
        self.assertIn("alice", shared)                              # the user name is not a secret
        self.assertIn("{{token}}", shared)
        self.assertIn("pw-basic-9x", json.dumps(ws.export_collection(c, include_secrets=True)))
        self.assertNotIn("tok-bearer-7q", json.dumps(ws.export_all_collections()))
        postman = json.dumps(exporters.to_postman(vault.strip_collection(c))[0])
        self.assertNotIn("pw-basic-9x", postman)
        self.assertIn("pw-basic-9x", json.dumps(ws.export_workspace()))     # a full backup is complete (and warned about)
        self.assertEqual(c["items"][0]["auth"]["password"], "pw-basic-9x")  # exporting never changes the workspace
        self.assertEqual(vault.strip_request(c["items"][0])["auth"]["password"], "")


class FallbackTests(unittest.TestCase):
    def test_without_dpapi_secrets_are_stored_plain_and_say_so(self):
        real = vault.available
        vault.available = lambda: False
        try:
            ws = build()
            self.assertFalse(ws.secrets_encrypted())
            ws.save()
            self.assertIn("pw-basic-9x", wsfile.read_text(ws.path))
            self.assertEqual(store.Workspace(ws.path).secret_failures, 0)
        finally:
            vault.available = real

    def test_encrypted_file_opened_where_dpapi_is_missing_is_kept_and_not_overwritten(self):
        if not real_available():
            self.skipTest("needs Windows to create the file")
        ws = build()
        ws.save()
        before = open(ws.path, "rb").read()
        real = vault.available
        vault.available = lambda: False
        try:
            loaded = store.Workspace(ws.path)
            self.assertIsNotNone(loaded.unreadable)
            self.assertEqual(loaded.collections, [])
            self.assertTrue(os.path.isfile(loaded.unreadable["copy"]))                  # nothing is lost
            self.assertEqual(open(loaded.unreadable["copy"], "rb").read(), before)
        finally:
            vault.available = real


def real_available():
    return vault.available()


class UiTests(unittest.TestCase):
    def test_settings_toggle_and_unreadable_secret_notice(self):
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        from zapi import theme
        theme.load_fonts()
        from zapi.dialogs import SettingsDialog
        ws = store.Workspace(os.path.join(tempfile.mkdtemp(), "w.json"))
        d = SettingsDialog(ws)
        self.assertEqual(d.encrypt.isChecked(), vault.available())
        if vault.available():
            d.encrypt.setChecked(False)
            d._ok()
            self.assertFalse(ws.settings["encrypt_secrets"])
            self.assertFalse(ws.secrets_encrypted())
        from zapi.app import MainWindow
        ws.secret_failures = 3
        shown = []
        w = MainWindow(ws)
        import zapi.app as app_mod
        real, app_mod.QMessageBox = app_mod.QMessageBox, type("QMB", (), {"warning": staticmethod(
            lambda *a: shown.append(a[2]))})
        try:
            w._warn_unreadable_secrets()
        finally:
            app_mod.QMessageBox = real
            w.close()
        self.assertEqual(len(shown), 1)
        self.assertIn("3 saved secret", shown[0])
        self.assertEqual(ws.secret_failures, 0)                     # shown once


if __name__ == "__main__":
    unittest.main()
