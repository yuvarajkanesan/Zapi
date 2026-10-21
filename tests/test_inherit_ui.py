import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["ZAPI_HOME"] = tempfile.mkdtemp(prefix="zapi-test-")

from PySide6.QtCore import QObject, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from tests.qt_cleanup import purge  # noqa: E402
from tests.test_inherit import kv, make, request  # noqa: E402
from zapi import store, theme  # noqa: E402
from zapi.collection_settings import AuthForm, CollectionSettingsDialog  # noqa: E402
from zapi.request_view import AUTH_TYPES  # noqa: E402

app = QApplication.instance() or QApplication([])
theme.load_fonts()
app.setStyleSheet(theme.qss())


class Base(unittest.TestCase):
    def setUp(self):
        purge()
        self.ws, self.c = make()
        self.top = request("Top")
        self.admin = request("Admin", "api/admin")
        self.c["items"] = [self.top, self.admin]

    def tearDown(self):
        purge()

    def window(self):
        from zapi.app import MainWindow
        w = MainWindow(self.ws)
        w.show()
        return w


class RequestTabTests(Base):
    def test_inherit_is_the_first_choice_and_the_default(self):
        self.assertEqual(AUTH_TYPES[0][0], "inherit")
        w = self.window()
        w.open_saved_request(self.top["id"])
        v = w.current_view()
        self.assertEqual(v.auth_type.currentData(), "inherit")
        self.assertEqual(v.auth_type.itemData(0), "inherit")
        w.close()

    def test_notes_explain_what_the_parents_add(self):
        w = self.window()
        w.open_saved_request(self.admin["id"])
        v = w.current_view()
        self.assertIn("Inherited from folder", v.inherit_note.text())
        self.assertIn("Basic auth", v.inherit_note.text())
        self.assertFalse(v.headers_note.isHidden())
        self.assertIn("X-Team", v.headers_note.text())
        self.assertFalse(v.script_notes["pre"].isHidden())
        self.assertIn("collection", v.script_notes["pre"].text())
        # an unsaved request in no collection has nothing to inherit
        w.new_request("")
        v2 = w.current_view()
        self.assertIn("not in a collection", v2.inherit_note.text())
        self.assertTrue(v2.headers_note.isHidden())
        w.close()

    def test_choosing_an_own_auth_type_stops_inheriting(self):
        w = self.window()
        w.open_saved_request(self.top["id"])
        v = w.current_view()
        v.auth_type.setCurrentIndex(v.auth_type.findData("bearer"))
        v.auth_widgets[("bearer", "token")].setText("own")
        r = v.collect()
        self.assertEqual((r["auth"]["type"], r["auth"]["token"]), ("bearer", "own"))
        eff = self.ws.effective_request(r, self.c["id"])
        self.assertEqual(eff["auth"]["token"], "own")
        w.close()

    def test_sending_uses_the_inherited_values(self):
        import zapi.app as app_mod
        captured = []

        class Fake(QObject):
            done = Signal(object)

            def __init__(self, req, scopes, opts, jar, info, parent=None):
                super().__init__(parent)
                captured.append(req)

            def start(self):
                pass
        real = app_mod.SendThread
        app_mod.SendThread = Fake
        try:
            w = self.window()
            w.open_saved_request(self.admin["id"])
            v = w.current_view()
            v.url.setText("http://127.0.0.1:1/x")
            w.send_view(v)
        finally:
            app_mod.SendThread = real
        sent = captured[0]
        self.assertEqual(sent["auth"]["type"], "basic")
        self.assertEqual(sent["auth"]["username"], "root")
        self.assertTrue(any(h["key"] == "X-Team" for h in sent["headers"]))
        self.assertIn("// coll pre", sent["scripts"]["pre"])
        # the request in the tab is untouched
        self.assertEqual(v.collect()["auth"]["type"], "inherit")
        self.assertEqual(v.collect()["headers"], [])
        w.close()

    def test_copy_as_curl_and_code_include_the_inheritance(self):
        w = self.window()
        w.open_saved_request(self.top["id"])
        v = w.current_view()
        v.url.setText("https://x.io/a")
        text = v.copy_text_for("curl")
        self.assertIn("X-Team: shop", text)
        self.assertIn("Authorization: Bearer COLL-TOKEN", text)
        shown = []
        import zapi.app as app_mod
        real = app_mod.CodeDialog
        app_mod.CodeDialog = lambda req, values, parent: type("D", (), {"exec": lambda s: shown.append(req)})()
        try:
            w.show_code(v)
        finally:
            app_mod.CodeDialog = real
        self.assertEqual(shown[0]["auth"]["type"], "bearer")
        w.sidebar.copy_request_text(self.admin, "curl", self.c["id"])
        self.assertIn("Authorization: Basic", QApplication.clipboard().text())
        w.close()


class SettingsDialogTests(Base):
    def test_auth_form_round_trip_for_every_type(self):
        for allow in (False, True):
            f = AuthForm(allow_inherit=allow)
            keys = [f.type.itemData(i) for i in range(f.type.count())]
            self.assertEqual("inherit" in keys, allow)
            self.assertIn("bearer", keys)
        f = AuthForm(False)
        f.set_auth({**store.AUTH_DEFAULTS, "type": "apikey", "key": "X-K", "value": "V", "in": "query"})
        a = f.get_auth()
        self.assertEqual((a["type"], a["key"], a["value"], a["in"]), ("apikey", "X-K", "V", "query"))
        f.type.setCurrentIndex(f.type.findData("oauth2"))
        self.assertEqual(f.get_auth()["type"], "oauth2")
        self.assertEqual(f.get_auth()["key"], "")                               # fields of other types are not carried over

    def test_collection_dialog_loads_and_saves(self):
        d = CollectionSettingsDialog(self.ws, self.c["id"])
        d.show()
        self.assertIn("Collection settings", d.windowTitle())
        self.assertEqual(d.auth.get_auth()["token"], "COLL-TOKEN")
        self.assertEqual([h["key"] for h in d.headers.rows()][:2], ["X-Team", "X-Env"])
        self.assertEqual(d.pre[1].toPlainText(), "// coll pre")
        d.auth.set_auth({**store.AUTH_DEFAULTS, "type": "basic", "username": "u", "password": "p"})
        d.headers.set_rows([kv("X-New", "1")])
        d.pre[1].setPlainText("console.log('x')")
        d._save()
        self.assertEqual(self.c["auth"]["username"], "u")
        self.assertEqual([h["key"] for h in self.c["headers"]], ["X-New"])
        self.assertEqual(self.c["scripts"]["pre"], "console.log('x')")
        self.assertEqual(store.Workspace(self.ws.path).collections[0]["auth"]["username"], "u")        # persisted

    def test_folder_dialog_inherit_means_nothing_stored(self):
        d = CollectionSettingsDialog(self.ws, self.c["id"], "api/admin")
        d.show()
        self.assertIn("Folder settings", d.windowTitle())
        self.assertEqual(d.auth.get_auth()["username"], "root")
        self.assertIn("inherit", [d.auth.type.itemData(i) for i in range(d.auth.type.count())])
        d.auth.type.setCurrentIndex(d.auth.type.findData("inherit"))
        d.headers.set_rows([])
        d.pre[1].setPlainText("")
        d.test[1].setPlainText("")
        d._save()
        self.assertNotIn("api/admin", self.c["folder_settings"])                  # all defaults: the entry is removed
        d2 = CollectionSettingsDialog(self.ws, self.c["id"], "api/admin")
        self.assertEqual(d2.auth.type.currentData(), "inherit")
        d2.auth.type.setCurrentIndex(d2.auth.type.findData("none"))
        d2._save()
        self.assertEqual(self.c["folder_settings"]["api/admin"]["auth"]["type"], "none")
        self.assertEqual(self.ws.effective_request(self.admin, self.c["id"])["auth"]["type"], "none")

    def test_summary_text(self):
        self.assertEqual(CollectionSettingsDialog.summary(self.c), "auth, 3 header(s), scripts")
        self.assertEqual(CollectionSettingsDialog.summary(self.c, "api/admin"), "auth, scripts")
        self.assertEqual(CollectionSettingsDialog.summary(self.c, "nothing"), "")


class MenuTests(Base):
    def test_menu_signal_opens_the_dialog_and_refreshes_open_tabs(self):
        import zapi.collection_settings as cs
        w = self.window()
        w.open_saved_request(self.top["id"])
        v = w.current_view()
        opened = []

        class Fake:
            def __init__(self, ws, cid, folder, parent):
                opened.append((cid, folder))

            def exec(self):
                self.coll_changed = True
                return 1
        real = cs.CollectionSettingsDialog
        cs.CollectionSettingsDialog = Fake
        try:
            self.c["auth"] = {**store.AUTH_DEFAULTS, "type": "basic", "username": "changed", "password": "x"}
            w.sidebar.settingsRequested.emit(self.c["id"], "")
        finally:
            cs.CollectionSettingsDialog = real
        self.assertEqual(opened, [(self.c["id"], "")])
        self.assertIn("Basic auth", v.inherit_note.text())                          # the open tab saw the change
        w.sidebar.settingsRequested.emit("no-such-collection", "")                  # an unknown id is ignored
        w.close()


if __name__ == "__main__":
    unittest.main()


class ScriptsTabTests(Base):
    def test_one_scripts_tab_with_pre_request_and_post_response(self):
        from zapi.request_view import RequestView
        v = RequestView(store.new_request(url="http://x"))
        v.show()
        titles = [v.rtabs.bar.tabText(i) for i in range(v.rtabs.bar.count())]
        self.assertEqual(titles, ["Params", "Authorization", "Headers", "Body", "Scripts"])     # like Postman
        self.assertEqual(v.script_btns["pre"].text(), "Pre-request")
        self.assertEqual(v.script_btns["test"].text(), "Post-response")
        v.rtabs.bar.setCurrentIndex(4)
        self.assertIs(v.script_stack.currentWidget().findChild(type(v.script_pre)), v.script_pre)
        v.script_btns["test"].click()
        self.assertIs(v.script_stack.currentWidget().findChild(type(v.script_test)), v.script_test)
        v.script_btns["pre"].click()
        self.assertEqual(v.script_stack.currentIndex(), 0)

    def test_dots_show_which_scripts_have_content_and_values_round_trip(self):
        from zapi.request_view import RequestView
        v = RequestView(store.new_request(url="http://x"))
        v.show()
        self.assertEqual(v.rtabs.bar.tabText(4), "Scripts")
        v.script_test.setPlainText("pm.test('ok', () => {});")
        self.assertEqual(v.rtabs.bar.tabText(4), "Scripts •")
        self.assertEqual(v.script_btns["test"].text(), "Post-response •")
        self.assertEqual(v.script_btns["pre"].text(), "Pre-request")
        v.script_pre.setPlainText("console.log(1)")
        self.assertEqual(v.script_btns["pre"].text(), "Pre-request •")
        r = v.collect()
        self.assertEqual(r["scripts"], {"pre": "console.log(1)", "test": "pm.test('ok', () => {});"})
        w = RequestView(r)
        self.assertEqual(w.script_pre.toPlainText(), "console.log(1)")
        self.assertEqual(w.script_test.toPlainText(), r["scripts"]["test"])
        self.assertEqual(w.rtabs.bar.tabText(4), "Scripts •")

    def test_show_script_jumps_to_the_right_editor(self):
        from zapi.request_view import RequestView
        v = RequestView(store.new_request(url="http://x"))
        v.show()
        v.show_script("test")
        self.assertEqual(v.rtabs.bar.currentIndex(), 4)
        self.assertTrue(v.script_btns["test"].isChecked())
        self.assertEqual(v.script_stack.currentIndex(), 1)
        v.show_script("pre")
        self.assertTrue(v.script_btns["pre"].isChecked())
        self.assertEqual(v.script_stack.currentIndex(), 0)

    def test_inherited_script_notes_appear_on_the_matching_editor(self):
        w = self.window()
        w.open_saved_request(self.admin["id"])
        v = w.current_view()
        self.assertIn("collection", v.script_notes["pre"].text())
        self.assertIn("folder", v.script_notes["test"].text())
        w.close()

    def test_collection_dialog_uses_the_same_names(self):
        d = CollectionSettingsDialog(self.ws, self.c["id"])
        self.assertEqual([d.tabs.tabText(i) for i in range(d.tabs.count())],
                         ["Authorization", "Headers", "Pre-request", "Post-response"])
