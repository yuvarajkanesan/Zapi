"""Request-signing schemes: OAuth 1.0, AWS Signature v4, Hawk, HTTP Digest and NTLMv2. Pure Python, no Qt."""
import base64
import hashlib
import hmac
import os
import re
import secrets
import struct
import time
import urllib.parse


# ---- helpers -----------------------------------------------------------------------------------
def _enc(s):
    """RFC 3986 percent-encoding as OAuth 1.0 and AWS require."""
    return urllib.parse.quote(str(s), safe="-_.~")


def _split(url):
    p = urllib.parse.urlsplit(url)
    return p.scheme.lower(), (p.hostname or "").lower(), p.port, p.path or "/", p.query


def _port(scheme, port):
    return port or (443 if scheme == "https" else 80)


# ---- OAuth 1.0 ---------------------------------------------------------------------------------
def oauth1_header(method, url, body_params, a, nonce=None, timestamp=None):
    """Authorization header value. `body_params` are the form fields of an urlencoded body."""
    scheme, host, port, path, query = _split(url)
    default = (scheme == "https" and port in (None, 443)) or (scheme == "http" and port in (None, 80))
    base_url = f"{scheme}://{host}{'' if default else ':' + str(port)}{path}"
    method_name = (a.get("sig_method") or "HMAC-SHA1").upper()
    oauth = {
        "oauth_consumer_key": a.get("consumer_key", ""),
        "oauth_signature_method": method_name,
        "oauth_timestamp": str(timestamp or int(time.time())),
        "oauth_nonce": nonce or secrets.token_hex(8),
        "oauth_version": "1.0",
    }
    if a.get("token"):
        oauth["oauth_token"] = a["token"]
    pairs = list(urllib.parse.parse_qsl(query, keep_blank_values=True)) + list(body_params) + list(oauth.items())
    norm = "&".join(f"{k}={v}" for k, v in sorted((_enc(k), _enc(v)) for k, v in pairs))
    base = "&".join([method.upper(), _enc(base_url), _enc(norm)])
    key = f"{_enc(a.get('consumer_secret', ''))}&{_enc(a.get('token_secret', ''))}"
    if method_name == "PLAINTEXT":
        sig = key
    else:
        algo = hashlib.sha256 if method_name == "HMAC-SHA256" else hashlib.sha1
        sig = base64.b64encode(hmac.new(key.encode(), base.encode(), algo).digest()).decode()
    oauth["oauth_signature"] = sig
    parts = []
    if a.get("realm"):
        parts.append(f'realm="{a["realm"]}"')
    parts += [f'{k}="{_enc(v)}"' for k, v in sorted(oauth.items())]
    return "OAuth " + ", ".join(parts)


# ---- AWS Signature v4 ----------------------------------------------------------------------------
def aws4_headers(method, url, headers, body, a, now=None):
    """Returns the headers to add: Authorization, X-Amz-Date (and token / content hash when needed)."""
    scheme, host, port, path, query = _split(url)
    service, region = a.get("service", ""), a.get("region", "") or "us-east-1"
    t = time.gmtime(now) if now is not None else time.gmtime()
    amz_date = time.strftime("%Y%m%dT%H%M%SZ", t)
    day = amz_date[:8]
    payload_hash = hashlib.sha256(body or b"").hexdigest()
    host_header = host if port in (None, 80, 443) else f"{host}:{port}"
    signed = {"host": host_header, "x-amz-date": amz_date}
    ct = next((v for k, v in headers if k.lower() == "content-type"), None)
    if ct:
        signed["content-type"] = ct
    if a.get("session_token"):
        signed["x-amz-security-token"] = a["session_token"]
    if service == "s3":
        signed["x-amz-content-sha256"] = payload_hash
    names = sorted(signed)
    canonical_headers = "".join(f"{n}:{' '.join(str(signed[n]).split())}\n" for n in names)
    canonical_path = path if service == "s3" else urllib.parse.quote(path, safe="/~")
    q = sorted((_enc(k), _enc(v)) for k, v in urllib.parse.parse_qsl(query, keep_blank_values=True))
    canonical_query = "&".join(f"{k}={v}" for k, v in q)
    canonical = "\n".join([method.upper(), canonical_path, canonical_query, canonical_headers,
                           ";".join(names), payload_hash])
    scope = f"{day}/{region}/{service}/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])

    def sign(key, msg):
        return hmac.new(key, msg.encode(), hashlib.sha256).digest()

    k = sign(("AWS4" + a.get("secret_key", "")).encode(), day)
    for part in (region, service, "aws4_request"):
        k = sign(k, part)
    signature = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
    out = {"X-Amz-Date": amz_date, "Authorization":
           f"AWS4-HMAC-SHA256 Credential={a.get('access_key', '')}/{scope}, SignedHeaders={';'.join(names)}, "
           f"Signature={signature}"}
    if a.get("session_token"):
        out["X-Amz-Security-Token"] = a["session_token"]
    if service == "s3":
        out["X-Amz-Content-Sha256"] = payload_hash
    return out


# ---- Hawk ----------------------------------------------------------------------------------------------
def hawk_header(method, url, a, body=None, content_type="", nonce=None, timestamp=None):
    scheme, host, port, path, query = _split(url)
    algo = hashlib.sha1 if (a.get("hawk_algorithm") or "sha256").lower() == "sha1" else hashlib.sha256
    ts = str(timestamp or int(time.time()))
    nonce = nonce or secrets.token_hex(3)
    ext = a.get("hawk_ext", "")
    payload_hash = ""
    if body:
        h = algo()
        h.update(f"hawk.1.payload\n{content_type.split(';')[0].strip().lower()}\n".encode() + body + b"\n")
        payload_hash = base64.b64encode(h.digest()).decode()
    resource = path + (("?" + query) if query else "")
    normalized = "\n".join(["hawk.1.header", ts, nonce, method.upper(), resource, host, str(_port(scheme, port)),
                            payload_hash, ext, ""])
    mac = base64.b64encode(hmac.new(a.get("hawk_key", "").encode(), normalized.encode(), algo).digest()).decode()
    parts = [f'id="{a.get("hawk_id", "")}"', f'ts="{ts}"', f'nonce="{nonce}"']
    if payload_hash:
        parts.append(f'hash="{payload_hash}"')
    if ext:
        parts.append(f'ext="{ext}"')
    parts.append(f'mac="{mac}"')
    return "Hawk " + ", ".join(parts)


# ---- HTTP Digest ---------------------------------------------------------------------------------------
def parse_challenge(header):
    """'Digest realm="x", qop="auth"' -> {"realm": "x", "qop": "auth"} (first Digest challenge)."""
    m = re.search(r"digest\s+(.*)", header, re.I)
    if not m:
        return {}
    return {k.lower(): (v1 if v1 else v2) for k, v1, v2 in
            re.findall(r'(\w+)=(?:"([^"]*)"|([^\s,]*))', m.group(1))}


def digest_header(method, uri, challenge, username, password, body=b"", nc=1, cnonce=None):
    algorithm = (challenge.get("algorithm") or "MD5")
    base = algorithm.upper().replace("-SESS", "")
    hasher = {"MD5": hashlib.md5, "SHA-256": hashlib.sha256, "SHA-512-256": lambda d=b"": hashlib.new("sha512_256", d)
              }.get(base, hashlib.md5)

    def H(s):
        return hasher(s.encode() if isinstance(s, str) else s).hexdigest()

    realm, nonce = challenge.get("realm", ""), challenge.get("nonce", "")
    qops = [q.strip() for q in (challenge.get("qop") or "").split(",") if q.strip()]
    qop = "auth" if "auth" in qops else ("auth-int" if "auth-int" in qops else "")
    cnonce = cnonce or secrets.token_hex(8)
    ncs = f"{nc:08x}"
    ha1 = H(f"{username}:{realm}:{password}")
    if algorithm.upper().endswith("-SESS"):
        ha1 = H(f"{ha1}:{nonce}:{cnonce}")
    ha2 = H(f"{method.upper()}:{uri}:{H(body or b'')}") if qop == "auth-int" else H(f"{method.upper()}:{uri}")
    response = H(f"{ha1}:{nonce}:{ncs}:{cnonce}:{qop}:{ha2}") if qop else H(f"{ha1}:{nonce}:{ha2}")
    parts = [f'username="{username}"', f'realm="{realm}"', f'nonce="{nonce}"', f'uri="{uri}"',
             f'response="{response}"']
    if challenge.get("algorithm"):
        parts.append(f"algorithm={algorithm}")
    if qop:
        parts += [f"qop={qop}", f"nc={ncs}", f'cnonce="{cnonce}"']
    if challenge.get("opaque"):
        parts.append(f'opaque="{challenge["opaque"]}"')
    return "Digest " + ", ".join(parts)


# ---- NTLMv2 --------------------------------------------------------------------------------------------
def md4(data: bytes) -> bytes:
    """Pure-Python MD4 (OpenSSL 3 no longer ships it, but NTLM still needs it)."""
    def rol(x, n):
        x &= 0xFFFFFFFF
        return ((x << n) | (x >> (32 - n))) & 0xFFFFFFFF
    msg = bytearray(data)
    bitlen = (len(data) * 8) & 0xFFFFFFFFFFFFFFFF
    msg.append(0x80)
    while len(msg) % 64 != 56:
        msg.append(0)
    msg += struct.pack("<Q", bitlen)
    a, b, c, d = 0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476
    for off in range(0, len(msg), 64):
        X = list(struct.unpack("<16I", msg[off:off + 64]))
        aa, bb, cc, dd = a, b, c, d
        for i in range(16):
            k, s = i, (3, 7, 11, 19)[i % 4]
            f = (b & c) | (~b & d)
            a, b, c, d = d, rol(a + f + X[k], s), b, c
        for i in range(16):
            k, s = (i % 4) * 4 + i // 4, (3, 5, 9, 13)[i % 4]
            g = (b & c) | (b & d) | (c & d)
            a, b, c, d = d, rol(a + g + X[k] + 0x5A827999, s), b, c
        order = (0, 8, 4, 12, 2, 10, 6, 14, 1, 9, 5, 13, 3, 11, 7, 15)
        for i in range(16):
            k, s = order[i], (3, 9, 11, 15)[i % 4]
            h = b ^ c ^ d
            a, b, c, d = d, rol(a + h + X[k] + 0x6ED9EBA1, s), b, c
        a, b, c, d = (a + aa) & 0xFFFFFFFF, (b + bb) & 0xFFFFFFFF, (c + cc) & 0xFFFFFFFF, (d + dd) & 0xFFFFFFFF
    return struct.pack("<4I", a, b, c, d)


def nt_hash(password):
    return md4(password.encode("utf-16-le"))


def ntlmv2_hash(user, password, domain):
    return hmac.new(nt_hash(password), (user.upper() + domain).encode("utf-16-le"), hashlib.md5).digest()


_NTLM_SIG = b"NTLMSSP\x00"
_NEGOTIATE_FLAGS = 0xE2088297   # unicode, NTLM, sign, request target, extended security, 128/56-bit, target info


def ntlm_negotiate():
    return _NTLM_SIG + struct.pack("<I", 1) + struct.pack("<I", _NEGOTIATE_FLAGS) + b"\x00" * 16


def ntlm_parse_challenge(blob):
    if blob[:8] != _NTLM_SIG or struct.unpack("<I", blob[8:12])[0] != 2:
        raise ValueError("The server sent an invalid NTLM challenge.")
    flags = struct.unpack("<I", blob[20:24])[0]
    server_challenge = blob[24:32]
    ln, _mx, off = struct.unpack("<HHI", blob[40:48]) if len(blob) >= 48 else (0, 0, 0)
    return server_challenge, blob[off:off + ln], flags


def _filetime():
    return struct.pack("<Q", int((time.time() + 11644473600) * 10_000_000))


def ntlm_authenticate(challenge_blob, user, password, domain="", workstation="", client_nonce=None, timestamp=None):
    server_challenge, target_info, flags = ntlm_parse_challenge(challenge_blob)
    client_nonce = client_nonce or os.urandom(8)
    v2 = ntlmv2_hash(user, password, domain)
    blob = (b"\x01\x01\x00\x00" + b"\x00" * 4 + (timestamp or _filetime()) + client_nonce + b"\x00" * 4 +
            target_info + b"\x00" * 4)
    nt_resp = hmac.new(v2, server_challenge + blob, hashlib.md5).digest() + blob
    lm_resp = hmac.new(v2, server_challenge + client_nonce, hashlib.md5).digest() + client_nonce
    enc = "utf-16-le" if flags & 1 else "latin-1"
    fields = [domain.encode(enc), user.encode(enc), workstation.encode(enc), lm_resp, nt_resp, b""]
    offset = 64
    header = b""
    payload = b""
    # security buffer order in the type-3 header: LM, NT, domain, user, workstation, session key
    order = {"lm": fields[3], "nt": fields[4], "domain": fields[0], "user": fields[1], "ws": fields[2],
             "key": fields[5]}
    pos = {}
    for name in ("domain", "user", "ws", "lm", "nt", "key"):     # payload layout
        pos[name] = offset + len(payload)
        payload += order[name]
    for name in ("lm", "nt", "domain", "user", "ws", "key"):
        header += struct.pack("<HHI", len(order[name]), len(order[name]), pos[name])
    out_flags = flags & ~0x40000000       # no key exchange: we send no session key
    return _NTLM_SIG + struct.pack("<I", 3) + header + struct.pack("<I", out_flags) + payload


def ntlm_verify(authenticate_blob, challenge_blob, password, expect_user, expect_domain):
    """Server-side check used by the tests: recompute the proof from the password."""
    server_challenge, _t, flags = ntlm_parse_challenge(challenge_blob)
    def buf(off):
        ln, _m, o = struct.unpack("<HHI", authenticate_blob[off:off + 8])
        return authenticate_blob[o:o + ln]
    lm, nt, dom, user = buf(12), buf(20), buf(28), buf(36)
    enc = "utf-16-le" if flags & 1 else "latin-1"
    if user.decode(enc) != expect_user or dom.decode(enc) != expect_domain:
        return False
    v2 = ntlmv2_hash(expect_user, password, expect_domain)
    proof, blob = nt[:16], nt[16:]
    return hmac.compare_digest(proof, hmac.new(v2, server_challenge + blob, hashlib.md5).digest())


def b64(data):
    return base64.b64encode(data).decode("ascii")
