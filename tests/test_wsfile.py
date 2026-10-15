import json
import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from zapi import config, store, vault, wsfile  # noqa: E402


@unittest.skipUnless(vault.available(), "DPAPI needs Windows")
class WorkspaceFileTests(unittest.TestCase):
    def make(self):
        ws = store.Workspace(os.path.join(tempfile.mkdtemp(), "workspace.json"))
        c = ws.add_collection("Pet store")
        for i in range(30):
            ws.save_request(store.new_request(name=f"Request {i}", url=f"https://api.example.com/pets/{i}",
                                              method="GET"), c["id"])
        e = ws.add_environment("Prod")
        e["vars"].append(store.new_var("host", "https://prod.example.com"))
        return ws

    def test_file_is_unreadable_without_windows_and_much_smaller_than_json(self):
        ws = self.make()
        ws.save()
        raw = open(ws.path, "rb").read()
        self.assertTrue(raw.startswith(wsfile.MAGIC))
        for visible in (b"Pet store", b"api.example.com", b"prod.example.com", b"Request 1", b'"collections"'):
            self.assertNotIn(visible, raw)                                  # nothing readable, not even names or URLs
        plain = json.dumps(ws.collections, indent=2).encode()
        self.assertLess(len(raw), len(plain) / 3)                           # compressed
        again = store.Workspace(ws.path)
        self.assertIsNone(again.unreadable)
        self.assertEqual(len(again.collections[0]["items"]), 30)
        self.assertEqual(again.environments[0]["vars"][0]["initial"], "https://prod.example.com")

    def test_old_plain_json_is_read_and_upgraded_on_the_next_save(self):
        path = os.path.join(tempfile.mkdtemp(), "workspace.json")
        ws = store.Workspace(path)
        ws.add_collection("Legacy")
        ws.settings["encrypt_secrets"] = False
        ws.save()
        self.assertFalse(wsfile.is_packed(open(path, "rb").read()))         # encryption off: plain, readable JSON
        old = store.Workspace(path)
        self.assertEqual(old.collections[0]["name"], "Legacy")
        old.settings["encrypt_secrets"] = True
        old.save()
        self.assertTrue(wsfile.is_packed(open(path, "rb").read()))
        self.assertEqual(store.Workspace(path).collections[0]["name"], "Legacy")

    def test_a_file_from_another_account_is_kept_and_reported(self):
        ws = self.make()
        ws.save()
        raw = open(ws.path, "rb").read()
        # as if DPAPI could not open it: flip bytes inside the encrypted blob
        broken = raw[:20] + bytes(b ^ 0xFF for b in raw[20:60]) + raw[60:]
        open(ws.path, "wb").write(broken)
        loaded = store.Workspace(ws.path)
        self.assertEqual(loaded.collections, [])
        self.assertIn("Windows could not decrypt", loaded.unreadable["reason"])
        self.assertEqual(open(loaded.unreadable["copy"], "rb").read(), broken)   # the original is preserved untouched

    def test_damaged_and_newer_files(self):
        with self.assertRaises(wsfile.WorkspaceFileError):
            wsfile.unpack(wsfile.MAGIC + bytes([99]) + b"x")
        with self.assertRaises(wsfile.WorkspaceFileError):
            wsfile.unpack(b"not a workspace")
        with self.assertRaises(wsfile.WorkspaceFileError):
            wsfile.unpack(wsfile.MAGIC + bytes([1]) + vault.protect_bytes(b"not zlib"))

    def test_the_app_tells_the_user_instead_of_silently_starting_empty(self):
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        from zapi import theme
        theme.load_fonts()
        app.setStyleSheet(theme.qss())
        from zapi.app import MainWindow
        import zapi.app as app_mod
        ws = self.make()
        ws.unreadable = {"reason": "Windows could not decrypt it.", "copy": "C:/x/workspace.json.unreadable-1"}
        shown = []
        real, app_mod.QMessageBox = app_mod.QMessageBox, type("QMB", (), {"warning": staticmethod(
            lambda *a: shown.append(a[2]))})
        w = MainWindow(ws)
        try:
            w._warn_unreadable_workspace()
        finally:
            app_mod.QMessageBox = real
            w.close()
        self.assertEqual(len(shown), 1)
        self.assertIn("kept as", shown[0])
        self.assertIn("workspace.json.unreadable-1", shown[0])
        self.assertIsNone(ws.unreadable)

    def test_save_is_atomic_bytes(self):
        path = os.path.join(tempfile.mkdtemp(), "x.bin")
        config.save_bytes(path, b"abc")
        config.save_bytes(path, b"defg")
        self.assertEqual(open(path, "rb").read(), b"defg")
        self.assertEqual([f for f in os.listdir(os.path.dirname(path)) if f.endswith(".tmp")], [])


if __name__ == "__main__":
    unittest.main()
