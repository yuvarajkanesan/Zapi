import base64
import json
import threading
import unittest
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

from zapi import auth, http_client, oauth2, store


class VectorTests(unittest.TestCase):
    def test_md4_and_nt_hash(self):
        self.assertEqual(auth.md4(b"").hex(), "31d6cfe0d16ae931b73c59d7e0c089c0")
        self.assertEqual(auth.md4(b"abc").hex(), "a448017aaf21d8525fc10ae87aa6729d")
        self.assertEqual(auth.md4(b"message digest").hex(), "d9130a8164549fe818874806e1c7014b")
        self.assertEqual(auth.nt_hash("Password").hex(), "a4f49c406510bdcab6824ee7c30fd852")
        self.assertEqual(auth.md4(b"a" * 1000).hex(), auth.md4(b"a" * 1000).hex())

    def test_digest_rfc2617_example(self):
        ch = auth.parse_challenge('Digest realm="testrealm@host.com", qop="auth,auth-int", '
                                  'nonce="dcd98b7102dd2f0e8b11d0f600bfb0c093", '
                                  'opaque="5ccc069c403ebaf9f0171e9517f40e41"')
        hdr = auth.digest_header("GET", "/dir/index.html", ch, "Mufasa", "Circle Of Life", cnonce="0a4f113b")
        self.assertIn('response="6629fae49393a05397450978507c4ef1"', hdr)
        self.assertIn("nc=00000001", hdr)

    def test_aws_sigv4_vanilla_vector(self):
        a = {"access_key": "AKIDEXAMPLE", "secret_key": "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
             "region": "us-east-1", "service": "service"}
        out = auth.aws4_headers("GET", "https://example.amazonaws.com/", [], b"", a, now=1440938160)
        self.assertEqual(out["X-Amz-Date"], "20150830T123600Z")
        self.assertIn("Signature=5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31",
                      out["Authorization"])
        self.assertIn("SignedHeaders=host;x-amz-date", out["Authorization"])

    def test_oauth1_rfc5849_example(self):
        # RFC 5849 section 3.4.1 example request, signed with HMAC-SHA1 (values from the Twitter docs example)
        a = {"consumer_key": "xvz1evFS4wEEPTGEFPHBog", "consumer_secret": "kAcSOqF21Fu85e7zjz7ZN2U4ZRhfV3WpwPAoE3Z7kBw",
             "token": "370773112-GmHxMAgYyLbNEtIKZeRNFsMKPR9EyMZeS9weJAEb",
             "token_secret": "LswwdoUaIvS8ltyTt5jkRh4J50vUPVVHtR2YPi5kE"}
        hdr = auth.oauth1_header("POST", "https://api.twitter.com/1.1/statuses/update.json?include_entities=true",
                                 [("status", "Hello Ladies + Gentlemen, a signed OAuth request!")], a,
                                 nonce="kYjzVBB8Y0ZFabxSWbWovY3uYSQ2pTgmZeNu2VS4cg", timestamp=1318622958)
        self.assertIn('oauth_signature="hCtSmYh%2BiHYCEqBWrE7C7hYmtUk%3D"', hdr)

    def test_hawk_is_deterministic(self):
        a = {"hawk_id": "dh37fgj492je", "hawk_key": "werxhqb98rpaxn39848xrunpaw3489ruxnpa98w4rxn",
             "hawk_algorithm": "sha256", "hawk_ext": "some-app-ext-data"}
        h1 = auth.hawk_header("GET", "http://example.com:8000/resource/1?b=1&a=2", a, nonce="j4h3g2",
                              timestamp=1353832234)
        h2 = auth.hawk_header("GET", "http://example.com:8000/resource/1?b=1&a=2", a, nonce="j4h3g2",
                              timestamp=1353832234)
        self.assertEqual(h1, h2)
        self.assertIn('mac="', h1)
        # official Hawk test vector (no ext): mac for this request
        self.assertIn('mac="6R4rV5iE+NPoym+WwjeHzjAGXUtLNIxmo1vpMofpLAE="', h1)


class AuthHandler(BaseHTTPRequestHandler):
    ntlm_challenge = None
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, status, body=b"ok", headers=()):
        self.send_response(status)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        h = self.headers.get("Authorization", "")
        if self.path.startswith("/digest"):
            if h.startswith("Digest "):
                params = auth.parse_challenge(h)
                ch = {"realm": "zapi", "nonce": "abc123", "qop": "auth"}
                uri = params.get("uri", "")
                good = auth.digest_header("GET", uri, ch, "me", "pw", cnonce=params.get("cnonce"))
                if params.get("response") and f'response="{params["response"]}"' in good:
                    return self._send(200, b"digest-ok")
            return self._send(401, b"no", [("WWW-Authenticate", 'Digest realm="zapi", nonce="abc123", qop="auth"')])
        if self.path.startswith("/ntlm"):
            if h.upper().startswith("NTLM "):
                blob = base64.b64decode(h[5:])
                import struct
                if struct.unpack("<I", blob[8:12])[0] == 1:
                    chal = (b"NTLMSSP\x00" + struct.pack("<I", 2) + struct.pack("<HHI", 0, 0, 56) +
                            struct.pack("<I", 0xE2088297) + b"01234567" + b"\x00" * 8 +
                            struct.pack("<HHI", 0, 0, 56) + b"\x00" * 8)
                    AuthHandler.ntlm_challenge = chal
                    return self._send(401, b"", [("WWW-Authenticate", "NTLM " + auth.b64(chal))])
                if auth.ntlm_verify(blob, AuthHandler.ntlm_challenge, "pw", "me", "DOM"):
                    return self._send(200, b"ntlm-ok")
                return self._send(403, b"bad proof")
            return self._send(401, b"", [("WWW-Authenticate", "NTLM")])
        self._send(200, json.dumps({"authorization": h, "path": self.path,
                                    "amz": self.headers.get("X-Amz-Date", "")}).encode())

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n).decode()
        if self.path == "/token":
            fields = dict(urllib.parse.parse_qsl(body))
            basic = self.headers.get("Authorization", "")
            if fields.get("grant_type") == "client_credentials" and basic == "Basic " + base64.b64encode(b"cid:sec").decode():
                return self._send(200, json.dumps({"access_token": "AT1", "refresh_token": "RT1",
                                                   "expires_in": 3600}).encode(), [("Content-Type", "application/json")])
            if fields.get("grant_type") == "password" and fields.get("client_id") == "cid" \
                    and fields.get("username") == "u" and fields.get("password") == "p":
                return self._send(200, json.dumps({"access_token": "AT2"}).encode())
            if fields.get("grant_type") == "refresh_token" and fields.get("refresh_token") == "RT1":
                return self._send(200, json.dumps({"access_token": "AT3"}).encode())
            if fields.get("grant_type") == "authorization_code" and fields.get("code") == "CODE" \
                    and fields.get("code_verifier"):
                return self._send(200, json.dumps({"access_token": "AT4"}).encode())
            return self._send(400, b'{"error":"invalid_grant"}')
        self._send(200, b"posted")


class AuthFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), AuthHandler)
        cls.base = f"http://127.0.0.1:{cls.srv.server_port}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def req(self, path, **auth_fields):
        r = store.new_request(url=self.base + path)
        r["auth"].update(auth_fields)
        return r

    def test_digest_handshake(self):
        resp = http_client.send(self.req("/digest", type="digest", username="me", password="pw"), {})
        self.assertEqual((resp.status, resp.body), (200, b"digest-ok"))
        bad = http_client.send(self.req("/digest", type="digest", username="me", password="wrong"), {})
        self.assertEqual(bad.status, 401)

    def test_ntlm_handshake(self):
        ok = http_client.send(self.req("/ntlm", type="ntlm", username="me", password="pw", domain="DOM"), {})
        self.assertEqual((ok.status, ok.body), (200, b"ntlm-ok"))
        bad = http_client.send(self.req("/ntlm", type="ntlm", username="me", password="nope", domain="DOM"), {})
        self.assertEqual(bad.status, 403)

    def test_header_schemes_reach_the_server(self):
        e = json.loads(http_client.send(self.req("/x?a=1", type="oauth1", consumer_key="k", consumer_secret="s"),
                                        {}).body)
        self.assertTrue(e["authorization"].startswith("OAuth "))
        self.assertIn("oauth_signature=", e["authorization"])
        e = json.loads(http_client.send(self.req("/x", type="aws", access_key="AK", secret_key="SK", service="s3"),
                                        {}).body)
        self.assertTrue(e["authorization"].startswith("AWS4-HMAC-SHA256 Credential=AK/"))
        self.assertRegex(e["amz"], r"^\d{8}T\d{6}Z$")
        e = json.loads(http_client.send(self.req("/x", type="hawk", hawk_id="i", hawk_key="k"), {}).body)
        self.assertTrue(e["authorization"].startswith("Hawk id="))
        e = json.loads(http_client.send(self.req("/x", type="oauth2", token="TOK", o2_prefix="Bearer"), {}).body)
        self.assertEqual(e["authorization"], "Bearer TOK")

    def test_oauth2_grants(self):
        a = {**store.AUTH_DEFAULTS, "o2_token_url": self.base + "/token", "o2_client_id": "cid",
             "o2_client_secret": "sec"}
        t = oauth2.request_token({**a, "o2_grant": oauth2.GRANTS[0]})
        self.assertEqual((t["access_token"], t["refresh_token"]), ("AT1", "RT1"))
        t = oauth2.request_token({**a, "o2_grant": oauth2.GRANTS[1], "username": "u", "password": "p",
                                  "o2_client_auth": "Request body"})
        self.assertEqual(t["access_token"], "AT2")
        t = oauth2.request_token({**a, "o2_grant": oauth2.GRANTS[3], "o2_refresh_token": "RT1"})
        self.assertEqual(t["access_token"], "AT3")
        with self.assertRaises(oauth2.OAuthError):
            oauth2.request_token({**a, "o2_grant": oauth2.GRANTS[3], "o2_refresh_token": ""})
        with self.assertRaises(oauth2.OAuthError) as cm:
            oauth2.request_token({**a, "o2_grant": oauth2.GRANTS[1], "username": "u", "password": "bad"})
        self.assertIn("invalid_grant", str(cm.exception))

    def test_authorization_code_flow_with_pkce(self):
        seen = {}

        def browser(url):
            q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
            seen.update(q)
            urllib.request.urlopen(f"{q['redirect_uri']}?code=CODE&state={q['state']}").read()

        a = {**store.AUTH_DEFAULTS, "o2_auth_url": "https://auth.example/authorize", "o2_token_url": self.base + "/token",
             "o2_client_id": "cid", "o2_client_secret": "sec", "o2_scope": "read"}
        t = oauth2.authorization_code_flow(a, timeout=10, open_browser=browser)
        self.assertEqual(t["access_token"], "AT4")
        self.assertEqual(seen["code_challenge_method"], "S256")
        self.assertEqual(seen["scope"], "read")

    def test_state_mismatch_is_rejected(self):
        def browser(url):
            q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
            urllib.request.urlopen(f"{q['redirect_uri']}?code=CODE&state=WRONG").read()
        a = {**store.AUTH_DEFAULTS, "o2_auth_url": "https://a/x", "o2_token_url": self.base + "/token"}
        with self.assertRaises(oauth2.OAuthError):
            oauth2.authorization_code_flow(a, timeout=10, open_browser=browser)


if __name__ == "__main__":
    unittest.main()
