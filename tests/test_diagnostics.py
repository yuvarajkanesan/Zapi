import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["ZAPI_HOME"] = tempfile.mkdtemp(prefix="zapi-test-")

from PySide6.QtWidgets import QApplication  # noqa: E402

from zapi import VERSION, diagnostics, store, theme  # noqa: E402

app = QApplication.instance() or QApplication([])
theme.load_fonts()


class DiagnosticsTests(unittest.TestCase):
    def test_contains_the_facts_and_none_of_the_data(self):
        ws = store.Workspace(os.path.join(tempfile.mkdtemp(), "w.json"))
        c = ws.add_collection("Secret project name")
        r = store.new_request("Internal call", url="https://internal.corp/api?token=abc123def456")
        r["auth"].update({"type": "bearer", "token": "super-secret-token"})
        ws.save_request(r, c["id"])
        ws.settings.update({"proxy": "http://user:pw@proxy.corp:8080", "client_cert": "C:/certs/me.pem"})
        text = diagnostics.info(ws)
        self.assertIn(f"Zapi {VERSION}", text)
        self.assertIn("Collections: 1", text)
        self.assertIn("Proxy: custom", text)
        self.assertIn("Client certificate: set", text)
        for private in ("Secret project name", "Internal call", "internal.corp", "super-secret-token", "abc123def456",
                        "user:pw", "proxy.corp", "C:/certs"):
            self.assertNotIn(private, text, private)

    def test_menu_action_copies_it(self):
        from zapi.app import MainWindow
        w = MainWindow(store.Workspace(os.path.join(tempfile.mkdtemp(), "w.json")))
        w.copy_diagnostics()
        self.assertIn(f"Zapi {VERSION}", QApplication.clipboard().text())
        w.close()


if __name__ == "__main__":
    unittest.main()
