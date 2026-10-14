"""A local mock server: routes with path parameters, templated bodies, delays and a request log."""
import json
import re
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import variables


def new_route(method="GET", path="/", status=200, body="", headers=None, delay_ms=0, name=""):
    return {"name": name, "method": method, "path": path, "status": status, "delay_ms": delay_ms,
            "headers": headers if headers is not None else [{"key": "Content-Type", "value": "application/json",
                                                              "enabled": True}],
            "body": body, "enabled": True}


def compile_path(path):
    """'/users/:id' or '/users/{id}' -> regex with named groups; '*' matches anything."""
    names = []

    def repl(m):
        name = m.group(1) or m.group(2)
        names.append(name)
        return f"(?P<{re.sub(r'[^0-9a-zA-Z_]', '_', name)}>[^/]+)"
    pattern = re.sub(r":([A-Za-z_]\w*)|\{([A-Za-z_]\w*)\}", repl, re.escape(path).replace(r"\:", ":").replace(
        r"\{", "{").replace(r"\}", "}").replace(r"\*", ".*"))
    return re.compile("^" + pattern.rstrip("/") + "/?$"), names


class MockServer:
    def __init__(self, routes=None, cors=True, on_request=None):
        self.routes = routes or []
        self.cors = cors
        self.on_request = on_request
        self._server = None
        self._thread = None

    @property
    def running(self):
        return self._server is not None

    @property
    def port(self):
        return self._server.server_port if self._server else 0

    def url(self):
        return f"http://127.0.0.1:{self.port}" if self.running else ""

    def match(self, method, path):
        for r in self.routes:
            if not r.get("enabled", True) or r["method"].upper() not in (method.upper(), "ANY"):
                continue
            rx, _names = compile_path(r["path"] or "/")
            m = rx.match(path)
            if m:
                return r, m.groupdict()
        return None, {}

    def start(self, port=0, host="127.0.0.1"):
        if self.running:
            return
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _handle(self):
                started = time.perf_counter()
                parts = urllib.parse.urlsplit(self.path)
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n).decode("utf-8", "replace") if n else ""
                route, params = outer.match(self.command, urllib.parse.unquote(parts.path))
                if self.command == "OPTIONS" and outer.cors and route is None:
                    status, headers, payload = 204, [], b""
                elif route is None:
                    status, headers = 404, [("Content-Type", "application/json")]
                    payload = json.dumps({"error": "No mock route matches", "method": self.command,
                                          "path": parts.path}).encode()
                else:
                    values = {f"params.{k}": v for k, v in params.items()}
                    values.update({f"query.{k}": v for k, v in urllib.parse.parse_qsl(parts.query)})
                    values.update({f"headers.{k.lower()}": v for k, v in self.headers.items()})
                    values.update({"body": body, "method": self.command, "path": parts.path})
                    if route.get("delay_ms"):
                        time.sleep(min(route["delay_ms"], 30000) / 1000)
                    status = int(route.get("status") or 200)
                    headers = [(variables.substitute(h["key"], values), variables.substitute(h.get("value", ""), values))
                               for h in route.get("headers", []) if h.get("enabled", True) and h.get("key")]
                    payload = variables.substitute(route.get("body", ""), values).encode("utf-8")
                if outer.cors:
                    names = {k.lower() for k, _ in headers}
                    for k, v in (("Access-Control-Allow-Origin", "*"), ("Access-Control-Allow-Methods", "*"),
                                 ("Access-Control-Allow-Headers", "*")):
                        if k.lower() not in names:
                            headers.append((k, v))
                self.send_response(status)
                for k, v in headers:
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(payload)
                if outer.on_request:
                    outer.on_request({"method": self.command, "path": self.path, "status": status,
                                      "ms": int((time.perf_counter() - started) * 1000),
                                      "matched": route.get("name") or route["path"] if route else ""})

            do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = _handle

        self._server = ThreadingHTTPServer((host, port), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        server, self._server = self._server, None
        if server is not None:
            server.shutdown()
            server.server_close()


def routes_from_collection(collection):
    """One route per request. A saved example (status / headers / body) is used when the request has one."""
    routes = []
    for r in collection["items"]:
        if r.get("kind", "http") != "http":
            continue
        url = r["url"]
        m = re.match(r"^(?:[a-zA-Z][\w+.-]*://[^/]+|\{\{[^}]+\}\})?(/[^?#]*)?", url)
        path = (m.group(1) if m and m.group(1) else "/") or "/"
        path = re.sub(r"/\{\{([^}]+)\}\}", r"/:\1", path)
        ex = (r.get("examples") or [None])[0]
        if ex:
            routes.append(new_route(r["method"], path, ex.get("status", 200), ex.get("body", ""),
                                    [{"key": h[0], "value": h[1], "enabled": True} for h in ex.get("headers", [])]
                                    or None, name=r["name"]))
        else:
            routes.append(new_route(r["method"], path, 200, "{}", name=r["name"]))
    return routes
