"""Export to Postman collection v2.1 (round-trips through importers.from_postman_collection)."""
import urllib.parse

SCHEMA = "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
_LANG = {"JSON": "json", "XML": "xml", "HTML": "html", "JavaScript": "javascript", "Text": "text",
         "SOAP 1.1": "xml", "SOAP 1.2": "xml"}


def _kv(rows, key="key", extra=None):
    out = []
    for r in rows:
        d = {"key": r.get("key", ""), "value": r.get("value", "")}
        if not r.get("enabled", True):
            d["disabled"] = True
        if extra:
            d.update(extra(r))
        out.append(d)
    return out


def _auth(a):
    t = a.get("type", "none")

    def fields(*pairs):
        return [{"key": k, "value": a.get(src, ""), "type": "string"} for k, src in pairs]
    if t == "bearer":
        return {"type": "bearer", "bearer": fields(("token", "token"))}
    if t == "basic":
        return {"type": "basic", "basic": fields(("username", "username"), ("password", "password"))}
    if t == "digest":
        return {"type": "digest", "digest": fields(("username", "username"), ("password", "password"))}
    if t == "apikey":
        return {"type": "apikey", "apikey": fields(("key", "key"), ("value", "value"), ("in", "in"))}
    if t == "oauth1":
        return {"type": "oauth1", "oauth1": fields(("consumerKey", "consumer_key"), ("consumerSecret", "consumer_secret"),
                                                   ("token", "token"), ("tokenSecret", "token_secret"),
                                                   ("signatureMethod", "sig_method"), ("realm", "realm"))}
    if t == "oauth2":
        return {"type": "oauth2", "oauth2": fields(
            ("accessToken", "token"), ("grant_type", "o2_grant"), ("accessTokenUrl", "o2_token_url"),
            ("authUrl", "o2_auth_url"), ("clientId", "o2_client_id"), ("clientSecret", "o2_client_secret"),
            ("scope", "o2_scope"), ("headerPrefix", "o2_prefix"), ("refreshToken", "o2_refresh_token"))}
    if t == "aws":
        return {"type": "awsv4", "awsv4": fields(("accessKey", "access_key"), ("secretKey", "secret_key"),
                                                 ("sessionToken", "session_token"), ("region", "region"),
                                                 ("service", "service"))}
    if t == "hawk":
        return {"type": "hawk", "hawk": fields(("authId", "hawk_id"), ("authKey", "hawk_key"),
                                               ("algorithm", "hawk_algorithm"), ("ext", "hawk_ext"))}
    if t == "ntlm":
        return {"type": "ntlm", "ntlm": fields(("username", "username"), ("password", "password"),
                                               ("domain", "domain"), ("workstation", "workstation"))}
    return None


def _body(b):
    mode = b.get("mode", "none")
    if mode == "raw":
        return {"mode": "raw", "raw": b.get("raw", ""),
                "options": {"raw": {"language": _LANG.get(b.get("raw_type", "Text"), "text")}}}
    if mode == "x-www-form-urlencoded":
        return {"mode": "urlencoded", "urlencoded": _kv(b.get("urlencoded", []))}
    if mode == "form-data":
        return {"mode": "formdata", "formdata": _kv(b.get("form", []), extra=lambda r: {
            "type": r.get("type", "text")})}
    if mode == "GraphQL":
        return {"mode": "graphql", "graphql": {"query": b.get("query", ""), "variables": b.get("variables", "")}}
    if mode == "binary":
        return {"mode": "file", "file": {"src": b.get("file", "")}}
    return None


def _events(scripts):
    out = []
    for listen, key in (("prerequest", "pre"), ("test", "test")):
        code = (scripts or {}).get(key, "")
        if code.strip():
            out.append({"listen": listen, "script": {"type": "text/javascript", "exec": code.split("\n")}})
    return out


def _request(r):
    params = r.get("params", [])
    raw = r["url"]
    if params:
        pairs = urllib.parse.urlencode([(p["key"], p.get("value", "")) for p in params], quote_via=urllib.parse.quote)
        raw += ("&" if "?" in raw else "?") + pairs
    req = {"method": r["method"], "header": _kv(r.get("headers", [])),
           "url": {"raw": raw, "query": _kv(params)}}
    body = _body(r.get("body", {}))
    if body:
        req["body"] = body
    auth = _auth(r.get("auth", {}))
    if auth:
        req["auth"] = auth
    item = {"name": r["name"], "request": req}
    events = _events(r.get("scripts"))
    if events:
        item["event"] = events
    examples = [{"name": ex.get("name", ""), "originalRequest": req, "code": ex.get("status", 200),
                 "status": ex.get("name", ""), "header": [{"key": k, "value": v} for k, v in ex.get("headers", [])],
                 "body": ex.get("body", "")} for ex in r.get("examples", [])]
    if examples:
        item["response"] = examples
    return item


def to_postman(coll):
    """A Postman v2.1 collection. Protocol requests Postman has no equivalent for (WebSocket, MQTT...) are skipped;
    the second value is how many were left out."""
    root = []
    folders = {}
    for path in sorted(coll.get("folders", [])):
        node = {"name": path.split("/")[-1], "item": []}
        parent = folders.get("/".join(path.split("/")[:-1]))
        (parent["item"] if parent else root).append(node)
        folders[path] = node
    skipped = 0
    for r in coll["items"]:
        if r.get("kind", "http") not in ("http", "soap"):
            skipped += 1
            continue
        (folders[r["folder"]]["item"] if r.get("folder") in folders else root).append(_request(r))
    doc = {"info": {"_postman_id": coll.get("id", ""), "name": coll["name"], "schema": SCHEMA},
           "item": root, "variable": [{"key": v["key"], "value": v.get("value", "")}
                                      for v in coll.get("variables", []) if v.get("key")]}
    return doc, skipped
