"""Small developer utilities that sit next to an API client: JWT, Base64, URL encoding, hashes, UUID, timestamps."""
import base64
import binascii
import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import quote, unquote, unquote_plus


class ToolError(ValueError):
    pass


def _b64url_decode(part):
    try:
        return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))
    except (binascii.Error, ValueError):
        raise ToolError("Not valid Base64URL.") from None


def jwt_decode(token, secret=None, now=None):
    """-> {"header", "payload", "signature_ok": True/False/None, "alg", "expires": text, "expired": bool/None, ...}.
    The signature is only checked for HS256 / HS384 / HS512 and only when a secret is given."""
    t = token.strip()
    if t.lower().startswith("bearer "):
        t = t[7:].strip()
    parts = t.split(".")
    if len(parts) not in (2, 3) or not all(parts[:2]):
        raise ToolError("A JWT has three parts separated by dots (header.payload.signature).")
    try:
        header = json.loads(_b64url_decode(parts[0]))
        payload = json.loads(_b64url_decode(parts[1]))
    except (ValueError, UnicodeDecodeError):
        raise ToolError("The header or payload is not valid JSON.") from None
    out = {"header": header, "payload": payload, "alg": header.get("alg", "") if isinstance(header, dict) else "",
           "signature_ok": None, "times": {}, "expired": None}
    now = time.time() if now is None else now
    if isinstance(payload, dict):
        for claim, label in (("iat", "Issued at"), ("nbf", "Not before"), ("exp", "Expires")):
            v = payload.get(claim)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                out["times"][label] = datetime.fromtimestamp(v, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        exp = payload.get("exp")
        if isinstance(exp, (int, float)) and not isinstance(exp, bool):
            out["expired"] = exp < now
    algs = {"HS256": hashlib.sha256, "HS384": hashlib.sha384, "HS512": hashlib.sha512}
    if secret and len(parts) == 3 and out["alg"] in algs:
        mac = hmac.new(secret.encode("utf-8"), f"{parts[0]}.{parts[1]}".encode("ascii"), algs[out["alg"]]).digest()
        out["signature_ok"] = hmac.compare_digest(mac, _b64url_decode(parts[2]))
    return out


def jwt_summary(info):
    lines = ["HEADER", json.dumps(info["header"], indent=2), "", "PAYLOAD", json.dumps(info["payload"], indent=2)]
    if info["times"]:
        lines += [""] + [f"{k}: {v}" for k, v in info["times"].items()]
    if info["expired"] is not None:
        lines.append("Status: " + ("EXPIRED" if info["expired"] else "not expired"))
    if info["signature_ok"] is not None:
        lines.append("Signature: " + ("valid" if info["signature_ok"] else "INVALID for this secret"))
    elif info["alg"] and not info["alg"].startswith("HS"):
        lines.append(f"Signature: {info['alg']} is not verified here (needs the public key)")
    return "\n".join(lines)


def b64_encode(text, url_safe=False):
    raw = text.encode("utf-8")
    return (base64.urlsafe_b64encode(raw) if url_safe else base64.b64encode(raw)).decode("ascii")


def b64_decode(text):
    t = "".join(text.split())
    try:
        raw = base64.urlsafe_b64decode(t + "=" * (-len(t) % 4)) if ("-" in t or "_" in t) else \
            base64.b64decode(t + "=" * (-len(t) % 4), validate=True)
    except (binascii.Error, ValueError):
        raise ToolError("That is not valid Base64.") from None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return "(binary data) " + raw.hex()


def url_encode(text, full=False):
    return quote(text, safe="/:?&=#@!$'()*+,;~" if full else "")


def url_decode(text):
    return unquote_plus(text) if "+" in text and "%" not in text else unquote(text)


HASHES = ("MD5", "SHA-1", "SHA-256", "SHA-384", "SHA-512")


def hash_text(text, algorithm, key=""):
    name = algorithm.lower().replace("-", "")
    if name not in {h.lower().replace("-", "") for h in HASHES}:
        raise ToolError(f"Unknown algorithm {algorithm}.")
    data = text.encode("utf-8")
    if key:
        return hmac.new(key.encode("utf-8"), data, name).hexdigest()
    return hashlib.new(name, data).hexdigest()


def new_uuids(count=1):
    return [str(uuid.uuid4()) for _ in range(max(1, min(int(count), 100)))]


def time_info(text, now=None):
    """Accepts epoch seconds / milliseconds or an ISO date, returns the other forms. Empty text means 'now'."""
    t = text.strip()
    now = time.time() if now is None else now
    if not t:
        seconds = now
    else:
        try:
            n = float(t)
            seconds = n / 1000 if abs(n) >= 1e11 else n
        except ValueError:
            try:
                dt = datetime.fromisoformat(t.replace("Z", "+00:00"))
            except ValueError:
                raise ToolError("Enter epoch seconds, epoch milliseconds or a date like 2026-10-21T14:30:00Z.") from None
            if dt.tzinfo is None:
                dt = dt.astimezone()                               # no zone given: your local time
            seconds = dt.timestamp()
    try:
        utc = datetime.fromtimestamp(seconds, timezone.utc)
        local = utc.astimezone()
    except (OverflowError, OSError, ValueError):
        raise ToolError("That time is out of range.") from None
    return {"epoch_seconds": int(seconds), "epoch_milliseconds": int(seconds * 1000),
            "iso_utc": utc.strftime("%Y-%m-%dT%H:%M:%SZ"), "local": local.strftime("%Y-%m-%d %H:%M:%S %Z").strip(),
            "relative": _relative(seconds - now)}


def _relative(delta):
    n, future = abs(int(delta)), delta > 0
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if n >= size:
            v = n // size
            return f"in {v} {unit}{'s' if v != 1 else ''}" if future else f"{v} {unit}{'s' if v != 1 else ''} ago"
    return "now" if n < 2 else (f"in {n} seconds" if future else f"{n} seconds ago")
