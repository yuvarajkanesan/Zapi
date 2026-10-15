"""Keeps secrets out of files.

* At rest: secrets inside workspace.json are encrypted with Windows DPAPI. DPAPI keys the data to the signed-in
  Windows user, so the file is useless on another account or computer and Zapi never stores or asks for a key.
  In memory everything stays plain text; only what is written to disk is protected.
* Sharing: exports leave credentials out ({{variable}} references are kept: they are not secrets).
* Moving between machines: a full backup can be encrypted with a passphrase (scrypt + HMAC-SHA256 authenticated stream).

What counts as a secret, even when the user did not mark it:
  - variables marked Secret, and variables whose NAME looks sensitive (token, api_key, password ...);
  - every auth field (passwords, tokens, keys), an MQTT password, a proxy URL with credentials;
  - header, query-parameter and form-field VALUES whose name looks sensitive (Authorization, Cookie, X-API-Key ...);
  - a URL carrying user:password@ or ?token=...;
  - a script or body that contains a hard-coded credential literal (password: "...", Bearer <token>, long random keys).
"""
import base64
import copy
import hashlib
import hmac
import json
import os
import re
import sys
import zlib

PREFIX = "dpapi:"
_ENTROPY = b"Zapi workspace secrets v1"
AUTH_SECRET_KEYS = ("password", "token", "consumer_secret", "token_secret", "o2_client_secret", "o2_refresh_token",
                    "secret_key", "session_token", "hawk_key", "value")
PROTO_SECRET_KEYS = ("password",)
_cache = {}


class VaultError(Exception):
    pass


class BackupError(Exception):
    pass


def available():
    return sys.platform == "win32"


# ---- what looks secret --------------------------------------------------------------------------------------
_NAME = r"(?:authori[sz]ation|auth|token|secret|passw(?:or)?d|pwd|api[-_ ]?key|apikey|access[-_ ]?key|private[-_ ]?key|" \
        r"signature|credential|session|cookie|bearer|jwt)"
_NAME_RE = re.compile(_NAME, re.I)
_EXACT_NAMES = {"key", "sig", "sid", "pass", "pw", "x-api", "apikey"}
_TEXT_RES = [
    # name: "value" / name = 'value' / set('name', 'value') with a non-reference literal of 6+ characters
    re.compile(r"""(?i)["']?[\w.\-]*""" + _NAME + r"""[\w.\-]*["']?\s*[:=,(]\s*["'](?!\{\{)([^"'\s\\]{6,})["']"""),
    re.compile(r"""(?i)\bBearer\s+(?!\{\{)([A-Za-z0-9._~+/=\-]{12,})"""),
    re.compile(r"""(?i)\bBasic\s+(?!\{\{)([A-Za-z0-9+/=]{12,})"""),
    re.compile(r"""["'](?!\{\{)([A-Za-z0-9+/_=\-]{32,})["']"""),             # long random-looking literal
]
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_URL_USERINFO = re.compile(r"(://[^/@\s:]+:)([^/@\s]+)(@)")
_URL_PARAM = re.compile(r"([?&])([^=&#]*)=([^&#]*)")


def is_sensitive_name(name):
    n = (name or "").strip().lower()
    return bool(n) and (n in _EXACT_NAMES or bool(_NAME_RE.search(n)))


def _is_reference(v):
    v = v.strip()
    return v.startswith("{{") and v.endswith("}}") and v.count("{{") == 1 and v.count("}}") == 1


def _secret_literal(m):
    lit = m.group(1)
    if lit.lower() in ("true", "false", "null", "undefined", "bearer", "string", "password", "token"):
        return False
    if len(lit) >= 32 and m.re is _TEXT_RES[3]:                  # long literals must mix letters and digits
        if _UUID.match(lit):                                     # ids are not credentials
            return False
        return any(c.isdigit() for c in lit) and any(c.isalpha() for c in lit)
    return True


def contains_secret_literal(text):
    return isinstance(text, str) and any(_secret_literal(m) for rx in _TEXT_RES for m in rx.finditer(text))


def redact_text(text):
    """`text` with the literal credential values removed (the surrounding code is kept)."""
    for rx in _TEXT_RES:
        text = rx.sub(lambda m: m.group(0).replace(m.group(1), "", 1) if _secret_literal(m) else m.group(0), text)
    return text


def url_has_secret(url):
    if not isinstance(url, str) or not url:
        return False
    if _URL_USERINFO.search(url) and not _is_reference(_URL_USERINFO.search(url).group(2)):
        return True
    return any(is_sensitive_name(m.group(2)) and m.group(3) and not _is_reference(m.group(3))
               for m in _URL_PARAM.finditer(url))


def redact_url(url):
    url = _URL_USERINFO.sub(lambda m: m.group(1) + m.group(3) if not _is_reference(m.group(2)) else m.group(0), url)
    return _URL_PARAM.sub(lambda m: m.group(0) if not is_sensitive_name(m.group(2)) or _is_reference(m.group(3))
                          else f"{m.group(1)}{m.group(2)}=", url)


def is_secret_variable(var):
    """A variable marked Secret, or one whose name says it holds a credential and which has a real value."""
    if var.get("secret"):
        return True
    return is_sensitive_name(var.get("key", "")) and any(
        isinstance(var.get(k), str) and len(var[k]) >= 6 and not _is_reference(var[k]) for k in ("initial", "current"))


# ---- DPAPI -------------------------------------------------------------------------------------------------
def _dpapi():
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    ptr = ctypes.POINTER(Blob)
    crypt32.CryptProtectData.argtypes = [ptr, wintypes.LPCWSTR, ptr, ctypes.c_void_p, ctypes.c_void_p,
                                         wintypes.DWORD, ptr]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [ptr, ctypes.c_void_p, ptr, ctypes.c_void_p, ctypes.c_void_p,
                                           wintypes.DWORD, ptr]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    return ctypes, Blob, crypt32, kernel32


def _call(fn_name, data):
    ctypes, Blob, crypt32, kernel32 = _dpapi()
    buf = ctypes.create_string_buffer(data, len(data))
    src = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    ebuf = ctypes.create_string_buffer(_ENTROPY, len(_ENTROPY))
    entropy = Blob(len(_ENTROPY), ctypes.cast(ebuf, ctypes.POINTER(ctypes.c_char)))
    out = Blob()
    flags = 0x1                                           # CRYPTPROTECT_UI_FORBIDDEN: never pop up a dialog
    if fn_name == "protect":
        ok = crypt32.CryptProtectData(ctypes.byref(src), "Zapi", ctypes.byref(entropy), None, None, flags,
                                      ctypes.byref(out))
    else:
        ok = crypt32.CryptUnprotectData(ctypes.byref(src), None, ctypes.byref(entropy), None, None, flags,
                                        ctypes.byref(out))
    if not ok:
        raise VaultError(f"Windows could not {fn_name} the data (error {ctypes.GetLastError()}).")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def protect(text):
    """'dpapi:<base64>' for `text`. Identical inputs give identical tokens within a session (keeps saves stable)."""
    if not available():
        raise VaultError("Encryption needs Windows.")
    if text in _cache:
        return _cache[text]
    token = PREFIX + base64.b64encode(_call("protect", text.encode("utf-8"))).decode("ascii")
    if len(_cache) > 4096:
        _cache.clear()
    _cache[text] = token
    return token


def protect_bytes(data):
    """DPAPI-encrypt raw bytes for this Windows user (used for the whole workspace file)."""
    if not available():
        raise VaultError("Encryption needs Windows.")
    return _call("protect", data)


def unprotect_bytes(data):
    if not available():
        raise VaultError("Decryption needs Windows.")
    return _call("unprotect", data)


def unprotect(token):
    if not token.startswith(PREFIX):
        return token
    if not available():
        raise VaultError("This workspace was encrypted on Windows and cannot be read here.")
    try:
        return _call("unprotect", base64.b64decode(token[len(PREFIX):], validate=True)).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as e:
        raise VaultError(f"Corrupt secret: {e}")


def is_protected(value):
    return isinstance(value, str) and value.startswith(PREFIX)


# ---- walking the workspace ---------------------------------------------------------------------------------
# fn(value, kind): kind "secret" = always a secret, "text" = a script or body that may contain one, "url".
def _rows(rows, fn):
    for row in rows or []:
        if isinstance(row, dict) and row.get("type") != "file" and isinstance(row.get("value"), str) \
                and row["value"] and is_sensitive_name(row.get("key")) and not _is_reference(row["value"]):
            row["value"] = fn(row["value"], "secret")


def _walk_request(req, fn):
    if not isinstance(req, dict):
        return
    auth = req.get("auth")
    if isinstance(auth, dict):
        for k in AUTH_SECRET_KEYS:
            if isinstance(auth.get(k), str) and auth[k] and not _is_reference(auth[k]):
                auth[k] = fn(auth[k], "secret")
    proto = req.get("proto")
    if isinstance(proto, dict):
        for k in PROTO_SECRET_KEYS:
            if isinstance(proto.get(k), str) and proto[k] and not _is_reference(proto[k]):
                proto[k] = fn(proto[k], "secret")
    _rows(req.get("headers"), fn)
    _rows(req.get("params"), fn)
    body = req.get("body")
    if isinstance(body, dict):
        _rows(body.get("urlencoded"), fn)
        _rows(body.get("form"), fn)
        for k in ("raw", "variables", "query"):
            if isinstance(body.get(k), str) and body[k]:
                body[k] = fn(body[k], "text")
    scripts = req.get("scripts")
    if isinstance(scripts, dict):
        for k in ("pre", "test"):
            if isinstance(scripts.get(k), str) and scripts[k]:
                scripts[k] = fn(scripts[k], "text")
    if isinstance(req.get("url"), str) and req["url"]:
        req["url"] = fn(req["url"], "url")


def _walk(data, fn):
    for c in data.get("collections", []) or []:
        for r in c.get("items", []) or []:
            _walk_request(r, fn)
    for h in data.get("history", []) or []:
        _walk_request(h.get("request"), fn)
    settings = data.get("settings") or {}
    for t in settings.get("open_tabs", []) or []:
        _walk_request(t.get("req"), fn)
    proxy = settings.get("proxy")
    if isinstance(proxy, str) and (is_protected(proxy) or ("://" in proxy and "@" in proxy)):   # http://user:pass@host
        settings["proxy"] = fn(proxy, "secret")
    for env in list(data.get("environments", []) or []) + [data.get("globals") or {}]:
        for v in env.get("vars", []) or []:
            if is_secret_variable(v) or any(is_protected(v.get(k)) for k in ("initial", "current")):
                for k in ("initial", "current"):
                    if isinstance(v.get(k), str) and v[k] and not _is_reference(v[k]):
                        v[k] = fn(v[k], "secret")


def _protect_one(v, kind):
    if is_protected(v):
        return v
    if kind == "secret" or (kind == "text" and contains_secret_literal(v)) or (kind == "url" and url_has_secret(v)):
        return protect(v)
    return v


def protect_workspace(data):
    """A protected copy of the workspace dict, ready to be written to disk."""
    out = copy.deepcopy(data)
    _walk(out, _protect_one)
    return out


def reveal_workspace(data):
    """(plain workspace dict, number of secrets that could not be decrypted). Unreadable ones become empty."""
    failures = [0]

    def undo(v, kind):
        if not is_protected(v):
            return v
        try:
            return unprotect(v)
        except VaultError:
            failures[0] += 1
            return ""
    _walk(data, undo)
    return data, failures[0]


# ---- sharing: leave credentials out -------------------------------------------------------------------------
def _strip_one(v, kind):
    if kind == "secret":
        return ""
    if kind == "text":
        return redact_text(v)
    return redact_url(v)


def strip_request(req):
    """A copy of `req` without credentials ({{variable}} references are kept: they are not secrets)."""
    out = copy.deepcopy(req)
    _walk_request(out, _strip_one)
    return out


def strip_collection(coll):
    out = copy.deepcopy(coll)
    for r in out.get("items", []):
        _walk_request(r, _strip_one)
    return out


# ---- passphrase-protected backups ----------------------------------------------------------------------------
BACKUP_KIND = "encrypted-backup"
_HEADER_KEYS = ("zapi", "version", "kdf", "n", "r", "p", "salt", "nonce")
_MAX_PLAIN = 512 * 1024 * 1024


def _keys(passphrase, salt, n, r, p):
    if not passphrase:
        raise BackupError("A passphrase is required.")
    if not (2 ** 14 <= n <= 2 ** 17 and n & (n - 1) == 0 and 1 <= r <= 16 and 1 <= p <= 4):
        raise BackupError("This backup uses unsupported encryption settings.")
    material = hashlib.scrypt(passphrase.encode("utf-8"), salt=salt, n=n, r=r, p=p, maxmem=256 * 1024 * 1024, dklen=64)
    return material[:32], material[32:]


def _xor_stream(key, nonce, data):
    blocks = [hmac.new(key, nonce + i.to_bytes(8, "big"), hashlib.sha256).digest()
              for i in range((len(data) + 31) // 32)]
    stream = b"".join(blocks)[:len(data)]
    return (int.from_bytes(data, "big") ^ int.from_bytes(stream, "big")).to_bytes(len(data), "big") if data else b""


def _tag(mac_key, header, ciphertext):
    blob = json.dumps({k: header[k] for k in _HEADER_KEYS}, sort_keys=True).encode("utf-8")
    return hmac.new(mac_key, blob + ciphertext, hashlib.sha256).digest()


def encrypt_backup(obj, passphrase, n=2 ** 15):
    """A JSON-serialisable document holding `obj` encrypted under `passphrase`."""
    salt, nonce = os.urandom(16), os.urandom(16)
    enc_key, mac_key = _keys(passphrase, salt, n, 8, 1)
    plain = zlib.compress(json.dumps(obj, ensure_ascii=False).encode("utf-8"), 6)
    ciphertext = _xor_stream(enc_key, nonce, plain)
    doc = {"zapi": BACKUP_KIND, "version": 1, "kdf": "scrypt", "n": n, "r": 8, "p": 1,
           "salt": base64.b64encode(salt).decode(), "nonce": base64.b64encode(nonce).decode()}
    doc["mac"] = base64.b64encode(_tag(mac_key, doc, ciphertext)).decode()
    doc["data"] = base64.b64encode(ciphertext).decode()
    return doc


def decrypt_backup(doc, passphrase):
    """The original object. Raises BackupError for a wrong passphrase, a damaged file or a tampered one."""
    try:
        if doc.get("zapi") != BACKUP_KIND or doc.get("kdf") != "scrypt" or doc.get("version") != 1:
            raise BackupError("This is not a Zapi encrypted backup.")
        salt, nonce = base64.b64decode(doc["salt"], validate=True), base64.b64decode(doc["nonce"], validate=True)
        ciphertext, mac = base64.b64decode(doc["data"], validate=True), base64.b64decode(doc["mac"], validate=True)
        enc_key, mac_key = _keys(passphrase, salt, int(doc["n"]), int(doc["r"]), int(doc["p"]))
        header = {k: doc[k] for k in _HEADER_KEYS}
    except (KeyError, ValueError, TypeError, AttributeError):
        raise BackupError("The backup file is damaged.")
    if not hmac.compare_digest(mac, _tag(mac_key, header, ciphertext)):
        raise BackupError("Wrong passphrase, or the file was changed or damaged.")
    try:
        d = zlib.decompressobj()
        plain = d.decompress(_xor_stream(enc_key, nonce, ciphertext), _MAX_PLAIN)
        return json.loads(plain.decode("utf-8"))
    except (zlib.error, ValueError, UnicodeDecodeError):
        raise BackupError("The backup could not be unpacked.")
