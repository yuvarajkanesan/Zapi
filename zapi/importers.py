"""Import Postman collections / environments, OpenAPI 3 specs and Zapi's own exports."""
import json
import os

from . import curl, store


class ImportError_(ValueError):
    pass


def Workspace_fix(c):
    """Same normalisation a loaded collection gets (folders list covers every request's folder)."""
    return store.Workspace._fix_collection(c)


def load_file(path):
    with open(path, "rb") as f:
        raw = f.read()
    text = raw.decode("utf-8-sig", errors="replace")
    try:
        return json.loads(text)
    except ValueError:
        pass
    try:
        import yaml  # optional
        data = yaml.safe_load(text)
        if isinstance(data, dict):
            return data
    except ImportError:
        raise ImportError_("This file is not JSON. YAML specs need the PyYAML package; convert the spec to JSON "
                           "or run: pip install pyyaml")
    except Exception as e:  # noqa: BLE001
        raise ImportError_(f"Could not read the file: {e}")
    raise ImportError_("This is not a JSON or YAML file.")


def import_data(data, fallback_name="Imported"):
    """Returns ("collection" | "environment", object) for a parsed document."""
    if not isinstance(data, dict):
        raise ImportError_("Not a collection, environment or OpenAPI document.")
    kind = data.get("zapi")
    if kind == "encrypted-backup":
        return "encrypted", data
    if kind == "history":
        return "history", [h for h in data.get("entries", []) if isinstance(h, dict) and "request" in h]
    if kind == "workspace":
        return "workspace", data
    if kind == "mock":
        return "mock", data
    if kind == "collections":
        return "collections", [_zapi_collection(c) for c in data.get("collections", []) if isinstance(c, dict)]
    if kind == "environments":
        return "environments", [_zapi_environment(e) for e in data.get("environments", []) if isinstance(e, dict)]
    if kind == "collection":
        return "collection", _zapi_collection(data)
    if kind == "environment":
        return "environment", _zapi_environment(data)
    if isinstance(data.get("log"), dict) and isinstance(data["log"].get("entries"), list):
        return "collection", from_har(data, fallback_name if fallback_name != "Imported" else "HAR import")
    if "openapi" in data or "swagger" in data:
        return "collection", from_openapi(data)
    if isinstance(data.get("info"), dict) and "item" in data:
        return "collection", from_postman_collection(data)
    if "values" in data and isinstance(data["values"], list):
        return "environment", from_postman_environment(data)
    raise ImportError_("Unrecognised file. Zapi can import Postman collections and environments, "
                       "OpenAPI 3 specs, browser HAR files and Zapi exports.")


def import_file(path):
    return import_data(load_file(path), os.path.splitext(os.path.basename(path))[0])


# ---- Zapi -------------------------------------------------------------------
def _zapi_collection(d):
    c = store.new_collection(d.get("name") or "Imported")
    c["variables"] = d.get("variables", [])
    c["folders"] = list(d.get("folders", []))
    c["items"] = [store.normalize_request({**r, "id": store.new_id()}) for r in d.get("items", [])]
    return Workspace_fix(c)


def _zapi_environment(d):
    e = store.new_environment(d.get("name") or "Imported")
    e["vars"] = [store.new_var(v.get("key", ""), v.get("value", ""), secret=bool(v.get("secret")),
                               enabled=v.get("enabled", True)) for v in d.get("vars", []) if v.get("key")]
    return e


# ---- Postman ----------------------------------------------------------------
_SKIP_RESOURCES = {"image", "stylesheet", "font", "script", "media", "manifest", "texttrack", "ping", "prefetch"}
_SKIP_MIME = ("image/", "font/", "audio/", "video/", "text/css", "text/javascript", "application/javascript",
              "application/x-javascript", "application/font", "application/wasm")
_HAR_SKIP_HEADERS = {"host", "content-length", "connection", "accept-encoding", "upgrade-insecure-requests",
                     "sec-fetch-site", "sec-fetch-mode", "sec-fetch-dest", "sec-fetch-user", "sec-ch-ua",
                     "sec-ch-ua-mobile", "sec-ch-ua-platform", "priority", "pragma", "cache-control"}


def from_har(d, name="HAR import", only_api=True):
    """A browser HAR file (DevTools: Network -> Save all as HAR) becomes a collection. By default only API-like calls are
    kept (XHR / fetch and JSON / XML / text responses); pictures, fonts, scripts and stylesheets are left out."""
    from urllib.parse import urlsplit
    log = d["log"]
    c = store.new_collection(((log.get("pages") or [{}])[0].get("title") or "").strip() or name)
    seen = set()
    for e in log["entries"]:
        if not isinstance(e, dict) or not isinstance(e.get("request"), dict):
            continue
        r = e["request"]
        url = r.get("url") or ""
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https"):
            continue
        method = (r.get("method") or "GET").upper()
        mime = ((e.get("response") or {}).get("content") or {}).get("mimeType", "").lower()
        if only_api:
            if (e.get("_resourceType") or "").lower() in _SKIP_RESOURCES or mime.startswith(_SKIP_MIME):
                continue
            if method == "OPTIONS":
                continue
        base = url.split("#")[0].partition("?")[0]
        req = store.new_request(f"{method} {parts.path or '/'}", method, base)
        for q in r.get("queryString") or []:
            if q.get("name"):
                req["params"].append(store.kv(q["name"], str(q.get("value", ""))))
        if not req["params"] and parts.query:
            for pair in parts.query.split("&"):
                k, _, v = pair.partition("=")
                if k:
                    req["params"].append(store.kv(k, v))
        for h in r.get("headers") or []:
            n = str(h.get("name", ""))
            if n and not n.startswith(":") and n.lower() not in _HAR_SKIP_HEADERS:
                req["headers"].append(store.kv(n, str(h.get("value", ""))))
        post = r.get("postData") or {}
        ctype = (post.get("mimeType") or "").lower()
        if post.get("text") or post.get("params"):
            if "x-www-form-urlencoded" in ctype:
                rows = post.get("params") or [dict(zip(("name", "value"), pair.partition("=")[::2]))
                                              for pair in (post.get("text") or "").split("&") if pair]
                req["body"].update({"mode": "urlencoded", "urlencoded": [
                    store.kv(x.get("name", ""), str(x.get("value", ""))) for x in rows if x.get("name")]})
            elif "multipart/form-data" in ctype and post.get("params"):
                req["body"].update({"mode": "form-data", "form": [
                    dict(store.kv(x.get("name", ""), str(x.get("value", ""))), type="file" if x.get("fileName") else "text")
                    for x in post["params"] if x.get("name")]})
                req["headers"] = [h for h in req["headers"] if h["key"].lower() != "content-type"]
            else:
                kind = "JSON" if "json" in ctype else "XML" if "xml" in ctype else "HTML" if "html" in ctype else "Text"
                req["body"].update({"mode": "raw", "raw_type": kind, "raw": post.get("text") or ""})
        key = (method, base, tuple((p["key"], p["value"]) for p in req["params"]), req["body"].get("raw", ""))
        if key in seen:
            continue
        seen.add(key)
        c["items"].append(req)
    if not c["items"]:
        raise ImportError_("That HAR file has no API requests in it (pictures, fonts, scripts and stylesheets are skipped).")
    return Workspace_fix(c)


def from_postman_environment(d):
    e = store.new_environment(d.get("name") or "Imported environment")
    for v in d.get("values", []):
        if v.get("key"):
            e["vars"].append(store.new_var(v["key"], str(v.get("value", "")),
                                           secret=v.get("type") == "secret", enabled=v.get("enabled", True)))
    return e


def from_postman_collection(d):
    c = store.new_collection(d.get("info", {}).get("name") or "Imported collection")
    for v in d.get("variable", []) or []:
        if v.get("key"):
            c["variables"].append(store.kv(v["key"], str(v.get("value", ""))))
    top_auth = d.get("auth")
    _walk(d.get("item", []), [], c, top_auth)
    return Workspace_fix(c)


def _walk(items, path, coll, inherited_auth):
    for it in items:
        if "item" in it:
            sub_path = path + [(it.get("name") or "Folder").replace("/", "-")]
            coll["folders"].append("/".join(sub_path))
            _walk(it["item"], sub_path, coll, it.get("auth") or inherited_auth)
        elif "request" in it:
            req = _postman_request(it.get("name", "Request"), it["request"], it.get("auth") or inherited_auth)
            req["folder"] = "/".join(path)
            _postman_events(req, it.get("event"))
            for ex in it.get("response") or []:
                req["examples"].append({"name": ex.get("name", ""), "status": ex.get("code", 200),
                                        "headers": [[h.get("key", ""), str(h.get("value", ""))]
                                                    for h in ex.get("header") or [] if isinstance(h, dict)],
                                        "body": ex.get("body") or ""})
            coll["items"].append(req)


def _postman_events(req, events):
    """Postman keeps scripts as event lists; the `exec` lines are the JavaScript source."""
    for ev in events or []:
        script = ev.get("script") or {}
        exec_ = script.get("exec")
        code = "\n".join(exec_) if isinstance(exec_, list) else (exec_ or "")
        if ev.get("listen") == "prerequest":
            req["scripts"]["pre"] = code
        elif ev.get("listen") == "test":
            req["scripts"]["test"] = code


def _postman_url(u):
    if isinstance(u, str):
        return u
    if isinstance(u, dict):
        return u.get("raw") or ""
    return ""


def _postman_request(name, r, auth):
    if isinstance(r, str):
        return store.normalize_request({"name": name, "url": r})
    req = store.new_request(name, (r.get("method") or "GET").upper(), _postman_url(r.get("url")))
    for h in r.get("header", []) or []:
        if h.get("key"):
            req["headers"].append(store.kv(h["key"], str(h.get("value", "")), not h.get("disabled", False)))
    # query lives in the Params table
    url = req["url"]
    if "?" in url:
        base, _, q = url.partition("?")
        req["url"] = base
        for pair in q.split("&"):
            if pair:
                k, _, v = pair.partition("=")
                req["params"].append(store.kv(k, v))
        if isinstance(r.get("url"), dict):
            disabled = {p.get("key") for p in r["url"].get("query", []) or [] if p.get("disabled")}
            for p in req["params"]:
                if p["key"] in disabled:
                    p["enabled"] = False
    a = r.get("auth") or auth
    if a:
        t = a.get("type")

        def field(name):
            for e in a.get(t, []) or []:
                if e.get("key") == name:
                    return str(e.get("value", ""))
            return ""
        if t == "bearer":
            req["auth"].update({"type": "bearer", "token": field("token")})
        elif t == "basic":
            req["auth"].update({"type": "basic", "username": field("username"), "password": field("password")})
        elif t == "apikey":
            req["auth"].update({"type": "apikey", "key": field("key"), "value": field("value"),
                                "in": field("in") or "header"})
        elif t == "digest":
            req["auth"].update({"type": "digest", "username": field("username"), "password": field("password")})
        elif t == "oauth1":
            req["auth"].update({"type": "oauth1", "consumer_key": field("consumerKey"),
                                "consumer_secret": field("consumerSecret"), "token": field("token"),
                                "token_secret": field("tokenSecret"),
                                "sig_method": field("signatureMethod") or "HMAC-SHA1", "realm": field("realm")})
        elif t == "oauth2":
            grant = {"client_credentials": "Client credentials", "password_credentials": "Password",
                     "authorization_code": "Authorization code (PKCE)", "authorization_code_with_pkce":
                     "Authorization code (PKCE)"}.get(field("grant_type"), "Client credentials")
            req["auth"].update({"type": "oauth2", "token": field("accessToken"), "o2_grant": field("grant_type")
                                if field("grant_type") in ("Client credentials", "Password", "Authorization code (PKCE)",
                                                           "Refresh token") else grant,
                                "o2_token_url": field("accessTokenUrl"), "o2_auth_url": field("authUrl"),
                                "o2_client_id": field("clientId"), "o2_client_secret": field("clientSecret"),
                                "o2_scope": field("scope"), "o2_prefix": field("headerPrefix") or "Bearer",
                                "o2_refresh_token": field("refreshToken")})
        elif t == "awsv4":
            req["auth"].update({"type": "aws", "access_key": field("accessKey"), "secret_key": field("secretKey"),
                                "session_token": field("sessionToken"), "region": field("region") or "us-east-1",
                                "service": field("service")})
        elif t == "hawk":
            req["auth"].update({"type": "hawk", "hawk_id": field("authId"), "hawk_key": field("authKey"),
                                "hawk_algorithm": field("algorithm") or "sha256", "hawk_ext": field("ext")})
        elif t == "ntlm":
            req["auth"].update({"type": "ntlm", "username": field("username"), "password": field("password"),
                                "domain": field("domain"), "workstation": field("workstation")})
    b = r.get("body") or {}
    mode = b.get("mode")
    if mode == "raw":
        lang = ((b.get("options") or {}).get("raw") or {}).get("language", "text")
        req["body"].update({"mode": "raw", "raw": b.get("raw", ""),
                            "raw_type": {"json": "JSON", "xml": "XML", "html": "HTML",
                                         "javascript": "JavaScript"}.get(lang, "Text")})
    elif mode == "urlencoded":
        req["body"]["mode"] = "x-www-form-urlencoded"
        req["body"]["urlencoded"] = [store.kv(p.get("key", ""), str(p.get("value", "")), not p.get("disabled", False))
                                     for p in b.get("urlencoded", [])]
    elif mode == "formdata":
        req["body"]["mode"] = "form-data"
        req["body"]["form"] = [store.kv(p.get("key", ""), str(p.get("value", p.get("src", "")) or ""),
                                        not p.get("disabled", False), type=p.get("type", "text"))
                               for p in b.get("formdata", [])]
    elif mode == "graphql":
        g = b.get("graphql") or {}
        req["body"].update({"mode": "GraphQL", "query": g.get("query", ""), "variables": g.get("variables", "")})
    elif mode == "file":
        req["body"].update({"mode": "binary", "file": (b.get("file") or {}).get("src", "")})
    return req


# ---- OpenAPI ----------------------------------------------------------------
_HTTP = ("get", "post", "put", "patch", "delete", "head", "options")


def from_openapi(d):
    info = d.get("info") or {}
    c = store.new_collection(info.get("title") or "OpenAPI")
    servers = d.get("servers") or []
    base = servers[0].get("url", "") if servers else ""
    if not base and d.get("host"):   # swagger 2
        base = f"{(d.get('schemes') or ['https'])[0]}://{d['host']}{d.get('basePath', '')}"
    c["variables"].append(store.kv("base_url", base.rstrip("/")))
    for path, ops in (d.get("paths") or {}).items():
        if not isinstance(ops, dict):
            continue
        shared = ops.get("parameters", [])
        for method in _HTTP:
            op = ops.get(method)
            if not isinstance(op, dict):
                continue
            name = op.get("summary") or op.get("operationId") or f"{method.upper()} {path}"
            req = store.new_request(name, method.upper(), "{{base_url}}" + path)
            if op.get("tags"):
                req["folder"] = store.clean_folder(str(op["tags"][0]).replace("/", "-"))
            for p in _resolve_params(d, shared + op.get("parameters", [])):
                if p.get("in") == "query":
                    req["params"].append(store.kv(p.get("name", ""), _example(p), p.get("required", False)))
                elif p.get("in") == "header":
                    req["headers"].append(store.kv(p.get("name", ""), _example(p), p.get("required", False)))
            content = ((op.get("requestBody") or {}).get("content")) or {}
            if "application/json" in content:
                sample = _sample(d, content["application/json"].get("schema") or {})
                req["body"].update({"mode": "raw", "raw_type": "JSON", "raw": json.dumps(sample, indent=2)})
            c["items"].append(req)
    return Workspace_fix(c)


def _resolve_params(doc, params):
    out = []
    for p in params:
        if "$ref" in p:
            p = _ref(doc, p["$ref"]) or {}
        out.append(p)
    return out


def _ref(doc, ref):
    if not ref.startswith("#/"):
        return None
    node = doc
    for part in ref[2:].split("/"):
        node = node.get(part.replace("~1", "/").replace("~0", "~")) if isinstance(node, dict) else None
    return node


def _example(p):
    if "example" in p:
        return str(p["example"])
    schema = p.get("schema") or {}
    return str(schema.get("example", schema.get("default", "")))


def _sample(doc, schema, depth=0):
    if depth > 6 or not isinstance(schema, dict):
        return None
    if "$ref" in schema:
        return _sample(doc, _ref(doc, schema["$ref"]) or {}, depth + 1)
    if "example" in schema:
        return schema["example"]
    if "default" in schema:
        return schema["default"]
    if schema.get("enum"):
        return schema["enum"][0]
    t = schema.get("type")
    if t == "object" or "properties" in schema:
        return {k: _sample(doc, v, depth + 1) for k, v in (schema.get("properties") or {}).items()}
    if t == "array":
        return [_sample(doc, schema.get("items") or {}, depth + 1)]
    return {"integer": 0, "number": 0, "boolean": False, "string": "string"}.get(t)
