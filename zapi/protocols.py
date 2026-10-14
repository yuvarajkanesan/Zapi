"""Streaming protocol clients without third-party packages: WebSocket (RFC 6455), SSE and MQTT 3.1.1."""
import base64
import hashlib
import http.client
import os
import socket
import ssl
import struct
import threading
import urllib.parse

_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _tls_context(verify):
    return ssl.create_default_context() if verify else ssl._create_unverified_context()


def _recv_exact(sock, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("The connection was closed.")
        buf += chunk
    return bytes(buf)


# ---- WebSocket ----------------------------------------------------------------------------------------------
def ws_accept_key(key):
    return base64.b64encode(hashlib.sha1((key + _WS_GUID).encode()).digest()).decode()


def ws_encode_frame(opcode, payload, mask=True, fin=True):
    head = bytearray([(0x80 if fin else 0) | opcode])
    n = len(payload)
    flag = 0x80 if mask else 0
    if n < 126:
        head.append(flag | n)
    elif n < 65536:
        head += bytes([flag | 126]) + struct.pack(">H", n)
    else:
        head += bytes([flag | 127]) + struct.pack(">Q", n)
    if mask:
        key = os.urandom(4)
        head += key
        payload = bytes(b ^ key[i % 4] for i, b in enumerate(payload))
    return bytes(head) + payload


def ws_read_frame(sock):
    """Returns (fin, opcode, payload)."""
    b1, b2 = _recv_exact(sock, 2)
    fin, opcode = bool(b1 & 0x80), b1 & 0x0F
    n = b2 & 0x7F
    if n == 126:
        n = struct.unpack(">H", _recv_exact(sock, 2))[0]
    elif n == 127:
        n = struct.unpack(">Q", _recv_exact(sock, 8))[0]
    key = _recv_exact(sock, 4) if b2 & 0x80 else None
    payload = _recv_exact(sock, n) if n else b""
    if key:
        payload = bytes(b ^ key[i % 4] for i, b in enumerate(payload))
    return fin, opcode, payload


class WebSocketClient:
    """on_message(kind, data): kind is "text" or "binary". on_event(kind, text): connected / closed / error / ping / pong."""

    def __init__(self, url, headers=None, verify=True, subprotocols=None, on_message=None, on_event=None):
        self.url, self.headers, self.verify = url, list(headers or []), verify
        self.subprotocols = subprotocols or []
        self.on_message = on_message or (lambda k, d: None)
        self.on_event = on_event or (lambda k, t: None)
        self.sock = None
        self._send_lock = threading.Lock()
        self.connected = False
        self.protocol = ""

    def connect(self, timeout=15):
        p = urllib.parse.urlsplit(self.url)
        if p.scheme not in ("ws", "wss") or not p.hostname:
            raise ValueError("A WebSocket URL starts with ws:// or wss://")
        port = p.port or (443 if p.scheme == "wss" else 80)
        raw = socket.create_connection((p.hostname, port), timeout=timeout)
        sock = _tls_context(self.verify).wrap_socket(raw, server_hostname=p.hostname) if p.scheme == "wss" else raw
        key = base64.b64encode(os.urandom(16)).decode()
        path = (p.path or "/") + ("?" + p.query if p.query else "")
        names = {k.lower() for k, _ in self.headers}
        lines = [f"GET {path} HTTP/1.1", f"Host: {p.hostname}" + (f":{p.port}" if p.port else ""),
                 "Upgrade: websocket", "Connection: Upgrade", f"Sec-WebSocket-Key: {key}", "Sec-WebSocket-Version: 13"]
        if self.subprotocols:
            lines.append("Sec-WebSocket-Protocol: " + ", ".join(self.subprotocols))
        lines += [f"{k}: {v}" for k, v in self.headers if k.lower() not in
                  ("host", "upgrade", "connection", "sec-websocket-key", "sec-websocket-version")]
        if "user-agent" not in names:
            lines.append("User-Agent: Zapi")
        sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = sock.recv(1)
            if not chunk:
                raise ConnectionError("The server closed the connection during the handshake.")
            head += chunk
            if len(head) > 65536:
                raise ConnectionError("The handshake response is too large.")
        text = head.decode("latin-1")
        status = text.split("\r\n", 1)[0]
        if " 101 " not in status + " ":
            sock.close()
            raise ConnectionError(f"The server did not upgrade the connection: {status}")
        hdrs = {}
        for ln in text.split("\r\n")[1:]:
            k, _, v = ln.partition(":")
            hdrs[k.strip().lower()] = v.strip()
        if hdrs.get("sec-websocket-accept") != ws_accept_key(key):
            sock.close()
            raise ConnectionError("The server's Sec-WebSocket-Accept value is wrong.")
        self.protocol = hdrs.get("sec-websocket-protocol", "")
        sock.settimeout(None)
        self.sock = sock
        self.connected = True
        self.on_event("connected", status)
        threading.Thread(target=self._read_loop, daemon=True).start()

    def _send(self, opcode, payload):
        if not self.connected:
            raise ConnectionError("Not connected.")
        with self._send_lock:
            self.sock.sendall(ws_encode_frame(opcode, payload, mask=True))

    def send_text(self, text):
        self._send(0x1, text.encode("utf-8"))

    def send_binary(self, data):
        self._send(0x2, data)

    def ping(self, data=b""):
        self._send(0x9, data)

    def close(self, code=1000):
        if self.connected:
            try:
                self._send(0x8, struct.pack(">H", code))
            except OSError:
                pass
        self._shutdown()

    def _shutdown(self):
        was, self.connected = self.connected, False
        sock, self.sock = self.sock, None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
        return was

    def _read_loop(self):
        parts, kind = bytearray(), 0
        try:
            while True:
                fin, op, payload = ws_read_frame(self.sock)
                if op in (0x1, 0x2):
                    kind, parts = op, bytearray(payload)
                elif op == 0x0:
                    parts += payload
                elif op == 0x9:
                    self.on_event("ping", payload.decode("utf-8", "replace"))
                    self._send(0xA, payload)
                    continue
                elif op == 0xA:
                    self.on_event("pong", payload.decode("utf-8", "replace"))
                    continue
                elif op == 0x8:
                    code = struct.unpack(">H", payload[:2])[0] if len(payload) >= 2 else 1005
                    reason = payload[2:].decode("utf-8", "replace")
                    try:
                        self._send(0x8, payload[:2])
                    except OSError:
                        pass
                    if self._shutdown():
                        self.on_event("closed", f"closed by the server ({code}{' ' + reason if reason else ''})")
                    return
                if fin and op in (0x0, 0x1, 0x2):
                    data = bytes(parts)
                    if kind == 0x1:
                        self.on_message("text", data.decode("utf-8", "replace"))
                    else:
                        self.on_message("binary", data)
        except (OSError, ConnectionError, ValueError, AttributeError) as e:
            if self._shutdown():
                self.on_event("error", str(e) or "The connection was lost.")
            else:
                self.on_event("closed", "disconnected")


# ---- Server-Sent Events ------------------------------------------------------------------------------------
class SseClient:
    """on_event(event_dict): {"event","data","id","retry"}; on_status(kind, text): connected / closed / error."""

    def __init__(self, url, method="GET", headers=None, body=None, verify=True, on_event=None, on_status=None):
        self.url, self.method, self.headers, self.body, self.verify = url, method, list(headers or []), body, verify
        self.on_event = on_event or (lambda e: None)
        self.on_status = on_status or (lambda k, t: None)
        self._conn = None
        self._stop = False

    def start(self, timeout=15):
        """Connects (raising on failure), then streams on a background thread."""
        p = urllib.parse.urlsplit(self.url)
        if p.scheme not in ("http", "https") or not p.hostname:
            raise ValueError("An SSE URL starts with http:// or https://")
        if p.scheme == "https":
            conn = http.client.HTTPSConnection(p.hostname, p.port, timeout=timeout, context=_tls_context(self.verify))
        else:
            conn = http.client.HTTPConnection(p.hostname, p.port, timeout=timeout)
        names = {k.lower() for k, _ in self.headers}
        hdrs = dict(self.headers)
        if "accept" not in names:
            hdrs["Accept"] = "text/event-stream"
        hdrs.setdefault("Cache-Control", "no-cache")
        hdrs.setdefault("Accept-Encoding", "identity")
        hdrs.setdefault("User-Agent", "Zapi")
        conn.request(self.method, (p.path or "/") + ("?" + p.query if p.query else ""), body=self.body, headers=hdrs)
        resp = conn.getresponse()
        if resp.status != 200:
            body = resp.read(500).decode("utf-8", "replace")
            conn.close()
            raise ConnectionError(f"The server answered {resp.status} {resp.reason}. {body}".strip())
        ctype = resp.getheader("Content-Type", "")
        if "text/event-stream" not in ctype:
            conn.close()
            raise ConnectionError(f"Not an event stream (Content-Type: {ctype or 'none'}).")
        if conn.sock is not None:
            conn.sock.settimeout(None)
        self._conn = conn
        self.on_status("connected", f"{resp.status} {resp.reason}")
        threading.Thread(target=self._loop, args=(resp,), daemon=True).start()

    def stop(self):
        """Disconnect. Reported straight away: a thread blocked in recv() is not guaranteed to wake on Windows."""
        if self._stop:
            return
        self._stop = True
        conn, self._conn = self._conn, None
        if conn is not None and conn.sock is not None:
            try:
                conn.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            conn.close()
            self.on_status("closed", "Disconnected.")

    def _loop(self, resp):
        ev = {"event": "message", "data": [], "id": "", "retry": ""}
        try:
            while not self._stop:
                raw = resp.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if line == "":
                    if ev["data"] or ev["event"] != "message":
                        self.on_event({"event": ev["event"], "data": "\n".join(ev["data"]), "id": ev["id"],
                                       "retry": ev["retry"]})
                    ev = {"event": "message", "data": [], "id": ev["id"], "retry": ""}
                elif line.startswith(":"):
                    continue
                else:
                    field, _, value = line.partition(":")
                    value = value[1:] if value.startswith(" ") else value
                    if field == "data":
                        ev["data"].append(value)
                    elif field in ("event", "id", "retry"):
                        ev[field] = value or ("message" if field == "event" else "")
        except (OSError, ValueError, AttributeError, http.client.HTTPException):
            pass
        if not self._stop:
            self._stop = True
            self._conn = None
            self.on_status("closed", "The server ended the stream.")


# ---- MQTT 3.1.1 --------------------------------------------------------------------------------------------
CONNACK_ERRORS = {1: "unacceptable protocol version", 2: "client id rejected", 3: "server unavailable",
                  4: "bad user name or password", 5: "not authorized"}


def mqtt_str(s):
    b = s.encode("utf-8")
    return struct.pack(">H", len(b)) + b


def mqtt_varint(n):
    out = bytearray()
    while True:
        digit, n = n % 128, n // 128
        out.append(digit | (0x80 if n else 0))
        if not n:
            return bytes(out)


def mqtt_packet(ptype, flags, body=b""):
    return bytes([(ptype << 4) | flags]) + mqtt_varint(len(body)) + body


def mqtt_read_packet(sock):
    """Returns (type, flags, body)."""
    b = _recv_exact(sock, 1)[0]
    n, mult = 0, 1
    for _ in range(4):
        d = _recv_exact(sock, 1)[0]
        n += (d & 0x7F) * mult
        mult *= 128
        if not d & 0x80:
            break
    return b >> 4, b & 0x0F, _recv_exact(sock, n) if n else b""


def mqtt_parse_publish(flags, body):
    """-> (topic, payload, qos, retain, packet_id)"""
    qos, retain = (flags >> 1) & 3, bool(flags & 1)
    tl = struct.unpack(">H", body[:2])[0]
    topic = body[2:2 + tl].decode("utf-8", "replace")
    pos = 2 + tl
    pid = 0
    if qos:
        pid = struct.unpack(">H", body[pos:pos + 2])[0]
        pos += 2
    return topic, body[pos:], qos, retain, pid


def topic_matches(filter_, topic):
    f, t = filter_.split("/"), topic.split("/")
    for i, part in enumerate(f):
        if part == "#":
            return True
        if i >= len(t) or (part != "+" and part != t[i]):
            return False
    return len(f) == len(t)


class MqttClient:
    """on_message(topic, payload: bytes, qos, retain); on_event(kind, text): connected / subscribed / published / closed / error."""

    def __init__(self, host, port=1883, client_id="", username="", password="", keepalive=30, tls=False,
                 verify=True, clean=True, on_message=None, on_event=None):
        self.host, self.port, self.tls, self.verify = host, port, tls, verify
        self.client_id = client_id or "zapi-" + os.urandom(4).hex()
        self.username, self.password, self.keepalive, self.clean = username, password, keepalive, clean
        self.on_message = on_message or (lambda *a: None)
        self.on_event = on_event or (lambda k, t: None)
        self.sock = None
        self.connected = False
        self._pid = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()

    def _next_pid(self):
        self._pid = self._pid % 65535 + 1
        return self._pid

    def _write(self, data):
        with self._lock:
            self.sock.sendall(data)

    def connect(self, timeout=15):
        raw = socket.create_connection((self.host, self.port), timeout=timeout)
        sock = _tls_context(self.verify).wrap_socket(raw, server_hostname=self.host) if self.tls else raw
        flags = 0x02 if self.clean else 0
        body = mqtt_str("MQTT") + bytes([4]) + b"\x00" + struct.pack(">H", self.keepalive) + mqtt_str(self.client_id)
        if self.username:
            flags |= 0x80
        if self.password:
            flags |= 0x40
        body = mqtt_str("MQTT") + bytes([4, flags]) + struct.pack(">H", self.keepalive) + mqtt_str(self.client_id)
        if self.username:
            body += mqtt_str(self.username)
        if self.password:
            body += mqtt_str(self.password)
        sock.sendall(mqtt_packet(1, 0, body))
        ptype, _f, resp = mqtt_read_packet(sock)
        if ptype != 2 or len(resp) < 2:
            sock.close()
            raise ConnectionError("The server did not answer with CONNACK (is this an MQTT broker?)")
        if resp[1] != 0:
            sock.close()
            raise ConnectionError("The broker refused the connection: " + CONNACK_ERRORS.get(resp[1], f"code {resp[1]}"))
        sock.settimeout(None)
        self.sock = sock
        self.connected = True
        self._stop.clear()
        self.on_event("connected", f"connected as {self.client_id}")
        threading.Thread(target=self._read_loop, daemon=True).start()
        if self.keepalive:
            threading.Thread(target=self._ping_loop, daemon=True).start()

    def subscribe(self, topic, qos=0):
        pid = self._next_pid()
        self._write(mqtt_packet(8, 2, struct.pack(">H", pid) + mqtt_str(topic) + bytes([qos])))

    def unsubscribe(self, topic):
        pid = self._next_pid()
        self._write(mqtt_packet(10, 2, struct.pack(">H", pid) + mqtt_str(topic)))

    def publish(self, topic, payload, qos=0, retain=False):
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        flags = (qos << 1) | (1 if retain else 0)
        body = mqtt_str(topic)
        pid = 0
        if qos:
            pid = self._next_pid()
            body += struct.pack(">H", pid)
        self._write(mqtt_packet(3, flags, body + payload))
        if qos == 0:
            self.on_event("published", topic)

    def disconnect(self):
        if self.connected:
            try:
                self._write(mqtt_packet(14, 0))
            except OSError:
                pass
        self._close()

    def _close(self):
        self._stop.set()
        was, self.connected = self.connected, False
        sock, self.sock = self.sock, None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
        return was

    def _ping_loop(self):
        while not self._stop.wait(max(1, self.keepalive / 2)):
            try:
                self._write(mqtt_packet(12, 0))
            except (OSError, AttributeError):
                return

    def _read_loop(self):
        try:
            while True:
                ptype, flags, body = mqtt_read_packet(self.sock)
                if ptype == 3:
                    topic, payload, qos, retain, pid = mqtt_parse_publish(flags, body)
                    if qos == 1:
                        self._write(mqtt_packet(4, 0, struct.pack(">H", pid)))
                    elif qos == 2:
                        self._write(mqtt_packet(5, 0, struct.pack(">H", pid)))
                    self.on_message(topic, payload, qos, retain)
                elif ptype == 4:
                    self.on_event("published", f"acknowledged (id {struct.unpack('>H', body[:2])[0]})")
                elif ptype == 5:                       # PUBREC -> PUBREL
                    self._write(mqtt_packet(6, 2, body[:2]))
                elif ptype == 6:                       # PUBREL -> PUBCOMP
                    self._write(mqtt_packet(7, 0, body[:2]))
                elif ptype == 7:
                    self.on_event("published", "completed (QoS 2)")
                elif ptype == 9:
                    codes = list(body[2:])
                    self.on_event("subscribed", "refused by the broker" if 0x80 in codes
                                  else f"subscribed (granted QoS {codes[0] if codes else 0})")
                elif ptype == 11:
                    self.on_event("unsubscribed", "unsubscribed")
        except (OSError, ConnectionError, AttributeError, struct.error) as e:
            if self._close():
                self.on_event("error", str(e) or "The connection was lost.")
            else:
                self.on_event("closed", "disconnected")
