"""Collection- and folder-level settings that requests inherit: authorization, headers and scripts.

A collection and each of its folders can carry `auth`, `headers` and `scripts` (folders in collection["folder_settings"],
keyed by folder path). A request whose auth type is "inherit" uses the nearest parent that sets one; headers from the
collection and folders are added to the request's own (the request wins on the same name); scripts run collection first,
then folders from the outermost, then the request."""
import copy

INHERIT = "inherit"


def folder_chain(folder):
    """'a/b/c' -> ['a', 'a/b', 'a/b/c'] (outermost first)."""
    parts = [p for p in (folder or "").strip("/").split("/") if p]
    return ["/".join(parts[:i + 1]) for i in range(len(parts))]


def levels(collection, folder):
    """[(label, settings dict)] outermost first: the collection, then each folder on the way down."""
    out = [(f"collection “{collection.get('name', '')}”", collection)]
    fs = collection.get("folder_settings") or {}
    for path in folder_chain(folder):
        out.append((f"folder “{path}”", fs.get(path) or {}))
    return out


def _real_auth(a):
    return isinstance(a, dict) and a.get("type") not in (None, "", INHERIT)


def resolve_auth(collection, req):
    """(auth dict, where it came from). Where: 'request', 'folder “x”', 'collection “y”' or '' (nothing set: no auth)."""
    own = req.get("auth") or {}
    if own.get("type") != INHERIT:
        return own, "request"
    chain = levels(collection, req.get("folder", ""))
    for n in range(len(chain) - 1, -1, -1):
        label, s = chain[n]
        if _real_auth(s.get("auth")):
            if n == 0 and s["auth"].get("type") == "none":
                break                                   # a collection set to "none" just means nothing is set
            return s["auth"], label                    # a folder set to "none" switches auth off for its requests
    return {"type": "none"}, ""


def inherited_headers(collection, req):
    """[(key, value, label)] added by the collection and folders, minus the ones the request sets itself."""
    own = {h.get("key", "").lower() for h in req.get("headers", []) if h.get("enabled", True) and h.get("key")}
    merged = {}
    for label, s in levels(collection, req.get("folder", "")):
        for h in s.get("headers") or []:
            if h.get("enabled", True) and h.get("key"):
                merged[h["key"].lower()] = (h["key"], h.get("value", ""), label)      # a deeper folder replaces an outer one
    return [v for k, v in merged.items() if k not in own]


def joined_script(collection, req, which):
    parts = []
    for _label, s in levels(collection, req.get("folder", "")):
        code = ((s.get("scripts") or {}).get(which) or "").strip("\n")
        if code.strip():
            parts.append(code)
    own = ((req.get("scripts") or {}).get(which) or "").strip("\n")
    if own.strip():
        parts.append(own)
    return "\n".join(parts)


def has_auth_above(collection, req):
    """True when the collection or one of the request's folders sets a real authorization (Postman export needs this)."""
    return any(_real_auth(s.get("auth")) and s["auth"].get("type") != "none"
               for _l, s in levels(collection, req.get("folder", "")))


def has_parent_settings(collection, req):
    for _label, s in levels(collection, req.get("folder", "")):
        if _real_auth(s.get("auth")) and s["auth"].get("type") != "none":
            return True
        if any(h.get("key") for h in s.get("headers") or []):
            return True
        if any(((s.get("scripts") or {}).get(k) or "").strip() for k in ("pre", "test")):
            return True
    return False


def effective(collection, req):
    """A deep copy of `req` with the inherited auth, headers and scripts filled in; safe to send or export.
    Without a collection the request is only made self-contained ('inherit' becomes no auth)."""
    out = copy.deepcopy(req)
    if collection is None:
        if (out.get("auth") or {}).get("type") == INHERIT:
            out["auth"] = {**out["auth"], "type": "none"}
        return out
    auth, _where = resolve_auth(collection, req)
    out["auth"] = {**(req.get("auth") or {}), **auth, "type": auth.get("type", "none")}
    extra = [{"key": k, "value": v, "enabled": True} for k, v, _l in inherited_headers(collection, req)]
    out["headers"] = extra + list(out.get("headers", []))
    out["scripts"] = {"pre": joined_script(collection, req, "pre"), "test": joined_script(collection, req, "test")}
    return out


def describe_auth(collection, req):
    """One sentence for the Authorization tab of a request that inherits."""
    if collection is None:
        return "This request is not in a collection, so there is nothing to inherit: no authorization is sent."
    auth, where = resolve_auth(collection, req)
    if not where:
        return "Nothing to inherit: the collection and its folders set no authorization, so none is sent."
    names = {"bearer": "Bearer token", "basic": "Basic auth", "digest": "Digest auth", "apikey": "API key",
             "oauth1": "OAuth 1.0", "oauth2": "OAuth 2.0", "aws": "AWS Signature", "hawk": "Hawk", "ntlm": "NTLM",
             "none": "No auth"}
    return f"Inherited from {where}: {names.get(auth.get('type'), auth.get('type', ''))}."


def describe_parents(collection, req):
    """What else the parents add (headers and scripts), for the notes on the Headers and Script tabs."""
    if collection is None:
        return {"headers": "", "pre": "", "test": ""}
    hdrs = inherited_headers(collection, req)
    out = {"headers": "", "pre": "", "test": ""}
    if hdrs:
        out["headers"] = "Also sent from the collection / folders: " + ", ".join(k for k, _v, _l in hdrs)
    for which in ("pre", "test"):
        labels = [label for label, s in levels(collection, req.get("folder", ""))
                  if ((s.get("scripts") or {}).get(which) or "").strip()]
        if labels:
            out[which] = "Also runs first: the script of " + ", ".join(labels) + "."
    return out
