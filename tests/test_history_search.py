import os
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["ZAPI_HOME"] = tempfile.mkdtemp(prefix="zapi-test-")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from tests.qt_cleanup import purge  # noqa: E402
from zapi import store, theme  # noqa: E402
from zapi.env_view import EnvironmentsPage  # noqa: E402
from zapi.sidebar import Sidebar, day_label  # noqa: E402

app = QApplication.instance() or QApplication([])
theme.load_fonts()
app.setStyleSheet(theme.qss())

NOON = time.mktime((2026, 10, 14, 12, 0, 0, 0, 0, -1))      # a Wednesday
DAY = 86400


class DayLabelTests(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(day_label(NOON, NOON), "Today")
        self.assertEqual(day_label(NOON - 11 * 3600, NOON), "Today")              # 01:00 the same day
        self.assertEqual(day_label(NOON - DAY, NOON), "Yesterday")
        self.assertEqual(day_label(NOON - 2 * DAY, NOON), "Monday")
        self.assertEqual(day_label(NOON - 6 * DAY, NOON), "Thursday")
        self.assertEqual(day_label(NOON - 7 * DAY, NOON), "7 Oct 2026")
        self.assertEqual(day_label(NOON - 400 * DAY, NOON), "9 Sep 2025")
        self.assertEqual(day_label(NOON + DAY, NOON), "Today")                     # a clock that is slightly off


def entry(ws, url, at, status=200):
    req = store.new_request(url=url)
    ws.history.append({"id": store.new_id(), "at": at, "status": status, "ms": 5, "request": req})


class HistoryGroupingTests(unittest.TestCase):
    def setUp(self):
        purge()
        self.ws = store.Workspace(os.path.join(tempfile.mkdtemp(), "w.json"))
        now = time.time()
        entry(self.ws, "http://a/old", now - 20 * DAY)
        entry(self.ws, "http://a/today1", now - 60)
        entry(self.ws, "http://a/yest", now - DAY)
        entry(self.ws, "http://a/today2", now - 5)
        self.sb = Sidebar(self.ws)
        self.sb.resize(320, 600)
        self.sb.show()

    def tearDown(self):
        purge()

    def headings(self):
        h = self.sb.history
        return [(i, h.itemWidget(h.item(i)).text()) for i in range(h.count()) if h.item(i).data(Qt.UserRole) is None]

    def test_entries_are_grouped_newest_first_under_headings(self):
        h = self.sb.history
        self.assertEqual([t for _, t in self.headings()][:2], ["TODAY", "YESTERDAY"])
        self.assertEqual(len(self.headings()), 3)
        order = [h.item(i).data(Qt.UserRole) for i in range(h.count()) if h.item(i).data(Qt.UserRole)]
        by_id = {e["id"]: e["request"]["url"] for e in self.ws.history}
        self.assertEqual([by_id[i] for i in order], ["http://a/today2", "http://a/today1", "http://a/yest", "http://a/old"])

    def test_headings_cannot_be_clicked_or_selected(self):
        h = self.sb.history
        opened = []
        self.sb.openHistory.connect(opened.append)
        idx, _ = self.headings()[0]
        self.assertEqual(h.item(idx).flags(), Qt.NoItemFlags)
        h.itemClicked.emit(h.item(idx))
        self.assertEqual(opened, [])
        h.itemClicked.emit(h.item(idx + 1))
        self.assertEqual(len(opened), 1)

    def test_search_keeps_only_the_groups_that_match(self):
        self.sb.tabs.setCurrentIndex(1)
        self.sb.search.setText("yest")
        self.assertEqual([t for _, t in self.headings()], ["YESTERDAY"])
        self.sb.search.setText("nothing-like-this")
        self.assertEqual(self.sb.history.count(), 0)


class VariableSearchTests(unittest.TestCase):
    def setUp(self):
        purge()
        self.ws = store.Workspace(os.path.join(tempfile.mkdtemp(), "w.json"))
        env = self.ws.add_environment("Dev")
        env["vars"] += [store.new_var("base_url", "https://dev"), store.new_var("token", "abc123", secret=True),
                        store.new_var("page_size", "50")]
        self.ws.globals["vars"].append(store.new_var("region", "eu-west"))
        self.ws.settings["active_env"] = env["id"]
        self.env = env

    def tearDown(self):
        purge()

    def test_sidebar_variables_tab_has_a_search(self):
        sb = Sidebar(self.ws)
        sb.show()
        sb.tabs.setCurrentIndex(2)
        self.assertFalse(sb.search.isHidden())
        self.assertEqual(sb.search.placeholderText(), "Search variables")
        self.assertEqual(sb.var_table.rowCount(), 4)
        sb.search.setText("size")
        self.assertEqual(sb.var_table.rowCount(), 1)
        sb.search.setText("EU-W")                                  # by value, any case
        self.assertEqual(sb.var_table.item(0, 0).text(), "region  (global)")
        sb.search.setText("abc123")                                # a secret's value is not searchable
        self.assertEqual(sb.var_table.rowCount(), 0)
        sb.search.setText("tok")                                   # but its name is
        self.assertEqual(sb.var_table.rowCount(), 1)
        sb.search.setText("")
        self.assertEqual(sb.var_table.rowCount(), 4)

    def test_search_text_from_another_tab_does_not_filter_variables(self):
        sb = Sidebar(self.ws)
        sb.show()
        sb.search.setText("zzz")                                  # filtering collections
        self.assertEqual(sb.var_table.rowCount(), 4)

    def test_environment_editor_filters_rows_without_losing_them(self):
        page = EnvironmentsPage(self.ws)
        page.resize(1000, 600)
        page.show()
        page._load(self.env["id"])
        t = page.table

        def visible():
            return [t.cellWidget(i, 1).text() for i in range(t.rowCount()) if not t.isRowHidden(i)]
        self.assertEqual(visible(), ["base_url", "token", "page_size", ""])
        page.var_search.setText("PAGE")
        self.assertEqual(visible(), ["page_size", ""])                # the blank row to add more stays
        page.var_search.setText("abc")                             # by value
        self.assertEqual(visible(), ["token", ""])
        page.var_search.setText("dev")
        self.assertEqual(visible(), ["base_url", ""])
        page.var_search.setText("nope")
        self.assertEqual(visible(), [""])
        self.assertEqual(len(t.vars()), 3)                         # hidden rows are still saved
        page.save()
        self.assertEqual([v["key"] for v in self.env["vars"]], ["base_url", "token", "page_size"])
        page.var_search.setText("")
        self.assertEqual(len(visible()), 4)

    def test_filter_survives_switching_environment_and_typing_in_a_row(self):
        other = self.ws.add_environment("Prod")
        other["vars"].append(store.new_var("page_size", "10"))
        page = EnvironmentsPage(self.ws)
        page.show()
        page._load(self.env["id"])
        page.var_search.setText("page")
        page._load(other["id"])
        self.assertEqual([page.table.cellWidget(i, 1).text() for i in range(page.table.rowCount())
                          if not page.table.isRowHidden(i)], ["page_size", ""])
        page._load(self.env["id"])
        row = next(i for i in range(page.table.rowCount()) if page.table.cellWidget(i, 1).text() == "page_size")
        page.table.cellWidget(row, 1).setText("renamed")           # no longer matches, but must not vanish mid-edit
        self.assertFalse(page.table.isRowHidden(row))


if __name__ == "__main__":
    unittest.main()
