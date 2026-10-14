import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["ZAPI_HOME"] = tempfile.mkdtemp(prefix="zapi-test-")

from PySide6.QtCore import QEvent, QPoint, Qt  # noqa: E402
from PySide6.QtGui import QHelpEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QLineEdit, QPlainTextEdit  # noqa: E402

from tests.qt_cleanup import purge  # noqa: E402
from zapi import store, theme, variable_assist as va  # noqa: E402
from zapi.request_view import RequestView  # noqa: E402

app = QApplication.instance() or QApplication([])
theme.load_fonts()
app.setStyleSheet(theme.qss())

CAT = {
    "base_url": {"value": "https://api.example.com", "scope": "Environment · Dev", "secret": False, "dynamic": False},
    "token": {"value": "s3cret-value", "scope": "Environment · Dev", "secret": True, "dynamic": False},
    "api_version": {"value": "v1", "scope": "Globals", "secret": False, "dynamic": False},
    "abc": {"value": "xyz", "scope": "Environment · Dev", "secret": False, "dynamic": False},
    "$guid": {"value": "", "scope": "Dynamic", "secret": False, "dynamic": True},
}


class PureTests(unittest.TestCase):
    def test_token_at(self):
        text = "a {{ abc }} b {{x}}"
        self.assertEqual(va.token_at(text, 5), (2, 11, "abc"))
        self.assertIsNone(va.token_at(text, 2))              # before the first brace
        self.assertEqual(va.token_at(text, 11), (2, 11, "abc"))
        self.assertIsNone(va.token_at(text, 12))
        self.assertEqual(va.token_at(text, 17)[2], "x")
        self.assertEqual(va.token_at("{{a}}{{b}}", 5)[2], "a")
        self.assertEqual(va.token_at("{{a}}{{b}}", 6)[2], "b")
        self.assertIsNone(va.token_at("{ {a} }", 3))
        self.assertIsNone(va.token_at("plain text", 4))

    def test_completion_context(self):
        c = va.completion_context
        self.assertEqual(c("x {{", 4), (4, 4, "", False))
        self.assertEqual(c("x {{ab", 6), (4, 6, "ab", False))
        self.assertEqual(c("{{ab}}", 4), (2, 4, "ab", True))               # editing inside an existing {{ab}}
        self.assertEqual(c("{{abXYZ}}", 4), (2, 7, "ab", True))             # the rest of the name is replaced too
        self.assertIsNone(c("{{ab}}", 6))                                   # cursor after the closing braces
        self.assertEqual(c("{{a-b.c$", 8)[2], "a-b.c$")
        self.assertEqual(c("{{ ab", 5), (3, 5, "ab", False))
        self.assertIsNone(c("plain", 5))
        self.assertIsNone(c("{ {a", 4))
        self.assertIsNone(c("{{a b", 5))                                    # a space ends the name

    def test_suggestions_order_and_matching(self):
        s = va.suggestions
        self.assertEqual(s(CAT, ""), ["abc", "api_version", "base_url", "token", "$guid"])
        self.assertEqual(s(CAT, "ba"), ["base_url"])
        self.assertEqual(s(CAT, "a"), ["abc", "api_version", "base_url"])   # starts with first, then contains
        self.assertEqual(s(CAT, "TOK"), ["token"])
        self.assertEqual(s(CAT, " ba "), ["base_url"])
        self.assertEqual(s(CAT, "zzz"), [])
        self.assertEqual(s(CAT, "$"), ["$guid"])
        self.assertEqual(s({}, "x"), [])

    def test_hover_text(self):
        d = va.describe_html
        ok = d("abc", CAT)
        self.assertIn("xyz", ok)
        self.assertIn("Environment · Dev", ok)
        self.assertIn("{{abc}}", ok)
        sec = d("token", CAT)
        self.assertNotIn("s3cret-value", sec)
        self.assertIn("••••", sec)
        self.assertIn("(secret)", sec)
        self.assertIn("Not defined", d("nope", CAT))
        self.assertIn("Generated again", d("$guid", CAT))
        evil = {"e": {"value": "<script>alert(1)</script>\nline2", "scope": "G", "secret": False, "dynamic": False}}
        self.assertNotIn("<script>", d("e", evil))
        self.assertIn("&lt;script&gt;", d("e", evil))
        self.assertIn("(empty)", d("e", {"e": {"value": "", "scope": "G", "secret": False, "dynamic": False}}))
        self.assertIn("…", d("e", {"e": {"value": "x" * 500, "scope": "G", "secret": False, "dynamic": False}}))
        self.assertNotIn("<b>", d("<b>", {}).replace("<b>{{", "").replace("</b>", "") if False else "")


class CatalogTests(unittest.TestCase):
    def test_precedence_scopes_secrets_and_dynamic(self):
        ws = store.Workspace(os.path.join(tempfile.mkdtemp(), "w.json"))
        c = ws.add_collection("Shop")
        c["variables"] += [store.kv("x", "from-collection"), store.kv("only_coll", "cv"), store.kv("off", "1", False)]
        ws.globals["vars"] += [store.new_var("x", "from-globals"), store.new_var("g", "gv")]
        env = ws.add_environment("Dev")
        env["vars"] += [store.new_var("x", "from-env", "cur"), store.new_var("api_key", "abcdefghijkl"),
                        store.new_var("hidden", "h", secret=True), store.new_var("short_token", "ab")]
        ws.settings["active_env"] = env["id"]
        cat = ws.variable_catalog(c["id"])
        self.assertEqual((cat["x"]["value"], cat["x"]["scope"]), ("cur", "Environment · Dev"))     # environment wins
        self.assertEqual(cat["only_coll"]["scope"], "Collection · Shop")
        self.assertEqual(cat["g"]["scope"], "Globals")
        self.assertNotIn("off", cat)                                                                # disabled
        self.assertTrue(cat["hidden"]["secret"])
        self.assertTrue(cat["api_key"]["secret"])                                                   # the name gives it away
        self.assertFalse(cat["short_token"]["secret"])
        self.assertTrue(all(cat[n]["dynamic"] for n in ("$guid", "$timestamp", "$isoTimestamp", "$randomInt")))
        self.assertEqual(ws.variable_catalog()["x"]["scope"], "Environment · Dev")                  # no collection: still fine
        ws.settings["active_env"] = ""
        self.assertEqual(ws.variable_catalog(c["id"])["x"]["scope"], "Collection · Shop")


class Base(unittest.TestCase):
    def setUp(self):
        purge()
        self.tips = []
        self.real_tip = va.show_tip
        va.show_tip = lambda pos, text, w: self.tips.append(text)
        va.set_provider(lambda: CAT)
        va.hide_popup()

    def tearDown(self):
        va.show_tip = self.real_tip
        va.set_provider(lambda: {})
        va.hide_popup()
        purge()

    # -- helpers --
    def focus(self, w):
        top = w.window()
        top.show()
        top.activateWindow()
        QTest.qWaitForWindowActive(top, 3000)
        w.setFocus()
        app.processEvents()
        self.assertTrue(w.hasFocus(), "the test window could not take focus")

    def popup(self):
        p = va._popup
        return p if p is not None and p.isVisible() else None

    def rows(self):
        p = self.popup()
        return None if p is None else [p.item(i).data(Qt.UserRole) for i in range(p.count())]

    def hover(self, w, point):
        target = w if isinstance(w, QLineEdit) else w.viewport()
        before = len(self.tips)
        QApplication.sendEvent(target, QHelpEvent(QEvent.ToolTip, point, target.mapToGlobal(point)))
        return len(self.tips) > before

    def x_of(self, le, idx):
        y = le.height() // 2
        for x in range(0, le.width()):
            if le.cursorPositionAt(QPoint(x, y)) >= idx:
                return x + 1
        return le.width() - 1

    def point_in_text(self, te, idx):
        c = te.textCursor()
        c.setPosition(idx)
        r = te.cursorRect(c)
        return QPoint(r.x() + 2, r.y() + r.height() // 2)


class HoverTests(Base):
    def line(self, text):
        le = QLineEdit(text)
        le.resize(420, 32)
        va.attach(le)
        le.show()
        app.processEvents()
        return le

    def test_line_edit_shows_value_and_scope_only_over_a_variable(self):
        le = self.line("https://x/{{abc}}/y")
        self.assertTrue(self.hover(le, QPoint(self.x_of(le, 12), 16)))               # on "abc"
        self.assertIn("xyz", self.tips[-1])
        self.assertIn("Environment · Dev", self.tips[-1])
        self.assertFalse(self.hover(le, QPoint(self.x_of(le, 3), 16)))               # on "https"
        self.assertFalse(self.hover(le, QPoint(410, 16)))                            # empty space on the right

    def test_empty_space_after_a_variable_at_the_end_is_not_the_variable(self):
        le = self.line("{{abc}}")
        self.assertTrue(self.hover(le, QPoint(self.x_of(le, 4), 16)))
        self.assertFalse(self.hover(le, QPoint(400, 16)))

    def test_undefined_secret_and_dynamic(self):
        le = self.line("{{nope}} {{token}} {{$guid}}")
        self.hover(le, QPoint(self.x_of(le, 4), 16))
        self.assertIn("Not defined", self.tips[-1])
        self.hover(le, QPoint(self.x_of(le, 12), 16))
        self.assertIn("••••", self.tips[-1])
        self.assertNotIn("s3cret-value", self.tips[-1])
        self.hover(le, QPoint(self.x_of(le, 24), 16))
        self.assertIn("Generated again", self.tips[-1])

    def test_text_editor_hover_on_the_second_line(self):
        te = QPlainTextEdit('first line\nsecond {{abc}} line\n{{base_url}}')
        te.resize(420, 120)
        va.attach(te)
        te.show()
        app.processEvents()
        self.assertTrue(self.hover(te, self.point_in_text(te, 20)))                 # inside {{abc}}
        self.assertIn("xyz", self.tips[-1])
        self.assertFalse(self.hover(te, self.point_in_text(te, 3)))                 # "first"
        self.assertFalse(self.hover(te, QPoint(380, self.point_in_text(te, 3).y())))   # right of line 1: empty
        self.assertTrue(self.hover(te, self.point_in_text(te, 36)))
        self.assertIn("api.example.com", self.tips[-1])

    def test_the_header_value_cell_from_the_bug_report(self):
        ws = store.Workspace(os.path.join(tempfile.mkdtemp(), "w.json"))
        env = ws.add_environment("Dev")
        env["vars"].append(store.new_var("abc", "xyz"))
        ws.settings["active_env"] = env["id"]
        va.set_provider(lambda: ws.variable_catalog())
        rv = RequestView(store.new_request(url="https://jsonplaceholder.typicode.com/posts/1"))
        rv.resize(900, 600)
        rv.show()
        rv.rtabs.bar.setCurrentIndex(2)
        app.processEvents()
        cell = rv.headers.table.cellWidget(0, 2)
        cell.setText("{{abc}}")
        cell.resize(220, 34)
        self.assertTrue(self.hover(cell, QPoint(self.x_of(cell, 4), 17)))
        self.assertIn("xyz", self.tips[-1])
        self.assertIn("Environment · Dev", self.tips[-1])

    def test_url_tooltip_still_works_when_not_over_a_variable(self):
        rv = RequestView(store.new_request(url="{{base_url}}/x"), values_provider=lambda cid: {"base_url": "http://h"})
        rv._check_variables()
        self.assertEqual(rv.url.toolTip(), "Sends to: http://h/x")                   # Qt shows it; we only intercept variables


class CompletionTests(Base):
    def field(self, text=""):
        le = QLineEdit(text)
        le.resize(420, 32)
        va.attach(le)
        self.focus(le)
        self.edited = []
        le.textEdited.connect(self.edited.append)
        return le

    def test_typing_braces_lists_everything_then_narrows(self):
        le = self.field()
        QTest.keyClicks(le, "{{")
        self.assertEqual(self.rows(), ["abc", "api_version", "base_url", "token", "$guid"])
        QTest.keyClicks(le, "a")
        self.assertEqual(self.rows(), ["abc", "api_version", "base_url"])
        QTest.keyClicks(le, "p")
        self.assertEqual(self.rows(), ["api_version"])
        QTest.keyClick(le, Qt.Key_Backspace)
        self.assertEqual(self.rows(), ["abc", "api_version", "base_url"])
        QTest.keyClick(le, Qt.Key_Backspace)
        QTest.keyClick(le, Qt.Key_Backspace)
        QTest.keyClick(le, Qt.Key_Backspace)
        self.assertIsNone(self.popup())                                              # the braces are gone

    def test_enter_accepts_and_completes_the_braces(self):
        le = self.field("GET ")
        QTest.keyClicks(le, "{{to")
        self.assertEqual(self.rows(), ["token"])
        before = len(self.edited)
        QTest.keyClick(le, Qt.Key_Return)
        self.assertEqual(le.text(), "GET {{token}}")
        self.assertEqual(le.cursorPosition(), len(le.text()))
        self.assertIsNone(self.popup())
        self.assertGreater(len(self.edited), before)                                 # the tab sees it as an edit

    def test_down_up_tab_and_the_arrow_wrap(self):
        le = self.field()
        QTest.keyClicks(le, "{{a")
        self.assertEqual(va._popup.currentRow(), 0)
        QTest.keyClick(le, Qt.Key_Down)
        self.assertEqual(va._popup.current_name(), "api_version")
        QTest.keyClick(le, Qt.Key_Down)
        QTest.keyClick(le, Qt.Key_Down)
        self.assertEqual(va._popup.current_name(), "abc")                            # wrapped around
        QTest.keyClick(le, Qt.Key_Up)
        self.assertEqual(va._popup.current_name(), "base_url")
        QTest.keyClick(le, Qt.Key_Tab)
        self.assertEqual(le.text(), "{{base_url}}")

    def test_editing_an_existing_variable_does_not_duplicate_the_braces(self):
        le = self.field("{{to}}")
        le.setCursorPosition(4)
        QTest.keyClick(le, "k")
        self.assertEqual(le.text(), "{{tok}}")
        self.assertEqual(self.rows(), ["token"])
        QTest.keyClick(le, Qt.Key_Return)
        self.assertEqual(le.text(), "{{token}}")

    def test_escape_closes_and_typing_reopens(self):
        le = self.field()
        QTest.keyClicks(le, "{{b")
        QTest.keyClick(le, Qt.Key_Escape)
        self.assertIsNone(self.popup())
        self.assertEqual(le.text(), "{{b")
        QTest.keyClicks(le, "a")
        self.assertEqual(self.rows(), ["base_url"])

    def test_no_match_says_so_and_enter_keeps_its_normal_job(self):
        le = self.field()
        pressed = []
        le.returnPressed.connect(lambda: pressed.append(1))
        QTest.keyClicks(le, "{{zzz")
        self.assertEqual(self.rows(), [None])
        self.assertIn("No variable matches", va._popup.itemWidget(va._popup.item(0)).text())
        QTest.keyClick(le, Qt.Key_Return)
        self.assertEqual(pressed, [1])
        self.assertEqual(le.text(), "{{zzz")
        self.assertIsNone(self.popup())

    def test_no_variables_at_all_gets_a_hint(self):
        va.set_provider(lambda: {})
        le = self.field()
        QTest.keyClicks(le, "{{")
        self.assertIn("No variables yet", va._popup.itemWidget(va._popup.item(0)).text())

    def test_ctrl_space_lists_variables_anywhere(self):
        le = self.field("abc ")
        QTest.keyClick(le, Qt.Key_Space, Qt.ControlModifier)
        self.assertEqual(self.rows()[0], "abc")
        QTest.keyClick(le, Qt.Key_Down)
        QTest.keyClick(le, Qt.Key_Return)
        self.assertEqual(le.text(), "abc {{api_version}}")                           # braces added around it
        le.setText("")
        QTest.keyClicks(le, "{{ba")
        QTest.keyClick(le, Qt.Key_Space, Qt.ControlModifier)                          # inside {{ }}: plain completion
        QTest.keyClick(le, Qt.Key_Return)
        self.assertEqual(le.text(), "{{base_url}}")

    def test_clicking_a_suggestion_and_losing_focus(self):
        le = self.field()
        QTest.keyClicks(le, "{{")
        p = va._popup
        QTest.mouseClick(p.viewport(), Qt.LeftButton, Qt.NoModifier, p.visualItemRect(p.item(3)).center())
        self.assertEqual(le.text(), "{{token}}")
        QTest.keyClicks(le, " {{a")
        self.assertIsNotNone(self.popup())
        other = QLineEdit(le.parent())
        other.show()
        other.setFocus()
        app.processEvents()
        self.assertIsNone(self.popup())

    def test_programmatic_changes_do_not_pop_anything_up(self):
        le = self.field()
        le.setText("{{")
        le.setCursorPosition(2)
        self.assertIsNone(self.popup())

    def test_read_only_and_opted_out_fields_are_left_alone(self):
        ro = QLineEdit("x")
        ro.setReadOnly(True)
        self.assertIsNone(va.attach(ro))
        out = QLineEdit()
        out.setProperty("noVariables", True)
        self.assertIsNone(va.attach(out))
        self.assertIsNone(va.attach(QApplication.instance()))
        le = QLineEdit()
        self.assertIs(va.attach(le), va.attach(le))                                  # attaching twice is harmless


class IntegrationTests(Base):
    def view(self):
        rv = RequestView(store.new_request(url=""))
        rv.resize(900, 640)
        rv.show()
        app.processEvents()
        return rv

    def test_url_bar_completion_marks_the_request_as_edited(self):
        rv = self.view()
        self.focus(rv.url)
        QTest.keyClicks(rv.url, "{{ba")
        QTest.keyClick(rv.url, Qt.Key_Return)
        self.assertEqual(rv.url.text(), "{{base_url}}")
        self.assertTrue(rv.is_dirty())

    def test_header_and_param_cells_complete(self):
        rv = self.view()
        rv.rtabs.bar.setCurrentIndex(2)
        cell = rv.headers.table.cellWidget(0, 2)
        self.focus(cell)
        QTest.keyClicks(cell, "Bearer {{to")
        QTest.keyClick(cell, Qt.Key_Return)
        self.assertEqual(rv.headers.rows()[0]["value"], "Bearer {{token}}")
        self.assertEqual(rv.headers.table.rowCount(), 2)                             # a new empty row appeared as usual
        rv.rtabs.bar.setCurrentIndex(0)
        key = rv.params.table.cellWidget(0, 1)
        self.focus(key)
        QTest.keyClicks(key, "{{api")
        QTest.keyClick(key, Qt.Key_Return)
        self.assertEqual(rv.params.rows()[0]["key"], "{{api_version}}")

    def test_body_editor_completes_in_the_middle_of_json(self):
        rv = self.view()
        rv.mode_buttons["raw"].setChecked(True)
        rv.rtabs.bar.setCurrentIndex(3)
        self.focus(rv.raw)
        QTest.keyClicks(rv.raw, '{"u": "{{ba')
        self.assertEqual(self.rows(), ["base_url"])
        QTest.keyClick(rv.raw, Qt.Key_Return)
        self.assertEqual(rv.raw.toPlainText(), '{"u": "{{base_url}}')
        self.assertTrue(rv.is_dirty())
        rv.raw.setPlainText('{"a": "{{to}}", "b": 1}')
        c = rv.raw.textCursor()
        c.setPosition(len('{"a": "{{to'))
        rv.raw.setTextCursor(c)
        self.focus(rv.raw)
        QTest.keyClick(rv.raw, "k")
        QTest.keyClick(rv.raw, Qt.Key_Return)
        self.assertEqual(rv.raw.toPlainText(), '{"a": "{{token}}", "b": 1}')          # the text around it is untouched

    def test_bulk_edit_text_completes_too(self):
        rv = self.view()
        rv.rtabs.bar.setCurrentIndex(2)
        rv.headers.set_bulk(True)
        self.focus(rv.headers.text)
        QTest.keyClicks(rv.headers.text, "X-Env: {{ab")
        QTest.keyClick(rv.headers.text, Qt.Key_Return)
        self.assertEqual(rv.headers.text.toPlainText(), "X-Env: {{abc}}")
        self.assertEqual(rv.headers.rows()[0]["value"], "{{abc}}")

    def test_auth_fields_complete(self):
        rv = self.view()
        rv.auth_type.setCurrentIndex(rv.auth_type.findData("bearer"))
        rv.rtabs.bar.setCurrentIndex(1)
        field = rv.auth_widgets[("bearer", "token")]
        self.focus(field)
        QTest.keyClicks(field, "{{to")
        QTest.keyClick(field, Qt.Key_Return)
        self.assertEqual(rv.collect()["auth"]["token"], "{{token}}")

    def test_response_search_scripts_and_response_view_have_no_variable_help(self):
        rv = self.view()
        for w in (rv.search, rv.script_pre, rv.script_test, rv.view):
            self.assertIsNone(w.findChild(va._Assist), w)
        for w in (rv.url, rv.raw, rv.gql_query, rv.gql_vars):
            self.assertIsNotNone(w.findChild(va._Assist), w)

    def test_protocol_tabs_and_the_mock_editor(self):
        from zapi.dialogs import MockDialog
        from zapi.mock import MockServer
        from zapi.proto_views import GrpcView, StreamView
        ws = StreamView(store.new_kind_request("websocket"))
        self.assertIsNotNone(ws.url_edit.findChild(va._Assist))
        self.assertIsNone(ws.log.filter.findChild(va._Assist))                       # filtering the log is not a request field
        self.assertIsNotNone(GrpcView(store.new_kind_request("grpc")).url_edit.findChild(va._Assist))
        w = store.Workspace(os.path.join(tempfile.mkdtemp(), "w.json"))
        m = MockDialog(w, MockServer(), type("B", (), {"request": type("S", (), {"connect": lambda *a: None,
                                                                                 "disconnect": lambda *a: None})()})())
        self.assertIsNone(m.body.findChild(va._Assist))                              # mock templates use {{params.x}}, not variables

    def test_real_workspace_end_to_end(self):
        ws = store.Workspace(os.path.join(tempfile.mkdtemp(), "w.json"))
        env = ws.add_environment("Dev")
        env["vars"] += [store.new_var("host", "https://dev.example.com"), store.new_var("secret_pw", "hunter22", secret=True)]
        ws.settings["active_env"] = env["id"]
        c = ws.add_collection("C")
        c["variables"].append(store.kv("page_size", "50"))
        va.set_provider(lambda: ws.variable_catalog(c["id"]))
        rv = self.view()
        self.focus(rv.url)
        QTest.keyClicks(rv.url, "{{")
        self.assertEqual(self.rows(), ["host", "page_size", "secret_pw", "$guid", "$isoTimestamp", "$randomInt", "$timestamp"])
        QTest.keyClicks(rv.url, "pa")
        self.assertEqual(self.rows(), ["page_size"])
        text = va._popup.itemWidget(va._popup.item(0)).text()
        self.assertIn("50", text)
        self.assertIn("Collection · C", text)
        QTest.keyClicks(rv.url, "s")
        QTest.keyClicks(rv.url, "")
        for _ in range(3):
            QTest.keyClick(rv.url, Qt.Key_Backspace)
        QTest.keyClicks(rv.url, "secret")
        self.assertNotIn("hunter22", va._popup.itemWidget(va._popup.item(0)).text())   # secrets are masked in the list too


if __name__ == "__main__":
    unittest.main()
