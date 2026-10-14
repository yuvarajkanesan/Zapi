"""OAuth 2.0 token acquisition: client credentials, password, authorization code (with PKCE) and refresh."""
import base64
import hashlib
import json
import secrets
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

GRANTS = ["Client credentials", "Password", "Authorization code (PKCE)", "Refresh token"]


class OAuthError(Exception):
    pass


def _post_form(url, fields, basic=None, timeout=30, verify=True):
    data = urllib.parse.urlencode(fields).encode()
    rq = urllib.request.Request(url, data=data, method="POST", headers={
        "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"})
    if basic:
        rq.add_header("Authorization", "Basic " + base64.b64encode(f"{basic[0]}:{basic[1]}".encode()).decode())
    ctx = ssl.create_default_context() if verify else ssl._create_unverified_context()
    try:
        with urllib.request.urlopen(rq, timeout=timeout, context=ctx if url.startswith("https") else None) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:400]
        raise OAuthError(f"The token server answered {e.code}: {detail}")
    except (urllib.error.URLError, OSError) as e:
        raise OAuthError(f"Could not reach the token server: {getattr(e, 'reason', e)}")
    try:
        out = json.loads(raw)
    except ValueError:
        # some servers (GitHub) answer form-encoded unless asked for JSON
        out = dict(urllib.parse.parse_qsl(raw.decode("utf-8", "replace")))
    if not isinstance(out, dict) or "access_token" not in out:
        raise OAuthError("The token response had no access_token: " + str(out)[:300])
    return out


def request_token(a, extra_fields=None, timeout=30, verify=True):
    """`a` is the request's auth dict. Returns the token response (access_token, refresh_token, expires_in...)."""
    grant = a.get("o2_grant", GRANTS[0])
    fields = {}
    if grant == GRANTS[0]:
        fields["grant_type"] = "client_credentials"
    elif grant == GRANTS[1]:
        fields.update(grant_type="password", username=a.get("username", ""), password=a.get("password", ""))
    elif grant == GRANTS[3]:
        if not a.get("o2_refresh_token"):
            raise OAuthError("There is no refresh token yet. Get a new access token first.")
        fields.update(grant_type="refresh_token", refresh_token=a["o2_refresh_token"])
    else:
        raise OAuthError("Use authorization_code_flow for this grant type.")
    if a.get("o2_scope"):
        fields["scope"] = a["o2_scope"]
    fields.update(extra_fields or {})
    return _exchange(a, fields, timeout, verify)


def _exchange(a, fields, timeout, verify):
    if not a.get("o2_token_url"):
        raise OAuthError("Enter the access token URL.")
    cid, secret = a.get("o2_client_id", ""), a.get("o2_client_secret", "")
    basic = None
    if (a.get("o2_client_auth") or "Basic header") == "Basic header" and cid:
        basic = (cid, secret)
    else:
        fields["client_id"] = cid
        if secret:
            fields["client_secret"] = secret
    return _post_form(a["o2_token_url"], fields, basic, timeout, verify)


def authorization_code_flow(a, timeout=180, verify=True, open_browser=webbrowser.open, port=0):
    """Opens the browser at the authorization URL and waits for the redirect on a loopback port."""
    if not a.get("o2_auth_url"):
        raise OAuthError("Enter the authorization URL.")
    result = {}
    state = secrets.token_urlsafe(16)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query))
            if urllib.parse.urlsplit(self.path).path != "/callback":
                self.send_response(404)
                self.end_headers()
                return
            result.update(q)
            body = (b"<html><body style='font-family:sans-serif;background:#0b0d12;color:#e8ebf2;text-align:center;"
                    b"padding-top:80px'><h2>Zapi</h2><p>Authorization complete. You can close this tab.</p></body></html>")
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = HTTPServer(("127.0.0.1", port), Handler)
    redirect = f"http://127.0.0.1:{server.server_port}/callback"
    query = {"response_type": "code", "client_id": a.get("o2_client_id", ""), "redirect_uri": redirect,
             "state": state, "code_challenge": challenge, "code_challenge_method": "S256"}
    if a.get("o2_scope"):
        query["scope"] = a["o2_scope"]
    sep = "&" if "?" in a["o2_auth_url"] else "?"
    url = a["o2_auth_url"] + sep + urllib.parse.urlencode(query)
    server.timeout = 1
    deadline = time.time() + timeout
    threading.Thread(target=open_browser, args=(url,), daemon=True).start()
    try:
        while not result and time.time() < deadline:
            server.handle_request()
    finally:
        server.server_close()
    if not result:
        raise OAuthError("Timed out waiting for the browser to finish authorizing.")
    if "error" in result:
        raise OAuthError(f"Authorization failed: {result.get('error_description') or result['error']}")
    if result.get("state") != state:
        raise OAuthError("The authorization response did not match this request (state mismatch).")
    return _exchange(a, {"grant_type": "authorization_code", "code": result.get("code", ""),
                         "redirect_uri": redirect, "code_verifier": verifier}, 30, verify)
