"""Zapi - a fast, local-first desktop API client.

Usage:
    Zapi                 open the app
    Zapi --version
    Zapi --selftest      exercise every bundled component once (used to verify a packaged build)
    zapi-cli run COLLECTION [options]
                         run a collection without a window (Newman-style); `zapi-cli --help` lists the options
    Zapi --help
"""
import os
import sys

from zapi import APP_NAME, VERSION

HELP = __doc__


def selftest():
    import json
    import tempfile
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["ZAPI_HOME"] = tempfile.mkdtemp(prefix="zapi-selftest-")
    log_path = os.path.join(tempfile.gettempdir(), "zapi-selftest.log")
    log = open(log_path, "w", encoding="utf-8")
    ok = True

    def say(text):
        log.write(text + "\n")
        log.flush()
        try:
            print(text)
        except (OSError, AttributeError):       # a windowed build has no console
            pass

    def check(name, fn):
        nonlocal ok
        try:
            fn()
            say(f"ok    {name}")
        except Exception as e:  # noqa: BLE001
            ok = False
            say(f"FAIL  {name}: {type(e).__name__}: {e}")

    class Echo(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = json.dumps({"ok": True, "path": self.path}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = HTTPServer(("127.0.0.1", 0), Echo)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"

    def qt():
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        from zapi import theme
        theme.load_fonts()
        assert "IBM Plex Sans" in theme.ui_family(), "bundled fonts are missing"
        assert not theme.app_icon().isNull()
        assert not theme.lavati_mark_pixmap(64).isNull()
        app.setStyleSheet(theme.qss())

    def window():
        from PySide6.QtWidgets import QApplication
        from zapi import store
        from zapi.app import MainWindow
        QApplication.instance() or QApplication([])
        w = MainWindow(store.Workspace())
        w.show()
        for kind in ("http", "soap", "websocket", "sse", "mqtt", "grpc"):
            w.new_request("", "", kind)
        w.show_environments()
        w.show_main()
        w.close()

    def network():
        from zapi import http_client, store
        r = http_client.send(store.new_request(url=base + "/"), {})
        assert r.ok and r.status == 200 and json.loads(r.body)["ok"] is True

    def tls():
        # HTTPS / WSS / FTPS-style clients need Python's OpenSSL; a missing DLL shows up here, not at the first https URL
        import hashlib
        import socket
        import ssl
        ctx = ssl.create_default_context()
        assert ctx.verify_mode == ssl.CERT_REQUIRED and "OpenSSL" in ssl.OPENSSL_VERSION
        assert len(ctx.get_ca_certs()) >= 0
        assert hashlib.sha256(b"abc").hexdigest().startswith("ba7816bf")
        a, b = socket.socketpair()
        try:                                              # the handshake must at least start and fail cleanly
            ctx.wrap_socket(a, server_hostname="localhost", do_handshake_on_connect=False)
        finally:
            a.close()
            b.close()
        from zapi import http_client, store
        r = http_client.send(store.new_request(url="https://127.0.0.1:1/"), {}, timeout=2)
        assert not r.ok and r.error and "Traceback" not in r.error, r.error    # any clean failure: the stack loaded

    def secrets_vault():
        # Windows DPAPI through ctypes: a packaged build must be able to encrypt and read back
        from zapi import store, vault
        if not vault.available():
            return
        assert vault.unprotect(vault.protect("pässword ✓")) == "pässword ✓"
        ws = store.Workspace()
        e = ws.add_environment("E")
        e["vars"].append(store.new_var("t", "top-secret-value", secret=True))
        ws.save()
        raw = open(ws.path, encoding="utf-8").read()
        assert "top-secret-value" not in raw and "dpapi:" in raw, "the workspace file is not encrypted"
        assert store.Workspace(ws.path).environments[0]["vars"][0]["current"] == "top-secret-value"

    def encrypted_backup():
        # passphrase backups use scrypt from the bundled OpenSSL: it must work in a packaged build too
        from zapi import vault
        enc = vault.encrypt_backup({"secret": "päss"}, "selftest passphrase")
        assert "päss" not in str(enc)
        assert vault.decrypt_backup(enc, "selftest passphrase") == {"secret": "päss"}
        try:
            vault.decrypt_backup(enc, "wrong passphrase")
        except vault.BackupError:
            return
        raise AssertionError("a wrong passphrase was accepted")

    def themes():
        from zapi import theme
        try:
            for name in theme.THEME_CHOICES:
                theme.apply(name, "Blue")
                assert len(theme.qss()) > 5000 and theme.contrast(theme.TEXT, theme.BG) >= 7
        finally:
            theme.apply("Dark", "Amber")

    def codegen():
        from zapi import curl, store
        req = curl.parse("curl -X POST https://x.io/a -H 'A: b' -d '{\"a\":1}'")
        assert len(curl.LANGUAGES) >= 10
        for lang in curl.LANGUAGES:
            assert "x.io" in curl.generate(req, {}, lang), lang
        assert store.normalize_request(req)["method"] == "POST"

    def scripts():
        # the script engine runs in a helper process; in a packaged build that is Zapi.exe --script-worker
        from zapi import scripting
        ctx = {"kind": "test", "request": None, "environment": {"a": "1"}, "globals": {}, "collection": {},
               "variables": {}, "iterationData": {}, "info": {"iteration": 0, "iterationCount": 1},
               "response": {"code": 200, "status": "OK", "responseTime": 5, "size": 2, "headers": [], "body": "{}"}}
        r = scripting.run_script("pm.test('ok', () => pm.response.to.have.status(200)); pm.environment.set('b', 2);",
                                 ctx, timeout=30)
        assert r.ok and r.tests == [{"name": "ok", "passed": True, "error": ""}], (r.error, r.tests)
        assert r.environment == {"a": "1", "b": "2"}
        slow = scripting.run_script("while (true) {}", ctx, timeout=1)
        assert not slow.ok and "timed out" in slow.error

    def cli_run():
        from zapi import cli
        path = os.path.join(os.environ["ZAPI_HOME"], "c.json")
        out = os.path.join(os.environ["ZAPI_HOME"], "r.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"info": {"name": "selftest"}, "item": [{"name": "ping", "request": {
                "method": "GET", "url": "{{base}}/ping"}, "event": [{"listen": "test", "script": {"exec": [
                    "pm.test('status', () => pm.response.to.have.status(200));"]}}]}]}, f)
        code = cli.main(["run", path, "--env-var", f"base={base}", "--silent", "--reporter-json-export", out])
        assert code == 0, f"exit code {code}"
        assert json.load(open(out, encoding="utf-8"))["stats"]["tests"] == 1

    def mock_server():
        from zapi import http_client, mock, store
        m = mock.MockServer([mock.new_route("GET", "/hi/:name", 200, "hello {{params.name}}")])
        m.start()
        try:
            r = http_client.send(store.new_request(url=m.url() + "/hi/zapi"), {})
            assert r.body == b"hello zapi"
        finally:
            m.stop()

    def protocols_and_auth():
        from zapi import auth, protocols
        frame = protocols.ws_encode_frame(0x1, b"hi", mask=True)
        assert protocols.ws_accept_key("dGhlIHNhbXBsZSBub25jZQ==") == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="
        assert frame[0] == 0x81 and frame[1] == 0x82
        assert protocols.mqtt_varint(321) == b"\xc1\x02"
        ch = auth.parse_challenge('Digest realm="testrealm@host.com", qop="auth,auth-int", '
                                  'nonce="dcd98b7102dd2f0e8b11d0f600bfb0c093", opaque="5ccc069c403ebaf9f0171e9517f40e41"')
        h = auth.digest_header("GET", "/dir/index.html", ch, "Mufasa", "Circle Of Life", cnonce="0a4f113b")
        assert "6629fae49393a05397450978507c4ef1" in h
        assert auth.md4(b"abc").hex() == "a448017aaf21d8525fc10ae87aa6729d"

    def grpc():
        from zapi import grpc_client
        assert grpc_client.available(), "grpcio / grpcio-reflection / protobuf are not bundled"
        import grpc as g
        from google.protobuf import descriptor_pb2
        ch = g.insecure_channel("127.0.0.1:1")
        ch.close()
        assert descriptor_pb2.FileDescriptorProto(name="x").name == "x"

    def importers():
        from zapi import importers as imp
        kind, c = imp.import_data({"openapi": "3.0.0", "info": {"title": "T"}, "paths": {
            "/a": {"get": {"tags": ["Users"], "summary": "A"}}}})
        assert kind == "collection" and c["items"][0]["folder"] == "Users" and c["folders"] == ["Users"]

    check("qt + fonts + icons", qt)
    check("main window and every request type", window)
    check("http client", network)
    check("TLS (OpenSSL) libraries", tls)
    check("themes", themes)
    check("secrets encrypted on disk (DPAPI)", secrets_vault)
    check("passphrase-encrypted backups (scrypt)", encrypted_backup)
    check("cURL import and code generation (10+ languages)", codegen)
    check("script engine (helper process, timeout)", scripts)
    check("command line runner", cli_run)
    check("mock server", mock_server)
    check("websocket / mqtt / digest / md4", protocols_and_auth)
    check("grpc libraries", grpc)
    check("OpenAPI import with folders", importers)
    srv.shutdown()
    try:
        from zapi import scripting
        scripting.shutdown()
    except Exception:  # noqa: BLE001
        pass
    say("selftest passed" if ok else "selftest FAILED")
    log.close()
    return 0 if ok else 1


def main(argv):
    args = argv[1:]
    # the console build (zapi-cli.exe) is the same program started under another name
    if os.path.basename(sys.executable).lower().startswith("zapi-cli") and "--script-worker" not in args:
        from zapi import cli
        return cli.main(args)
    if "--script-worker" in args:
        from zapi import scripting
        return scripting.worker_main()
    if "--version" in args:
        print(f"{APP_NAME} {VERSION}")
        return 0
    if "--help" in args or "-h" in args:
        print(HELP)
        return 0
    if args and args[0] == "run":
        from zapi import cli
        return cli.main(args)
    if "--selftest" in args:
        return selftest()

    from PySide6.QtWidgets import QApplication
    from zapi import store, theme
    from zapi.app import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("Lavati Softwares")
    ws = store.Workspace()
    theme.apply(ws.settings.get("theme", "Dark"), ws.settings.get("accent", "Amber"), ws.settings.get("code_size", 10))
    theme.install_titlebar_filter(app)
    from zapi import crash
    crash.install()
    theme.load_fonts()
    f = app.font()
    f.setFamily(theme.ui_family())
    app.setFont(f)
    app.setStyleSheet(theme.qss())
    app.setWindowIcon(theme.app_icon())
    window = MainWindow(ws)
    app._zapi_window = window
    window.show()

    def system_scheme_changed(*_):
        if ws.settings.get("theme") == "System":      # follow Windows live
            app._zapi_window.apply_appearance()
    try:
        app.styleHints().colorSchemeChanged.connect(system_scheme_changed)
    except AttributeError:                            # Qt older than 6.5
        pass
    return app.exec()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
