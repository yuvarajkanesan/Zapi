import json
import time
import unittest

from zapi import http_client, mock, store


class MockTests(unittest.TestCase):
    def setUp(self):
        self.log = []
        self.srv = mock.MockServer(on_request=self.log.append)
        self.addCleanup(self.srv.stop)

    def get(self, method, path, **kw):
        r = store.new_request(method=method, url=self.srv.url() + path)
        r["body"].update(kw.pop("body", {}))
        return http_client.send(r, {}, timeout=5)

    def test_path_params_query_and_templating(self):
        self.srv.routes = [
            mock.new_route("GET", "/users/:id", 200, '{"id": "{{params.id}}", "page": "{{query.page}}"}'),
            mock.new_route("POST", "/echo", 201, "{{body}}"),
            mock.new_route("GET", "/orders/{oid}/items", 200, '{"order": "{{params.oid}}"}'),
            mock.new_route("ANY", "/any/*", 202, "any"),
        ]
        self.srv.start()
        r = self.get("GET", "/users/42?page=3")
        self.assertEqual((r.status, json.loads(r.body)), (200, {"id": "42", "page": "3"}))
        r = self.get("POST", "/echo", body={"mode": "raw", "raw_type": "Text", "raw": "hello"})
        self.assertEqual((r.status, r.body), (201, b"hello"))
        self.assertEqual(json.loads(self.get("GET", "/orders/9/items").body), {"order": "9"})
        self.assertEqual(self.get("DELETE", "/any/x/y").status, 202)
        self.assertEqual(r.header("Access-Control-Allow-Origin"), "*")
        self.assertEqual(self.log[0]["matched"], "/users/:id")

    def test_unmatched_disabled_and_options(self):
        off = mock.new_route("GET", "/off", 200, "x")
        off["enabled"] = False
        self.srv.routes = [off]
        self.srv.start()
        r = self.get("GET", "/off")
        self.assertEqual(r.status, 404)
        self.assertEqual(json.loads(r.body)["path"], "/off")
        self.assertEqual(self.get("OPTIONS", "/anything").status, 204)

    def test_delay_headers_and_live_route_changes(self):
        self.srv.routes = [mock.new_route("GET", "/slow", 200, "ok", delay_ms=300,
                                          headers=[{"key": "X-Mock", "value": "{{method}}", "enabled": True}])]
        self.srv.start()
        t = time.time()
        r = self.get("GET", "/slow")
        self.assertGreaterEqual(time.time() - t, 0.29)
        self.assertEqual(r.header("X-Mock"), "GET")
        self.srv.routes.append(mock.new_route("GET", "/later", 200, "added"))
        self.assertEqual(self.get("GET", "/later").body, b"added")

    def test_start_stop_and_ports(self):
        self.assertFalse(self.srv.running)
        self.srv.start()
        port = self.srv.port
        self.assertGreater(port, 0)
        self.srv.stop()
        self.assertFalse(self.srv.running)
        self.assertFalse(self.get_safe(port))
        self.srv.start(port)            # the same port is reusable straight away
        self.assertEqual(self.srv.port, port)

    def get_safe(self, port):
        r = store.new_request(url=f"http://127.0.0.1:{port}/")
        return http_client.send(r, {}, timeout=2).ok

    def test_routes_from_collection(self):
        c = store.new_collection("C")
        a = store.new_request("List", url="{{base_url}}/users")
        b = store.new_request("Get", "GET", "https://api.x.io/v1/users/{{id}}?x=1")
        b["examples"] = [{"status": 201, "headers": [["Content-Type", "text/plain"]], "body": "hi"}]
        ws = store.new_request("socket")
        ws["kind"] = "websocket"
        c["items"] = [a, b, ws]
        routes = mock.routes_from_collection(c)
        self.assertEqual([(r["method"], r["path"], r["status"]) for r in routes],
                         [("GET", "/users", 200), ("GET", "/v1/users/:id", 201)])
        self.assertEqual(routes[1]["body"], "hi")


if __name__ == "__main__":
    unittest.main()
