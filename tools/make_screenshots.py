"""Renders the screenshots used in the README and the release page into docs/screenshots/.

Uses a throw-away data folder and a local demo server, so nothing real appears in the images and no network is needed.
Run from the project folder on Windows (it needs the real windows Qt platform for correct fonts):

    python tools/make_screenshots.py
"""
import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["ZAPI_HOME"] = tempfile.mkdtemp(prefix="zapi-shots-")
os.environ["QT_QPA_PLATFORM"] = "windows"
OUT = os.path.join(ROOT, "docs", "screenshots")

PETS = [{"id": 1, "name": "Rex", "species": "dog", "age": 4, "vaccinated": True},
        {"id": 2, "name": "Mia", "species": "cat", "age": 2, "vaccinated": True},
        {"id": 3, "name": "Kiwi", "species": "parrot", "age": 7, "vaccinated": False}]


class Demo(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj=None):
        body = json.dumps(obj, indent=None).encode() if obj is not None else b""
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("X-Request-Id", "req_8f3a21")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/pets/"):
            return self._send(200, PETS[0])
        self._send(200, {"data": PETS, "total": len(PETS), "page": 1})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        self._send(201, {"id": 4, **body, "created_at": "2026-10-14T10:32:08Z"})

    def do_DELETE(self):
        self._send(204)


def main():
    from PySide6.QtWidgets import QApplication
    app = QApplication([])
    from zapi import store, theme
    from zapi.app import MainWindow
    from zapi.dialogs import CodeDialog, MockDialog, RunnerDialog

    os.makedirs(OUT, exist_ok=True)
    srv = HTTPServer(("127.0.0.1", 0), Demo)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"

    def settle(seconds=0.4, until=None, timeout=10.0):
        end = time.time() + (timeout if until else seconds)
        while time.time() < end:
            app.processEvents()
            if until and until():
                break
            time.sleep(0.01)
        app.processEvents()

    def build_workspace():
        ws = store.Workspace(os.path.join(tempfile.mkdtemp(prefix="zapi-shots-ws-"), "workspace.json"))   # fresh each time
        c = ws.add_collection("Pet Store API")
        ws.add_folder(c["id"], "Pets")
        ws.add_folder(c["id"], "Admin")
        def req(name, method, path, folder, **kw):
            r = store.new_request(name, method, "{{base_url}}" + path)
            r["folder"] = folder
            r.update(kw)
            ws.save_request(r, c["id"])
            return r
        listing = req("List pets", "GET", "/pets", "Pets", params=[store.kv("page", "1"), store.kv("limit", "20")])
        create = req("Create pet", "POST", "/pets", "Pets")
        create["headers"] = [store.kv("Accept", "application/json"), store.kv("X-Client", "zapi")]
        create["body"].update({"mode": "raw", "raw_type": "JSON", "raw": json.dumps(
            {"name": "Pixel", "species": "rabbit", "age": 1, "vaccinated": True}, indent=2)})
        create["scripts"]["test"] = (
            "pm.test('Status is 201 Created', () => pm.response.to.have.status(201));\n"
            "pm.test('Response has an id', () => pm.expect(pm.response.json().id).to.be.a('number'));\n"
            "pm.test('Replies quickly', () => pm.expect(pm.response.responseTime).to.be.below(500));\n"
            "pm.environment.set('pet_id', pm.response.json().id);")
        req("Get pet by id", "GET", "/pets/{{pet_id}}", "Pets")
        req("Delete pet", "DELETE", "/pets/{{pet_id}}", "Admin")
        ws.save_request(create, c["id"])
        env = ws.add_environment("Staging")
        env["vars"] = [store.new_var("base_url", base), store.new_var("token", "demo-token-123", secret=True),
                       store.new_var("api_version", "v1")]
        ws.add_environment("Production")
        ws.add_environment("Development")
        ws.settings["active_env"] = env["id"]
        return ws, c, create, listing, env

    def window(theme_name, accent, ws):
        theme.apply(theme_name, accent)
        app.setStyleSheet(theme.qss())
        f = app.font()
        f.setFamily(theme.ui_family())
        app.setFont(f)
        w = MainWindow(ws)
        w.resize(1360, 860)
        w.show()
        theme.set_titlebar(w)
        settle(0.3)
        return w

    def shot(widget, name):
        settle(0.3)
        widget.grab().save(os.path.join(OUT, name))
        print("wrote", name)

    theme.load_fonts()
    for scheme, accent, tag in (("Dark", "Amber", "dark"), ("Light", "Blue", "light"), ("Midnight", "Purple", "midnight")):
        ws, c, create, listing, env = build_workspace()
        w = window(scheme, accent, ws)
        w.open_saved_request(create["id"])
        v = w.current_view()
        v.rtabs.bar.setCurrentIndex(3)                       # Body
        w.send_current()
        settle(until=lambda: v.response is not None)
        shot(w, f"01-request-{tag}.png")
        if tag == "dark":
            v.ptabs.bar.setCurrentIndex(3)                   # Test Results
            shot(w, "02-tests-dark.png")
            v.ptabs.bar.setCurrentIndex(0)
            v.rtabs.bar.setCurrentIndex(5)                   # the Tests script editor
            shot(w, "03-scripts-dark.png")
            v.rtabs.bar.setCurrentIndex(1)
            v.auth_type.setCurrentIndex(v.auth_type.findData("oauth2"))
            shot(w, "04-auth-dark.png")
            d = CodeDialog(v.collect(), ws.variable_values(c["id"]), w)
            d.lang.setCurrentText("Python (requests)")
            d.show()
            shot(d, "05-code-generation.png")
            d.close()
            r = RunnerDialog(ws, c["id"], "", w)
            r.show()
            r._toggle()
            settle(until=lambda: r.summary is not None)
            shot(r, "06-runner.png")
            r.close()
            m = MockDialog(ws, w.mock_server, w.mock_bridge, w)
            m.port.setValue(0)
            m._generate()
            m.show()
            shot(m, "07-mock-server.png")
            m.close()
            sock = store.new_kind_request("websocket")
            sock["name"], sock["url"] = "Live prices", "wss://stream.example.com/prices"
            sv = w._add_view(sock)
            sv.log.add("info", "Connected. HTTP/1.1 101 Switching Protocols")
            sv.log.add("sent", '{"subscribe": ["BTC-USD", "ETH-USD"]}')
            sv.log.add("recv", '{"pair": "BTC-USD", "price": 67421.5, "ts": 1760437928}')
            sv.log.add("recv", '{"pair": "ETH-USD", "price": 3512.08, "ts": 1760437929}')
            sv.log.add("recv", '{"pair": "BTC-USD", "price": 67425.0, "ts": 1760437931}')
            sv.message.setPlainText('{"subscribe": ["BTC-USD"]}')
            shot(w, "08-websocket.png")
            w.show_environments()
            w.env_page.refresh_list()
            shot(w, "09-environments.png")
            w.show_main()
        w.close()
        app.sendPostedEvents(None, 52)                       # QEvent.DeferredDelete
    # the first-run screen
    ws = store.Workspace(os.path.join(tempfile.mkdtemp(), "empty.json"))
    w = window("Dark", "Amber", ws)
    shot(w, "00-welcome.png")
    w.close()
    print("done:", len(os.listdir(OUT)), "files in", OUT)


if __name__ == "__main__":
    main()
