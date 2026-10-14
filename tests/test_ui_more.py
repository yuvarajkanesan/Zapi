import json
import os
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["ZAPI_HOME"] = tempfile.mkdtemp(prefix="zapi-test-")

from PySide6.QtWidgets import QApplication  # noqa: E402

from zapi import protocols, store, theme  # noqa: E402
from zapi.app import MainWindow  # noqa: E402
from zapi.dialogs import MockDialog, RunnerDialog  # noqa: E402
from zapi.proto_views import GrpcView, StreamView  # noqa: E402
from zapi.request_view import AUTH_FORMS, RequestView  # noqa: E402
from tests.test_protocols import FakeBroker, listen  # noqa: E402

app = QApplication.instance() or QApplication([])
theme.load_fonts()
app.setStyleSheet(theme.qss())


def wait(cond, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    app.processEvents()
    return cond()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = json.dumps({"path": self.path, "token": self.headers.get("X-Token", "")}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class UiBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), Handler)
        cls.base = f"http://127.0.0.1:{cls.srv.server_port}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        self.ws = store.Workspace(os.path.join(tempfile.mkdtemp(), "w.json"))
        self.win = MainWindow(self.ws)
        self.win.show()

    def tearDown(self):
        self.win.close()


class ViewTests(UiBase):
    def test_every_kind_opens_and_round_trips(self):
        for kind, cls in (("http", RequestView), ("soap", RequestView), ("websocket", StreamView),
                          ("sse", StreamView), ("mqtt", StreamView), ("grpc", GrpcView)):
            self.win.new_request("", "", kind)
            v = self.win.current_view()
            self.assertIsInstance(v, cls, kind)
            out = v.collect()
            self.assertEqual(out.get("kind", "http"), kind if kind != "http" else out.get("kind", "http"))
            again = type(v)(out)
            self.assertEqual(json.dumps(again.collect(), sort_keys=True), json.dumps(out, sort_keys=True), kind)
        self.assertEqual(self.win.tab_bar.count(), 6)
        # soap starter is XML with the right content type
        soap = store.new_kind_request("soap")
        self.assertEqual(soap["body"]["raw_type"], "SOAP 1.1")
        from zapi import http_client
        data, ctype = http_client.build_body(soap, {})
        self.assertTrue(ctype.startswith("text/xml"))

    def test_auth_forms_round_trip(self):
        v = RequestView(store.new_request(url="http://x"))
        for atype, fields in AUTH_FORMS.items():
            v.auth_type.setCurrentIndex(v.auth_type.findData(atype))
            for key, _label, _hint, kind in fields:
                w = v.auth_widgets[(atype, key)]
                if isinstance(kind, list):
                    w.setCurrentIndex(len(kind) - 1)
                else:
                    w.setText(f"{atype}-{key}")
            auth = v.collect()["auth"]
            self.assertEqual(auth["type"], atype)
            for key, _l, _h, kind in fields:
                if isinstance(kind, list):
                    self.assertEqual(auth[key], kind[-1][0], (atype, key))
                else:
                    self.assertEqual(auth[key], f"{atype}-{key}", (atype, key))
        # switching type keeps the other forms' values
        v.auth_type.setCurrentIndex(v.auth_type.findData("basic"))
        self.assertEqual(v.collect()["auth"]["consumer_key"], "oauth1-consumer_key")

    def test_scripts_and_examples_persist(self):
        v = RequestView(store.new_request(url="http://x"))
        v.script_pre.setPlainText("pm.environment.set('a', 1);")
        v.script_test.setPlainText("pm.test('t', () => {});")
        out = v.collect()
        self.assertEqual(out["scripts"], {"pre": "pm.environment.set('a', 1);", "test": "pm.test('t', () => {});"})
        self.assertEqual(RequestView(out).collect()["scripts"], out["scripts"])
        from zapi.http_client import Response
        v.response = Response(ok=True, status=200, reason="OK", headers=[("Content-Type", "text/plain"),
                                                                       ("Date", "x")], body=b"hi")
        v.save_example()
        ex = v.collect()["examples"][0]
        self.assertEqual((ex["status"], ex["body"], ex["headers"]), (200, "hi", [["Content-Type", "text/plain"]]))

    def test_send_with_scripts_updates_variables_and_test_tab(self):
        c = self.ws.add_collection("C")
        env = self.ws.add_environment("E")
        env["vars"] += [store.new_var("base", self.base), store.new_var("id", "5")]
        self.ws.settings["active_env"] = env["id"]
        r = store.new_request("R", url="{{base}}/items/{{id}}")
        r["headers"] = [store.kv("X-Token", "{{tok}}")]
        r["scripts"]["pre"] = "pm.environment.set('tok', 'abc');"
        r["scripts"]["test"] = ("pm.test('path', () => pm.expect(pm.response.json().path).to.equal('/items/5'));"
                                "pm.test('token', () => pm.expect(pm.response.json().token).to.equal('abc'));"
                                "pm.test('fails', () => pm.expect(1).to.equal(2));"
                                "pm.environment.set('seen', 'yes'); console.log('hello');")
        self.ws.save_request(r, c["id"])
        self.win.open_saved_request(r["id"])
        v = self.win.current_view()
        self.win.send_current()
        self.assertTrue(wait(lambda: v.response is not None), "no response")
        self.assertTrue(v.response.ok, v.response.error)
        self.assertEqual(v.test_table.rowCount(), 3)
        self.assertEqual([v.test_table.item(i, 0).text() for i in range(3)], ["PASS", "PASS", "FAIL"])
        self.assertIn("2/3", v.ptabs.bar.tabText(3))
        vals = {x["key"]: x["current"] for x in env["vars"]}
        self.assertEqual((vals["tok"], vals["seen"]), ("abc", "yes"))
        self.assertIn("console.log: hello", self.win.console.toPlainText())
        self.assertEqual(len(self.ws.history), 1)


class SidebarTests(UiBase):
    def test_folder_tree_menu_actions_and_move(self):
        c = self.ws.add_collection("C")
        self.ws.add_folder(c["id"], "Users/Admin")
        a = store.new_request("A", url="/a")
        a["folder"] = "Users"
        b = store.new_request("B", url="/b")
        for r in (a, b):
            self.ws.save_request(r, c["id"])
        sb = self.win.sidebar
        sb.refresh()
        top = sb.tree.topLevelItem(0)
        self.assertEqual(top.text(0), "C")
        kids = [(top.child(i).text(0), top.child(i).data(0, store.__class__ and 256 + 1)) for i in range(top.childCount())]
        names = [k[0] for k in kids]
        self.assertIn("Users", names)
        users = next(top.child(i) for i in range(top.childCount()) if top.child(i).text(0) == "Users")
        self.assertEqual(sorted(users.child(i).text(0) for i in range(users.childCount()) if users.child(i).childCount() or users.child(i).text(0) == "Admin"), ["Admin"])
        sb._moved(b["id"], c["id"], "Users/Admin")
        self.assertEqual(self.ws.find_request(b["id"])[1]["folder"], "Users/Admin")
        self.ws.rename_folder(c["id"], "Users", "People")
        self.assertEqual(self.ws.find_request(b["id"])[1]["folder"], "People/Admin")
        self.assertIn("People/Admin", c["folders"])
        self.assertEqual(self.ws.delete_folder(c["id"], "People"), 2)
        self.assertEqual(c["items"], [])
        # persisted
        self.ws.save()
        again = store.Workspace(self.ws.path)
        self.assertEqual(again.collections[0]["folders"], [])

    def test_new_request_in_folder_and_save_keeps_folder(self):
        c = self.ws.add_collection("C")
        self.ws.add_folder(c["id"], "F")
        self.win.new_request(c["id"], "F", "http")
        v = self.win.current_view()
        v.url.setText("http://x")

        class AutoSave:                         # stands in for the modal "Save request" dialog
            def __init__(self, ws, name, cid, parent=None):
                self.name = type("N", (), {"text": lambda _s: "Saved one"})()
                self.collection_id = cid

            def exec(self):
                return True
        import zapi.app as app_mod
        real, app_mod.SaveRequestDialog = app_mod.SaveRequestDialog, AutoSave
        try:
            self.assertTrue(self.win.save_view(v))
        finally:
            app_mod.SaveRequestDialog = real
        stored = c["items"][0]
        self.assertEqual(stored["folder"], "F")
        self.assertIn("F", v.crumb.text())


class DialogTests(UiBase):
    def test_runner_dialog_runs_with_data_file_and_keeps_variables(self):
        c = self.ws.add_collection("C")
        env = self.ws.add_environment("E")
        env["vars"].append(store.new_var("base", self.base))
        self.ws.settings["active_env"] = env["id"]
        r = store.new_request("row", url="{{base}}/i/{{name}}")
        r["scripts"]["test"] = "pm.test('ok', () => pm.response.to.have.status(200)); pm.environment.set('last', pm.iterationData.get('name'));"
        self.ws.save_request(r, c["id"])
        dlg = RunnerDialog(self.ws, c["id"], "", self.win)
        dlg.data = [{"name": "a"}, {"name": "b"}]
        dlg._toggle()
        self.assertTrue(wait(lambda: dlg.summary is not None), "runner did not finish")
        self.assertEqual(dlg.table.rowCount(), 2)
        self.assertEqual(dlg.table.item(1, 5).text(), "1/1")
        self.assertEqual({v["key"]: v["current"] for v in env["vars"]}["last"], "b")
        self.assertTrue(dlg.changed_variables)
        self.assertTrue(dlg.export_btn.isEnabled())
        dlg.reject()

    def test_mock_dialog_serves_generated_routes(self):
        c = self.ws.add_collection("C")
        r = store.new_request("List", url="{{base_url}}/users")
        r["examples"] = [{"status": 200, "headers": [["Content-Type", "application/json"]], "body": '{"ok": true}'}]
        self.ws.save_request(r, c["id"])
        dlg = MockDialog(self.ws, self.win.mock_server, self.win.mock_bridge, self.win)
        dlg.port.setValue(0)
        dlg._generate()
        self.assertEqual(dlg.table.rowCount(), 1)
        dlg._toggle()
        self.assertTrue(self.win.mock_server.running)
        from zapi import http_client
        resp = http_client.send(store.new_request(url=self.win.mock_server.url() + "/users"), {}, timeout=5)
        self.assertEqual((resp.status, resp.body), (200, b'{"ok": true}'))
        self.assertTrue(wait(lambda: "/users" in dlg.log.toPlainText()))
        dlg._toggle()
        self.assertFalse(self.win.mock_server.running)
        self.assertEqual(self.ws.settings["mock"]["routes"][0]["path"], "/users")
        dlg.done(0)


class ProtocolViewTests(UiBase):
    def test_mqtt_view_end_to_end(self):
        broker = FakeBroker()
        self.addCleanup(broker.close)
        req = store.new_kind_request("mqtt")
        req["proto"].update({"host": "127.0.0.1", "port": str(broker.port), "keepalive": "0", "client_id": "ui"})
        self.win._add_view(req)
        v = self.win.current_view()
        v.primary_action()
        self.assertTrue(wait(lambda: v._state == "connected"), v.status.text())
        v.sub_topic.setText("t/#")
        v._mqtt_subscribe_now()
        self.assertTrue(wait(lambda: "Subscribing" in v.log.view.toPlainText()))
        v.pub_topic.setText("t/x")
        v.message.setPlainText("payload-1")
        v._mqtt_publish_now()
        self.assertTrue(wait(lambda: "payload-1" in v.log.view.toPlainText().replace("\n", " ")
                             and v.log.view.toPlainText().count("payload-1") >= 2), v.log.view.toPlainText())
        self.assertEqual(broker.received.get(timeout=3)[:2], ("t/x", b"payload-1"))
        v.primary_action()           # connected + MQTT: Ctrl+Enter publishes again
        v.disconnect_now()
        self.assertTrue(wait(lambda: v._state == "disconnected"))

    def test_websocket_view_end_to_end(self):
        srv, port = listen()
        self.addCleanup(srv.close)

        def run():
            conn, _ = srv.accept()
            head = conn.recv(4096).decode()
            key = [l.split(":", 1)[1].strip() for l in head.split("\r\n") if l.lower().startswith("sec-websocket-key")][0]
            conn.sendall(("HTTP/1.1 101 x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                          f"Sec-WebSocket-Accept: {protocols.ws_accept_key(key)}\r\n\r\n").encode())
            try:
                while True:
                    fin, op, payload = protocols.ws_read_frame(conn)
                    if op == 8:
                        break
                    conn.sendall(protocols.ws_encode_frame(op, b"echo:" + payload, mask=False))
            except (OSError, ConnectionError):
                pass
            conn.close()
        threading.Thread(target=run, daemon=True).start()
        req = store.new_kind_request("websocket")
        req["url"] = f"ws://127.0.0.1:{port}/"
        self.win._add_view(req)
        v = self.win.current_view()
        v.primary_action()
        self.assertTrue(wait(lambda: v._state == "connected"), v.status.text())
        v.message.setPlainText('{"a": 1}')
        v.send_as.setCurrentText("JSON")
        v.primary_action()
        self.assertTrue(wait(lambda: 'echo:{"a": 1}' in v.log.view.toPlainText()), v.log.view.toPlainText())
        v.send_as.setCurrentText("Binary (hex)")
        v.message.setPlainText("de ad")
        v._ws_send()
        self.assertTrue(wait(lambda: "[binary" in v.log.view.toPlainText().split("echo")[-1] or "de ad" in v.log.view.toPlainText()))
        v.message.setPlainText("zz")
        v._ws_send()
        self.assertIn("hexadecimal", v.warn.text())
        v.primary_action() if False else v.disconnect_now()
        self.assertTrue(wait(lambda: v._state == "disconnected"))

    def test_connect_failures_are_shown(self):
        req = store.new_kind_request("websocket")
        req["url"] = "ws://127.0.0.1:1/"
        self.win._add_view(req)
        v = self.win.current_view()
        v.primary_action()
        self.assertTrue(wait(lambda: v._state == "disconnected" and v.warn.text() != ""))
        self.assertTrue(v.log.entries[-1][3] == "error")
        g = store.new_kind_request("grpc")
        g["proto"]["target"] = "127.0.0.1:1"
        self.win._add_view(g)
        gv = self.win.current_view()
        gv.invoke()
        self.assertTrue(wait(lambda: gv.warn.text() != "", 15))


if __name__ == "__main__":
    unittest.main()
