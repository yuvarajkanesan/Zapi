import queue
import socket
import struct
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from zapi import protocols as pr


def listen():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(5)
    return s, s.getsockname()[1]


class WebSocketTests(unittest.TestCase):
    def echo_server(self, behaviour="echo"):
        srv, port = listen()
        self.addCleanup(srv.close)

        def run():
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            data = b""
            while b"\r\n\r\n" not in data:
                data += conn.recv(1024)
            head = data.decode()
            key = [l.split(":", 1)[1].strip() for l in head.split("\r\n") if l.lower().startswith("sec-websocket-key")][0]
            self.request_head = head
            accept = pr.ws_accept_key(key if behaviour != "badkey" else "x")
            conn.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                          f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode())
            try:
                while True:
                    fin, op, payload = pr.ws_read_frame(conn)
                    if op == 0x8:
                        conn.sendall(pr.ws_encode_frame(0x8, payload, mask=False))
                        break
                    if op == 0x1 and payload == b"fragment":
                        conn.sendall(pr.ws_encode_frame(0x1, b"frag", mask=False, fin=False))
                        conn.sendall(pr.ws_encode_frame(0x0, b"ment!", mask=False, fin=True))
                    elif op == 0x1 and payload == b"ping-me":
                        conn.sendall(pr.ws_encode_frame(0x9, b"hi", mask=False))
                    elif op in (0x1, 0x2):
                        conn.sendall(pr.ws_encode_frame(op, payload, mask=False))
                    elif op == 0x9:
                        conn.sendall(pr.ws_encode_frame(0xA, payload, mask=False))
            except (OSError, ConnectionError):
                pass
            finally:
                conn.close()
        threading.Thread(target=run, daemon=True).start()
        return port

    def client(self, port, **kw):
        self.msgs, self.events = queue.Queue(), queue.Queue()
        c = pr.WebSocketClient(f"ws://127.0.0.1:{port}/chat?room=1", on_message=lambda k, d: self.msgs.put((k, d)),
                               on_event=lambda k, t: self.events.put((k, t)), **kw)
        self.addCleanup(c.close)
        return c

    def test_echo_text_binary_large_and_fragments(self):
        c = self.client(self.echo_server(), headers=[("X-Test", "yes")], subprotocols=["chat"])
        c.connect(5)
        self.assertEqual(self.events.get(timeout=3)[0], "connected")
        self.assertIn("GET /chat?room=1 HTTP/1.1", self.request_head)
        self.assertIn("X-Test: yes", self.request_head)
        c.send_text("héllo")
        self.assertEqual(self.msgs.get(timeout=3), ("text", "héllo"))
        c.send_binary(b"\x00\x01\xff")
        self.assertEqual(self.msgs.get(timeout=3), ("binary", b"\x00\x01\xff"))
        big = "x" * 70000                      # needs the 8-byte length form
        c.send_text(big)
        self.assertEqual(self.msgs.get(timeout=5), ("text", big))
        c.send_text("fragment")
        self.assertEqual(self.msgs.get(timeout=3), ("text", "fragment!"))

    def test_ping_pong_and_close(self):
        c = self.client(self.echo_server())
        c.connect(5)
        self.events.get(timeout=3)
        c.send_text("ping-me")                 # the server pings us; we must pong automatically
        self.assertEqual(self.events.get(timeout=3), ("ping", "hi"))
        c.ping(b"p")
        self.assertEqual(self.events.get(timeout=3), ("pong", "p"))
        c.close()
        self.assertFalse(c.connected)
        with self.assertRaises(ConnectionError):
            c.send_text("after close")

    def test_handshake_errors(self):
        c = self.client(self.echo_server("badkey"))
        with self.assertRaises(ConnectionError) as cm:
            c.connect(5)
        self.assertIn("Accept", str(cm.exception))
        with self.assertRaises(ValueError):
            pr.WebSocketClient("http://x").connect()
        srv, port = listen()
        self.addCleanup(srv.close)

        def reply():
            conn, _ = srv.accept()
            conn.recv(2048)
            conn.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
            conn.close()
        threading.Thread(target=reply, daemon=True).start()
        with self.assertRaises(ConnectionError) as cm:
            self.client(port).connect(5)
        self.assertIn("403", str(cm.exception))

    def test_server_closing_is_reported(self):
        srv, port = listen()
        self.addCleanup(srv.close)

        def run():
            conn, _ = srv.accept()
            head = conn.recv(4096).decode()
            key = [l.split(":", 1)[1].strip() for l in head.split("\r\n") if l.lower().startswith("sec-websocket-key")][0]
            conn.sendall(("HTTP/1.1 101 x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                          f"Sec-WebSocket-Accept: {pr.ws_accept_key(key)}\r\n\r\n").encode())
            conn.sendall(pr.ws_encode_frame(0x8, struct.pack(">H", 1001) + b"going away", mask=False))
            conn.close()
        threading.Thread(target=run, daemon=True).start()
        c = self.client(port)
        c.connect(5)
        self.assertEqual(self.events.get(timeout=3)[0], "connected")
        kind, text = self.events.get(timeout=3)
        self.assertEqual(kind, "closed")
        self.assertIn("1001", text)


class SseHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/json":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")
            return
        if self.path == "/denied":
            self.send_response(401)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        body = (": comment\n\ndata: one\n\nevent: update\nid: 7\ndata: line1\ndata: line2\n\n"
                "retry: 500\ndata: last\n\n").encode()
        self.wfile.write(f"{len(body):x}\r\n".encode() + body + b"\r\n")
        self.wfile.flush()
        self.server.hold.wait(5)             # keep the stream open until the test is done
        self.wfile.write(b"0\r\n\r\n")


class QuietServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):      # clients abort on purpose
        pass


class SseTests(unittest.TestCase):
    def setUp(self):
        self.srv = QuietServer(("127.0.0.1", 0), SseHandler)
        self.srv.hold = threading.Event()
        self.srv.daemon_threads = True
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_port}"
        self.events, self.status = queue.Queue(), queue.Queue()

    def tearDown(self):
        self.srv.hold.set()
        self.srv.shutdown()
        self.srv.server_close()

    def client(self, path):
        c = pr.SseClient(self.base + path, on_event=self.events.put, on_status=lambda k, t: self.status.put((k, t)))
        self.addCleanup(c.stop)
        return c

    def test_events_are_parsed(self):
        c = self.client("/stream")
        c.start(5)
        self.assertEqual(self.status.get(timeout=3)[0], "connected")
        e1, e2, e3 = (self.events.get(timeout=3) for _ in range(3))
        self.assertEqual((e1["event"], e1["data"]), ("message", "one"))
        self.assertEqual((e2["event"], e2["data"], e2["id"]), ("update", "line1\nline2", "7"))
        self.assertEqual((e3["data"], e3["retry"], e3["id"]), ("last", "500", "7"))
        c.stop()
        self.assertEqual(self.status.get(timeout=3), ("closed", "Disconnected."))

    def test_server_end_and_errors(self):
        c = self.client("/stream")
        c.start(5)
        self.status.get(timeout=3)
        for _ in range(3):
            self.events.get(timeout=3)
        self.srv.hold.set()
        self.assertEqual(self.status.get(timeout=5), ("closed", "The server ended the stream."))
        with self.assertRaises(ConnectionError) as cm:
            self.client("/json").start(5)
        self.assertIn("Not an event stream", str(cm.exception))
        with self.assertRaises(ConnectionError) as cm:
            self.client("/denied").start(5)
        self.assertIn("401", str(cm.exception))
        with self.assertRaises(ValueError):
            pr.SseClient("ws://x").start()


class FakeBroker:
    def __init__(self, user=None):
        self.srv, self.port = listen()
        self.user = user
        self.received = queue.Queue()
        self.subs = []
        threading.Thread(target=self.run, daemon=True).start()

    def close(self):
        self.srv.close()

    def run(self):
        try:
            conn, _ = self.srv.accept()
        except OSError:
            return
        try:
            t, f, body = pr.mqtt_read_packet(conn)
            assert t == 1 and body[2:6] == b"MQTT"
            flags = body[7]
            self.connect_flags = flags
            pos = 10
            cid_len = struct.unpack(">H", body[pos:pos + 2])[0]
            self.client_id = body[pos + 2:pos + 2 + cid_len].decode()
            pos += 2 + cid_len
            ok = True
            if self.user:
                ul = struct.unpack(">H", body[pos:pos + 2])[0]
                user = body[pos + 2:pos + 2 + ul].decode()
                ok = flags & 0x80 and user == self.user
            conn.sendall(pr.mqtt_packet(2, 0, bytes([0, 0 if ok else 4])))
            if not ok:
                conn.close()
                return
            while True:
                t, f, body = pr.mqtt_read_packet(conn)
                if t == 8:
                    pid = body[:2]
                    tl = struct.unpack(">H", body[2:4])[0]
                    self.subs.append(body[4:4 + tl].decode())
                    conn.sendall(pr.mqtt_packet(9, 0, pid + bytes([body[4 + tl]])))
                elif t == 3:
                    topic, payload, qos, retain, pid = pr.mqtt_parse_publish(f, body)
                    self.received.put((topic, payload, qos, retain))
                    if qos == 1:
                        conn.sendall(pr.mqtt_packet(4, 0, struct.pack(">H", pid)))
                    elif qos == 2:
                        conn.sendall(pr.mqtt_packet(5, 0, struct.pack(">H", pid)))
                    if any(pr.topic_matches(s, topic) for s in self.subs):      # echo to ourselves
                        conn.sendall(pr.mqtt_packet(3, 0, pr.mqtt_str(topic) + payload))
                elif t == 6:
                    conn.sendall(pr.mqtt_packet(7, 0, body[:2]))
                elif t == 12:
                    conn.sendall(pr.mqtt_packet(13, 0))
                elif t == 14:
                    break
        except (OSError, ConnectionError, AssertionError):
            pass
        finally:
            conn.close()


class MqttTests(unittest.TestCase):
    def setUp(self):
        self.msgs, self.events = queue.Queue(), queue.Queue()

    def client(self, broker, **kw):
        c = pr.MqttClient("127.0.0.1", broker.port, on_message=lambda *a: self.msgs.put(a),
                          on_event=lambda k, t: self.events.put((k, t)), **kw)
        self.addCleanup(c.disconnect)
        self.addCleanup(broker.close)
        return c

    def wait_event(self, kind, contains=""):
        while True:
            k, t = self.events.get(timeout=3)
            if k == kind and contains in t:
                return t

    def test_subscribe_publish_all_qos(self):
        b = FakeBroker()
        c = self.client(b, client_id="tester", username="", keepalive=0)
        c.connect(5)
        self.assertIn("tester", self.wait_event("connected"))
        c.subscribe("sensors/+/temp", 1)
        self.assertIn("granted QoS 1", self.wait_event("subscribed"))
        c.publish("sensors/a/temp", "21.5", qos=0)
        self.assertEqual(self.msgs.get(timeout=3)[:2], ("sensors/a/temp", b"21.5"))
        c.publish("sensors/b/temp", b"\x00\x01", qos=1, retain=True)
        self.assertEqual(self.msgs.get(timeout=3)[:2], ("sensors/b/temp", b"\x00\x01"))
        self.assertIn("acknowledged", self.wait_event("published", "acknowledged"))
        c.publish("other/topic", "x", qos=2)
        self.assertIn("completed", self.wait_event("published", "completed"))
        self.assertEqual(b.received.get(timeout=3), ("sensors/a/temp", b"21.5", 0, False))
        self.assertEqual(b.received.get(timeout=3)[2:], (1, True))
        self.assertEqual(b.client_id, "tester")

    def test_auth_failure_and_credentials(self):
        b = FakeBroker(user="me")
        c = self.client(b, username="wrong", password="x")
        with self.assertRaises(ConnectionError) as cm:
            c.connect(5)
        self.assertIn("bad user name or password", str(cm.exception))
        b2 = FakeBroker(user="me")
        c2 = self.client(b2, username="me", password="pw")
        c2.connect(5)
        self.assertEqual(b2.connect_flags & 0xC2, 0xC2)

    def test_not_a_broker_and_disconnect_event(self):
        srv, port = listen()
        self.addCleanup(srv.close)
        threading.Thread(target=lambda: (lambda c: (c.recv(100), c.sendall(b"\x20\x00"), c.close()))(srv.accept()[0]),
                         daemon=True).start()
        c = pr.MqttClient("127.0.0.1", port)
        with self.assertRaises(ConnectionError):
            c.connect(5)
        b = FakeBroker()
        c = self.client(b)
        c.connect(5)
        self.wait_event("connected")
        c.disconnect()
        self.assertFalse(c.connected)

    def test_topic_matching_and_packets(self):
        self.assertTrue(pr.topic_matches("a/+/c", "a/b/c"))
        self.assertTrue(pr.topic_matches("a/#", "a/b/c/d"))
        self.assertFalse(pr.topic_matches("a/+", "a/b/c"))
        self.assertFalse(pr.topic_matches("a/b", "a"))
        for n in (0, 127, 128, 16383, 16384, 2097151):
            enc = pr.mqtt_varint(n)
            val, mult = 0, 1
            for byte in enc:
                val += (byte & 127) * mult
                mult *= 128
            self.assertEqual(val, n)


if __name__ == "__main__":
    unittest.main()
