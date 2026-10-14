"""The workspace: collections, environments, globals, history and settings, saved as one JSON file."""
import copy
import os
import time
import uuid

from . import config, vault

METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
HISTORY_LIMIT = 200
BODY_MODES = ["none", "form-data", "x-www-form-urlencoded", "raw", "binary", "GraphQL"]
RAW_TYPES = ["JSON", "Text", "XML", "HTML", "JavaScript", "SOAP 1.1", "SOAP 1.2"]


AUTH_DEFAULTS = {
    "type": "none", "token": "", "username": "", "password": "", "key": "", "value": "", "in": "header",
    "domain": "", "workstation": "",
    "consumer_key": "", "consumer_secret": "", "token_secret": "", "sig_method": "HMAC-SHA1", "realm": "",
    "o2_grant": "Client credentials", "o2_token_url": "", "o2_auth_url": "", "o2_client_id": "",
    "o2_client_secret": "", "o2_scope": "", "o2_refresh_token": "", "o2_prefix": "Bearer",
    "o2_client_auth": "Basic header",
    "access_key": "", "secret_key": "", "session_token": "", "region": "us-east-1", "service": "",
    "hawk_id": "", "hawk_key": "", "hawk_algorithm": "sha256", "hawk_ext": "",
}


def new_id():
    return uuid.uuid4().hex[:12]


def kv(key="", value="", enabled=True, **extra):
    d = {"key": key, "value": value, "enabled": enabled}
    d.update(extra)
    return d


def new_request(name="Untitled request", method="GET", url=""):
    return {
        "id": new_id(), "name": name, "method": method, "url": url, "folder": "", "kind": "http",
        "params": [], "headers": [], "scripts": {"pre": "", "test": ""}, "proto": {}, "examples": [],
        "auth": dict(AUTH_DEFAULTS),
        "body": {"mode": "none", "raw_type": "JSON", "raw": "", "form": [], "urlencoded": [],
                 "file": "", "query": "", "variables": ""},
    }


KIND_LABELS = {"http": "", "soap": "SOAP", "websocket": "WS", "sse": "SSE", "mqtt": "MQTT", "grpc": "gRPC"}
SOAP_TEMPLATE = """<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <!-- the operation and its parameters go here -->
  </soap:Body>
</soap:Envelope>"""


def badge(req):
    """What the sidebar and tabs show in front of a request: the HTTP method, or the protocol."""
    return KIND_LABELS.get(req.get("kind", "http")) or req.get("method", "GET")


def new_kind_request(kind):
    """A starter request for each protocol Zapi can speak."""
    if kind == "soap":
        r = new_request("New SOAP request", "POST")
        r["kind"] = "soap"
        r["body"].update({"mode": "raw", "raw_type": "SOAP 1.1", "raw": SOAP_TEMPLATE})
        r["headers"] = [kv("SOAPAction", "")]
        return r
    r = new_request({"websocket": "New WebSocket", "sse": "New SSE stream", "mqtt": "New MQTT client",
                     "grpc": "New gRPC call"}.get(kind, "Untitled request"))
    r["kind"] = kind
    if kind == "websocket":
        r["proto"] = {"subprotocols": "", "message": "", "send_as": "Text"}
    elif kind == "sse":
        r["proto"] = {}
    elif kind == "mqtt":
        r["proto"] = {"host": "localhost", "port": "1883", "tls": False, "client_id": "", "username": "",
                      "password": "", "keepalive": "30", "subscribe": "#", "sub_qos": 0, "pub_topic": "",
                      "pub_qos": 0, "retain": False, "payload": ""}
    elif kind == "grpc":
        r["proto"] = {"target": "localhost:50051", "tls": False, "service": "", "method": "", "message": "{}"}
    return r


def new_collection(name="New collection"):
    return {"id": new_id(), "name": name, "items": [], "variables": [], "folders": []}


def new_environment(name="New environment"):
    return {"id": new_id(), "name": name, "vars": []}


def new_var(key="", initial="", current=None, secret=False, enabled=True):
    return {"key": key, "initial": initial, "current": initial if current is None else current,
            "secret": secret, "enabled": enabled}


def clean_folder(path):
    return "/".join(p.strip() for p in (path or "").split("/") if p.strip())


def folder_ancestors(path):
    parts = clean_folder(path).split("/") if path else []
    return ["/".join(parts[:i + 1]) for i in range(len(parts)) if parts[i]]


def normalize_request(req):
    """Fill in anything an older / imported request is missing so the UI can rely on the shape."""
    out = new_request()
    out.update({k: v for k, v in req.items() if k not in ("auth", "body")})
    out["auth"].update(req.get("auth") or {})
    out["body"].update(req.get("body") or {})
    out["scripts"] = {**{"pre": "", "test": ""}, **(req.get("scripts") or {})}
    out["proto"] = dict(req.get("proto") or {})
    out["examples"] = list(req.get("examples") or [])
    out["folder"] = clean_folder(req.get("folder", ""))
    out["id"] = req.get("id") or new_id()
    return out


class Workspace:
    def __init__(self, path=None):
        self.path = path or os.path.join(config.data_dir(), "workspace.json")
        self.collections = []
        self.environments = []
        self.globals = new_environment("Globals")
        self.history = []
        self.settings = {"active_env": "", "workspace_name": "My Workspace", "verify_ssl": True,
                         "follow_redirects": True, "timeout": 30, "open_tabs": [], "active_tab": 0,
                         "encrypt_secrets": True}
        self.secret_failures = 0              # secrets in the file that this Windows account could not decrypt
        self.load()

    # ---- persistence -------------------------------------------------------
    def load(self):
        data = config.load_json(self.path, {})
        data, self.secret_failures = vault.reveal_workspace(data)      # no-op for a plain-text (older) file
        self.collections = [self._fix_collection(c) for c in data.get("collections", [])]
        self.environments = data.get("environments", [])
        self.globals = data.get("globals") or new_environment("Globals")
        self.history = data.get("history", [])
        self.settings.update(data.get("settings", {}))

    def save(self):
        payload = {
            "version": 1, "collections": self.collections, "environments": self.environments,
            "globals": self.globals, "history": self.history[:HISTORY_LIMIT], "settings": self.settings,
        }
        if self.settings.get("encrypt_secrets", True) and vault.available():
            payload = vault.protect_workspace(payload)       # secrets are encrypted on disk, plain in memory
        config.save_json(self.path, payload)

    def secrets_encrypted(self):
        return bool(self.settings.get("encrypt_secrets", True)) and vault.available()

    @staticmethod
    def _fix_collection(c):
        c.setdefault("id", new_id())
        c.setdefault("variables", [])
        c["items"] = [normalize_request(r) for r in c.get("items", [])]
        folders = [clean_folder(f) for f in c.get("folders", [])]
        for r in c["items"]:
            folders += folder_ancestors(r["folder"])
        c["folders"] = sorted({f for f in folders if f})
        return c

    # ---- collections -------------------------------------------------------
    def add_collection(self, name="New collection"):
        c = new_collection(name)
        self.collections.append(c)
        return c

    def collection(self, cid):
        return next((c for c in self.collections if c["id"] == cid), None)

    def find_request(self, rid):
        """Returns (collection, request) or (None, None)."""
        for c in self.collections:
            for r in c["items"]:
                if r["id"] == rid:
                    return c, r
        return None, None

    def save_request(self, req, collection_id):
        """Insert or replace `req` (a deep copy is stored) in the given collection."""
        c = self.collection(collection_id)
        if c is None:
            raise KeyError(collection_id)
        old_c, _ = self.find_request(req["id"])
        if old_c is not None and old_c["id"] != collection_id:
            old_c["items"] = [r for r in old_c["items"] if r["id"] != req["id"]]
        stored = copy.deepcopy(req)
        for i, r in enumerate(c["items"]):
            if r["id"] == req["id"]:
                c["items"][i] = stored
                break
        else:
            c["items"].append(stored)
        return stored

    def delete_request(self, rid):
        for c in self.collections:
            c["items"] = [r for r in c["items"] if r["id"] != rid]

    # ---- folders (a folder is a "/"-separated path stored on each request) ----
    def add_folder(self, cid, path):
        c = self.collection(cid)
        path = clean_folder(path)
        if c is None or not path:
            return None
        for f in folder_ancestors(path):
            if f not in c["folders"]:
                c["folders"].append(f)
        c["folders"].sort()
        return path

    def rename_folder(self, cid, old, new):
        c = self.collection(cid)
        old, new = clean_folder(old), clean_folder(new)
        if c is None or not old or not new:
            return False

        def move(p):
            return new + p[len(old):] if p == old or p.startswith(old + "/") else p
        c["folders"] = sorted({move(f) for f in c["folders"]})
        for r in c["items"]:
            r["folder"] = move(r["folder"])
        for f in folder_ancestors(new):
            if f not in c["folders"]:
                c["folders"].append(f)
        c["folders"].sort()
        return True

    def delete_folder(self, cid, path):
        """Removes the folder, its subfolders and every request inside them."""
        c = self.collection(cid)
        path = clean_folder(path)
        if c is None or not path:
            return 0
        inside = lambda p: p == path or p.startswith(path + "/")   # noqa: E731
        before = len(c["items"])
        c["items"] = [r for r in c["items"] if not inside(r["folder"])]
        c["folders"] = [f for f in c["folders"] if not inside(f)]
        return before - len(c["items"])

    def move_request(self, rid, folder):
        c, r = self.find_request(rid)
        if r is None:
            return False
        r["folder"] = clean_folder(folder)
        self.add_folder(c["id"], r["folder"])
        return True

    def delete_collection(self, cid):
        self.collections = [c for c in self.collections if c["id"] != cid]

    def duplicate_request(self, rid):
        c, r = self.find_request(rid)
        if r is None:
            return None
        dup = copy.deepcopy(r)
        dup["id"] = new_id()
        dup["name"] = r["name"] + " copy"
        c["items"].insert(c["items"].index(r) + 1, dup)
        return dup

    # ---- environments ------------------------------------------------------
    def environment(self, eid):
        return next((e for e in self.environments if e["id"] == eid), None)

    def active_environment(self):
        return self.environment(self.settings.get("active_env", ""))

    def add_environment(self, name="New environment"):
        e = new_environment(name)
        self.environments.append(e)
        return e

    def duplicate_environment(self, eid):
        e = self.environment(eid)
        dup = copy.deepcopy(e)
        dup["id"] = new_id()
        dup["name"] = e["name"] + " copy"
        self.environments.append(dup)
        return dup

    def delete_environment(self, eid):
        self.environments = [e for e in self.environments if e["id"] != eid]
        if self.settings.get("active_env") == eid:
            self.settings["active_env"] = ""

    @staticmethod
    def _scope(env):
        return {v["key"]: v.get("current", "") for v in (env or {}).get("vars", [])
                if v.get("enabled", True) and v.get("key")}

    def variable_values(self, collection_id=None):
        """Globals < collection variables < active environment (current values)."""
        coll = self.collection(collection_id) if collection_id else None
        coll_scope = {v["key"]: v.get("value", "") for v in (coll or {}).get("variables", [])
                      if v.get("enabled", True) and v.get("key")}
        return {**self._scope(self.globals), **coll_scope, **self._scope(self.active_environment())}

    def request_options(self):
        """Network settings handed to http_client.send by the UI and the runner."""
        s = self.settings
        cert = (s.get("client_cert", ""), s.get("client_key", ""))
        return {"verify_ssl": s.get("verify_ssl", True), "follow_redirects": s.get("follow_redirects", True),
                "timeout": s.get("timeout", 30), "proxy": s.get("proxy", ""),
                "client_cert": cert if cert[0] else None, "ca_file": s.get("ca_file", "")}

    def scopes(self, collection_id=None):
        """The three writable variable scopes as plain dicts (what scripts read and change)."""
        coll = self.collection(collection_id) if collection_id else None
        return {"environment": self._scope(self.active_environment()), "globals": self._scope(self.globals),
                "collection": {v["key"]: v.get("value", "") for v in (coll or {}).get("variables", [])
                               if v.get("enabled", True) and v.get("key")}}

    def apply_scopes(self, collection_id, before, after):
        """Write back what a script changed (sets and unsets) to Current values / collection variables."""
        env = self.active_environment()
        for name, target in (("environment", env if env is not None else self.globals),
                             ("globals", self.globals)):
            old, new = before.get(name, {}), after.get(name, {})
            for k, v in new.items():
                if old.get(k) != v:
                    row = next((x for x in target["vars"] if x["key"] == k), None)
                    if row is None:
                        target["vars"].append(new_var(k, "", v))
                    else:
                        row["current"] = v
            for k in set(old) - set(new):
                target["vars"] = [x for x in target["vars"] if x["key"] != k]
        coll = self.collection(collection_id) if collection_id else None
        if coll is not None:
            old, new = before.get("collection", {}), after.get("collection", {})
            for k, v in new.items():
                if old.get(k) != v:
                    row = next((x for x in coll["variables"] if x["key"] == k), None)
                    if row is None:
                        coll["variables"].append(kv(k, v))
                    else:
                        row["value"] = v
            for k in set(old) - set(new):
                coll["variables"] = [x for x in coll["variables"] if x["key"] != k]

    # ---- history -----------------------------------------------------------
    def add_history(self, req, status, elapsed_ms, size):
        entry = {"id": new_id(), "at": time.time(), "status": status, "ms": elapsed_ms, "size": size,
                 "request": copy.deepcopy(req)}
        self.history.insert(0, entry)
        del self.history[HISTORY_LIMIT:]
        return entry

    def clear_history(self):
        self.history = []

    # ---- export ------------------------------------------------------------
    def export_environment(self, env):
        """Initial values only, as the Variables screen promises; secrets are exported empty."""
        return {"zapi": "environment", "name": env["name"], "vars": [
            {"key": v["key"], "value": "" if vault.is_secret_variable(v) else v.get("initial", ""),
             "secret": vault.is_secret_variable(v), "enabled": v.get("enabled", True)} for v in env["vars"]]}

    def export_all_collections(self, include_secrets=False):
        return {"zapi": "collections",
                "collections": [self.export_collection(c, include_secrets) for c in self.collections]}

    def export_all_environments(self):
        """Initial values only (what sharing an environment means); secrets are left out."""
        return {"zapi": "environments", "environments": [self.export_environment(e) for e in self.environments]}

    def export_history(self, include_secrets=False):
        """For sharing: the requests keep their shape but lose credentials, unless asked for."""
        entries = copy.deepcopy(self.history)
        if not include_secrets:
            for e in entries:
                e["request"] = vault.strip_request(e["request"])
        return {"zapi": "history", "entries": entries}

    def import_history(self, entries):
        """Merge entries (newest first, capped). Returns how many were new."""
        have = {h["id"] for h in self.history}
        added = 0
        for e in entries:
            if e.get("id") in have:
                continue
            e = {"id": e.get("id") or new_id(), "at": float(e.get("at") or time.time()),
                 "status": int(e.get("status") or 0), "ms": e.get("ms", 0), "size": e.get("size", 0),
                 "request": normalize_request(e["request"])}
            self.history.append(e)
            have.add(e["id"])
            added += 1
        self.history.sort(key=lambda h: h["at"], reverse=True)
        del self.history[HISTORY_LIMIT:]
        return added

    def export_workspace(self):
        """A complete backup. Unlike the sharing exports it keeps Current values and secrets: keep the file private."""
        return {"zapi": "workspace", "version": 1, "name": self.settings.get("workspace_name", ""),
                "collections": copy.deepcopy(self.collections), "environments": copy.deepcopy(self.environments),
                "globals": copy.deepcopy(self.globals), "history": copy.deepcopy(self.history),
                "mock": copy.deepcopy(self.settings.get("mock", {})),
                "settings": {k: self.settings[k] for k in ("verify_ssl", "follow_redirects", "timeout")
                             if k in self.settings}}

    def import_workspace(self, data, replace=False):
        """Restore a backup. replace=True swaps everything; otherwise items with a new id are added and items
        with the same id are overwritten by the backup. Returns a short summary."""
        cols = [self._fix_collection(copy.deepcopy(c)) for c in data.get("collections", [])]
        envs = copy.deepcopy(data.get("environments", []))
        if replace:
            self.collections, self.environments = cols, envs
            self.history = []
            self.globals = copy.deepcopy(data.get("globals")) or new_environment("Globals")
        else:
            for c in cols:
                self.collections = [x for x in self.collections if x["id"] != c["id"]] + [c]
            for e in envs:
                self.environments = [x for x in self.environments if x["id"] != e["id"]] + [e]
            g = data.get("globals") or {}
            have = {v["key"] for v in self.globals["vars"]}
            self.globals["vars"] += [v for v in g.get("vars", []) if v.get("key") not in have]
        self.import_history(data.get("history", []))
        if data.get("mock"):
            self.settings["mock"] = copy.deepcopy(data["mock"])
        self.settings.update(data.get("settings", {}))
        if self.settings.get("active_env") and not self.environment(self.settings["active_env"]):
            self.settings["active_env"] = ""
        return f"{len(cols)} collection(s), {len(envs)} environment(s), {len(data.get('history', []))} history entries"

    def export_collection(self, coll, include_secrets=False):
        """For sharing: credentials are left out unless asked for ({{variable}} references are kept)."""
        if not include_secrets:
            coll = vault.strip_collection(coll)
        return {"zapi": "collection", "name": coll["name"], "variables": coll.get("variables", []),
                "folders": list(coll.get("folders", [])), "items": copy.deepcopy(coll["items"])}
