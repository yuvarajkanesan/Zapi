"""Sends a request dict with the standard library and returns a Response; no Qt in here."""
import base64
import gzip
import http.cookiejar
import json
import mimetypes
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zlib
from dataclasses import dataclass, field

from . import auth as auth_mod
from . import variables

USER_AGENT = "Zapi/0.1"
MAX_BODY = 50 * 1024 * 1024
RAW_CONTENT_TYPES = {"JSON": "application/json", "Text": "text/plain", "XML": "application/xml",
                     "HTML": "text/html", "JavaScript": "application/javascript",
                     "SOAP 1.1": "text/xml; charset=utf-8", "SOAP 1.2": "application/soap+xml; charset=utf-8"}


@dataclass
class Response:
    ok: bool = False
    status: int = 0
    reason: str = ""
    headers: list = field(default_factory=list)       # [(name, value)]
    body: bytes = b""
    elapsed_ms: int = 0
    size: int = 0
    url: str = ""
    error: str = ""
    cookies: list = field(default_factory=list)       # [{"name","value","domain","path"}]
    sent: dict = field(default_factory=dict)          # resolved request, for the console

    def header(self, name, default=""):
        n = name.lower()
        return next((v for k, v in self.headers if k.lower() == n), default)

    @property
    def content_type(self):
        return self.header("Content-Type").split(";")[0].strip().lower()

    @property
    def charset(self):
        for part in self.header("Content-Type").split(";")[1:]:
            k, _, v = part.strip().partition("=")
            if k.lower() == "charset" and v:
                return v.strip("\"'")
        return "utf-8"

    def status_line(self):
        return f"HTTP {self.status} {self.reason}".strip()

    def headers_text(self):
        return "\n".join(f"{k}: {v}" for k, v in self.headers)

    def raw_text(self):
        """The whole response as it would look on the wire: status line, headers, blank line, body."""
        return f"{self.status_line()}\n{self.headers_text()}\n\n{self.text()}"

    def text(self):
        try:
            return self.body.decode(self.charset, errors="replace")
        except LookupError:
            return self.body.decode("utf-8", errors="replace")


def _enabled(rows):
    return [r for r in rows if r.get("enabled", True) and (r.get("key") or "") != ""]


def build_url(req, values):
    """The request URL with {{vars}} resolved, enabled params appended, and auth-in-query applied."""
    url = variables.substitute(req.get("url", "").strip(), values)
    pairs = [(variables.substitute(r["key"], values), variables.substitute(r.get("value", ""), values))
             for r in _enabled(req.get("params", []))]
    auth = req.get("auth") or {}
    if auth.get("type") == "apikey" and auth.get("in") == "query" and auth.get("key"):
        pairs.append((variables.substitute(auth["key"], values), variables.substitute(auth.get("value", ""), values)))
    if url and pairs:
        frag = ""
        if "#" in url:
            url, _, frag = url.partition("#")
            frag = "#" + frag
        sep = "&" if "?" in url else "?"
        if url.endswith(("?", "&")):
            sep = ""
        url = url + sep + urllib.parse.urlencode(pairs, quote_via=urllib.parse.quote) + frag
    if url and "://" not in url:
        url = "http://" + url
    return url


def build_headers(req, values):
    headers = [(variables.substitute(r["key"], values), variables.substitute(r.get("value", ""), values))
               for r in _enabled(req.get("headers", []))]
    names = {k.lower() for k, _ in headers}
    auth = req.get("auth") or {}
    atype = auth.get("type", "none")
    if "authorization" not in names:
        if atype == "bearer" and auth.get("token"):
            headers.append(("Authorization", "Bearer " + variables.substitute(auth["token"], values)))
        elif atype == "oauth2" and auth.get("token"):
            headers.append(("Authorization", (auth.get("o2_prefix") or "Bearer") + " " +
                            variables.substitute(auth["token"], values)))
        elif atype == "basic" and (auth.get("username") or auth.get("password")):
            raw = variables.substitute(auth.get("username", ""), values) + ":" + \
                variables.substitute(auth.get("password", ""), values)
            headers.append(("Authorization", "Basic " + base64.b64encode(raw.encode("utf-8")).decode("ascii")))
    if atype == "apikey" and auth.get("in", "header") == "header" and auth.get("key"):
        k = variables.substitute(auth["key"], values)
        if k.lower() not in names:
            headers.append((k, variables.substitute(auth.get("value", ""), values)))
    return headers


def build_body(req, values):
    """Returns (bytes | None, content_type | None)."""
    body = req.get("body") or {}
    mode = body.get("mode", "none")
    if mode == "none":
        return None, None
    if mode == "raw":
        data = variables.substitute(body.get("raw", ""), values).encode("utf-8")
        return data, RAW_CONTENT_TYPES.get(body.get("raw_type", "Text"), "text/plain")
    if mode == "x-www-form-urlencoded":
        pairs = [(variables.substitute(r["key"], values), variables.substitute(r.get("value", ""), values))
                 for r in _enabled(body.get("urlencoded", []))]
        return urllib.parse.urlencode(pairs).encode("ascii"), "application/x-www-form-urlencoded"
    if mode == "GraphQL":
        payload = {"query": variables.substitute(body.get("query", ""), values)}
        raw_vars = variables.substitute(body.get("variables", ""), values).strip()
        if raw_vars:
            try:
                payload["variables"] = json.loads(raw_vars)
            except ValueError as e:
                raise ValueError(f"GraphQL variables are not valid JSON: {e}")
        return json.dumps(payload).encode("utf-8"), "application/json"
    if mode == "binary":
        path = variables.substitute(body.get("file", ""), values)
        if not path:
            return None, None
        with open(path, "rb") as f:
            return f.read(), mimetypes.guess_type(path)[0] or "application/octet-stream"
    if mode == "form-data":
        boundary = "----ZapiBoundary" + uuid.uuid4().hex
        out = bytearray()
        for r in _enabled(body.get("form", [])):
            name = variables.substitute(r["key"], values).replace('"', "%22")
            val = variables.substitute(r.get("value", ""), values)
            out += f"--{boundary}\r\n".encode()
            if r.get("type") == "file":
                if not val.strip():
                    raise ValueError(f"Form field “{r['key']}” is a file but no file is chosen: pick one in the "
                                     "Value column (or untick the row).")
                fname = (r.get("filename") or os.path.basename(val)).replace('"', "%22")
                ctype = r.get("content_type") or mimetypes.guess_type(fname)[0] or "application/octet-stream"
                try:
                    with open(val, "rb") as f:
                        content = f.read()
                except OSError as e:
                    raise ValueError(f"Cannot read the file for form field “{r['key']}”: {e.strerror or e}") from None
                out += (f'Content-Disposition: form-data; name="{name}"; filename="{fname}"\r\n'
                        f"Content-Type: {ctype}\r\n\r\n").encode() + content
            else:
                part_type = f"Content-Type: {r['content_type']}\r\n" if r.get("content_type") else ""
                out += f'Content-Disposition: form-data; name="{name}"\r\n{part_type}\r\n'.encode() + val.encode("utf-8")
            out += b"\r\n"
        out += f"--{boundary}--\r\n".encode()
        return bytes(out), f"multipart/form-data; boundary={boundary}"
    return None, None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def _decode(body, encoding):
    enc = (encoding or "").lower()
    try:
        if enc == "gzip":
            return gzip.decompress(body)
        if enc == "deflate":
            try:
                return zlib.decompress(body)
            except zlib.error:
                return zlib.decompress(body, -zlib.MAX_WBITS)
    except (OSError, zlib.error, EOFError):
        pass
    return body


def _signed_headers(req, values, method, url, headers, data, ctype):
    """Adds the header(s) for schemes that sign the final request (OAuth 1, AWS v4, Hawk)."""
    auth = {k: variables.substitute(str(v), values) for k, v in (req.get("auth") or {}).items()}
    atype = auth.get("type", "none")
    names = {k.lower() for k, _ in headers}

    def put(name, value):
        headers[:] = [(k, v) for k, v in headers if k.lower() != name.lower()]
        headers.append((name, value))
    if atype == "oauth1":
        form = []
        if ctype and "x-www-form-urlencoded" in ctype and data:
            form = urllib.parse.parse_qsl(data.decode("utf-8", "replace"), keep_blank_values=True)
        put("Authorization", auth_mod.oauth1_header(method, url, form, auth))
    elif atype == "aws":
        for k, v in auth_mod.aws4_headers(method, url, headers + ([("Content-Type", ctype)] if ctype and
                                                                  "content-type" not in names else []),
                                          data, auth).items():
            put(k, v)
    elif atype == "hawk":
        put("Authorization", auth_mod.hawk_header(method, url, auth, data, ctype or ""))


def _read(r):
    raw = r.read(MAX_BODY + 1)
    return (getattr(r, "status", None) or r.code, r.reason or "", list(r.headers.items()),
            raw, r.geturl() if hasattr(r, "geturl") else "")


def ssl_context(verify=True, client_cert=None, ca_file=""):
    """TLS settings shared by every HTTPS request: certificate checking, a custom CA bundle, a client certificate.
    `client_cert` is (cert_file, key_file_or_empty)."""
    ctx = ssl.create_default_context(cafile=ca_file or None) if verify else ssl._create_unverified_context()
    if client_cert and client_cert[0]:
        ctx.load_cert_chain(client_cert[0], client_cert[1] or None)
    return ctx


def proxy_handler(proxy):
    """'' = whatever Windows / the environment says (urllib's default), 'none' = direct, otherwise a proxy URL."""
    if not proxy:
        return None
    if proxy.lower() == "none":
        return urllib.request.ProxyHandler({})
    return urllib.request.ProxyHandler({"http": proxy, "https": proxy})


def _ntlm_exchange(url, method, headers, data, auth, timeout, ctx):
    """NTLM authenticates a connection, so all three messages go over one http.client connection."""
    import http.client
    p = urllib.parse.urlsplit(url)
    if p.scheme == "https":
        conn = http.client.HTTPSConnection(p.hostname, p.port, timeout=timeout, context=ctx)
    else:
        conn = http.client.HTTPConnection(p.hostname, p.port, timeout=timeout)
    target = (p.path or "/") + ("?" + p.query if p.query else "")
    base = [(k, v) for k, v in headers if k.lower() != "authorization"]
    try:
        conn.request(method, target, body=None if data is None else b"", headers=dict(
            base + [("Authorization", "NTLM " + auth_mod.b64(auth_mod.ntlm_negotiate())),
                    ("Content-Length", "0")] if data is not None else
            base + [("Authorization", "NTLM " + auth_mod.b64(auth_mod.ntlm_negotiate()))]))
        r1 = conn.getresponse()
        r1.read()
        challenge = r1.getheader("WWW-Authenticate", "")
        if r1.status != 401 or not challenge.upper().startswith("NTLM "):
            # no NTLM challenge: the server did not ask for it, report what it said
            conn.close()
            raise ValueError("The server did not offer NTLM authentication (HTTP %d)." % r1.status)
        blob = base64.b64decode(challenge[5:].strip())
        msg = auth_mod.ntlm_authenticate(blob, auth.get("username", ""), auth.get("password", ""),
                                         auth.get("domain", ""), auth.get("workstation", ""))
        conn.request(method, target, body=data, headers=dict(
            base + [("Authorization", "NTLM " + auth_mod.b64(msg))]))
        r2 = conn.getresponse()
        raw = r2.read(MAX_BODY + 1)
        return r2.status, r2.reason or "", list(r2.getheaders()), raw, url
    finally:
        conn.close()


def send(req, values, verify_ssl=True, follow_redirects=True, timeout=30, jar=None, proxy="", client_cert=None,
         ca_file=""):
    """Perform the request. Never raises: failures come back as Response(ok=False, error=...)."""
    resp = Response()
    try:
        url = build_url(req, values)
        if not url:
            raise ValueError("Enter a URL first.")
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError(f"Not a valid http(s) URL: {url}")
        method = req.get("method", "GET").upper()
        headers = build_headers(req, values)
        data, ctype = build_body(req, values)
        names = {k.lower() for k, _ in headers}
        if ctype and "content-type" not in names:
            headers.append(("Content-Type", ctype))
        if "user-agent" not in names:
            headers.append(("User-Agent", USER_AGENT))
        if "accept" not in names:
            headers.append(("Accept", "*/*"))
        if "accept-encoding" not in names:
            headers.append(("Accept-Encoding", "gzip, deflate"))
        _signed_headers(req, values, method, url, headers, data, ctype)
        resp.url = url
        resp.sent = {"method": method, "url": url, "headers": list(headers), "body": data}
        auth = {k: variables.substitute(str(v), values) for k, v in (req.get("auth") or {}).items()}
        atype = auth.get("type", "none")

        handlers = []
        ctx = ssl_context(verify_ssl, client_cert, ca_file) if parsed.scheme == "https" else None
        if ctx is not None:
            handlers.append(urllib.request.HTTPSHandler(context=ctx))
        ph = proxy_handler(proxy)
        if ph is not None:
            handlers.append(ph)
        if jar is not None:
            handlers.append(urllib.request.HTTPCookieProcessor(jar))
        if not follow_redirects:
            handlers.append(_NoRedirect())
        opener = urllib.request.build_opener(*handlers)

        def perform(hdrs):
            rq = urllib.request.Request(url, data=data, method=method)
            for k, v in hdrs:
                rq.add_header(k, v)
            try:
                r = opener.open(rq, timeout=timeout)
            except urllib.error.HTTPError as e:   # 4xx / 5xx are still responses
                r = e
            with r:
                return _read(r)

        started = time.perf_counter()
        if atype == "ntlm":
            status, reason, hdrs_out, raw, final = _ntlm_exchange(url, method, headers, data, auth, timeout, ctx)
        else:
            status, reason, hdrs_out, raw, final = perform(headers)
            if atype == "digest" and status == 401:
                challenge = next((v for k, v in hdrs_out if k.lower() == "www-authenticate"
                                  and v.lower().startswith("digest")), "")
                if challenge:
                    uri = parsed.path or "/"
                    if parsed.query:
                        uri += "?" + parsed.query
                    token = auth_mod.digest_header(method, uri, auth_mod.parse_challenge(challenge),
                                                   auth.get("username", ""), auth.get("password", ""), data or b"")
                    headers = [(k, v) for k, v in headers if k.lower() != "authorization"] + [("Authorization", token)]
                    resp.sent["headers"] = list(headers)
                    status, reason, hdrs_out, raw, final = perform(headers)
        resp.status, resp.reason, resp.headers, resp.url = status, reason, hdrs_out, final or url
        resp.elapsed_ms = int((time.perf_counter() - started) * 1000)
        raw = _decode(raw[:MAX_BODY], resp.header("Content-Encoding"))
        resp.body = raw
        resp.size = len(raw)
        resp.ok = True
        if jar is not None:
            host = parsed.hostname or ""
            resp.cookies = [{"name": c.name, "value": c.value or "", "domain": c.domain, "path": c.path}
                            for c in jar if host.endswith(c.domain.lstrip("."))]
    except Exception as e:                      # noqa: BLE001 - the UI shows any failure as text
        resp.ok = False
        resp.error = _explain(e)
    return resp


def _explain(e):
    if isinstance(e, urllib.error.URLError):
        reason = e.reason
        if isinstance(reason, ssl.SSLCertVerificationError):
            return f"SSL certificate problem: {reason.verify_message}. Turn off SSL verification in Settings to continue."
        if isinstance(reason, TimeoutError) or "timed out" in str(reason):
            return "The request timed out."
        return f"Could not connect: {reason}"
    if isinstance(e, OSError) and "timed out" in str(e):
        return "The request timed out."
    if isinstance(e, FileNotFoundError):
        return f"File not found: {e.filename}"
    if isinstance(e, ssl.SSLError):
        return f"TLS / certificate problem: {e}. Check the client certificate and CA bundle in Settings."
    return str(e) or e.__class__.__name__


def new_cookie_jar():
    return http.cookiejar.CookieJar()
