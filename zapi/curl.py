"""Turn a pasted cURL command into a request, and a request into code snippets."""
import json
import re
import shlex
import urllib.parse

from . import http_client, store, variables


class CurlError(ValueError):
    pass


_ANSI_C = re.compile(r"\$'((?:[^'\\]|\\.)*)'", re.S)
_ESCAPES = {"n": "\n", "r": "\r", "t": "\t", "\\": "\\", "'": "'", '"': '"', "a": "\a", "b": "\b", "f": "\f",
            "v": "\v", "e": "\x1b"}


def _decode_ansi_c(m):
    """bash $'...' strings (Chrome uses them when the body has quotes or newlines) -> a normal quoted string."""
    s, out, i = m.group(1), [], 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            n = s[i + 1]
            if n in _ESCAPES:
                out.append(_ESCAPES[n])
                i += 2
            elif n in "xuU":
                width = {"x": 2, "u": 4, "U": 8}[n]
                hexs = s[i + 2:i + 2 + width]
                try:
                    out.append(chr(int(hexs, 16)))
                    i += 2 + width
                except ValueError:
                    out.append(c)
                    i += 1
            else:
                out.append(c + n)
                i += 2
        else:
            out.append(c)
            i += 1
    return shlex.quote("".join(out))


def _tokenize(text):
    text = text.strip()
    # Windows "Copy as cURL (cmd)": every special character is escaped with ^ and lines end with ^
    cmd_style = bool(re.search(r"\^\r?\n", text)) or '^"' in text
    # line continuations from bash (\), cmd (^) and PowerShell (`)
    text = re.sub(r"\\\r?\n|\^\r?\n|`\r?\n", " ", text)
    if cmd_style:
        text = re.sub(r"\^(.)", r"\1", text, flags=re.S)
    text = _ANSI_C.sub(_decode_ansi_c, text)
    try:
        return shlex.split(text, posix=True)
    except ValueError as e:
        raise CurlError(f"Could not read the command: {e}")


def parse(text):
    """Returns a new request dict. Raises CurlError when no URL is found."""
    # cmd.exe style: curl "https://x" -H "A: b" -d "{\"a\":1}" -> backslash-escaped quotes are handled by shlex
    tokens = _tokenize(text)
    if tokens and tokens[0].lower() in ("curl", "curl.exe"):
        tokens = tokens[1:]
    if not tokens:
        raise CurlError("Paste a cURL command, for example: curl https://api.example.com/users")
    method = None
    url = None
    headers = []
    data_parts = []
    form = []
    user = None
    get_flag = False
    json_flag = False
    i = 0

    def value():
        nonlocal i
        i += 1
        if i >= len(tokens):
            raise CurlError(f"{tokens[i - 1]} needs a value")
        return tokens[i]

    while i < len(tokens):
        t = tokens[i]
        if t in ("-X", "--request"):
            method = value().upper()
        elif t.startswith("-X") and len(t) > 2:
            method = t[2:].upper()
        elif t.startswith("--request="):
            method = t.split("=", 1)[1].upper()
        elif t in ("-H", "--header"):
            headers.append(value())
        elif t.startswith("--header="):
            headers.append(t.split("=", 1)[1])
        elif t in ("-d", "--data", "--data-raw", "--data-binary", "--data-ascii", "--data-urlencode"):
            data_parts.append(value())
        elif t.startswith("--data=") or t.startswith("--data-raw="):
            data_parts.append(t.split("=", 1)[1])
        elif t in ("-F", "--form"):
            form.append(value())
        elif t in ("-u", "--user"):
            user = value()
        elif t in ("-A", "--user-agent"):
            headers.append("User-Agent: " + value())
        elif t in ("-b", "--cookie"):
            headers.append("Cookie: " + value())
        elif t in ("-e", "--referer"):
            headers.append("Referer: " + value())
        elif t in ("-G", "--get"):
            get_flag = True
        elif t == "--json":
            data_parts.append(value())
            json_flag = True
        elif t == "--url":
            url = value()
        elif t in ("-o", "--output", "-m", "--max-time", "--connect-timeout", "--proxy", "-x", "--cacert",
                   "--cert", "--key", "-w", "--write-out", "--retry"):
            value()
        elif t.startswith("-") and t != "-":
            pass   # -s -k -L -i -v --compressed ... carry no request data
        elif url is None:
            url = t
        i += 1
    return _from_parts(url, method, headers, data_parts, form, user, get_flag, json_flag)


# headers browsers add that must not be copied: the client sets its own, and a stale value breaks the request
_SKIP_HEADERS = {"accept-encoding", "content-length", "host", "connection", "transfer-encoding"}


def _from_parts(url, method, headers, data_parts, form, user, get_flag, json_flag):
    """The shared tail of cURL and fetch() import: build the request from already-split pieces."""
    if not url:
        raise CurlError("No URL found in the command.")
    if "://" not in url and not url.startswith("{{"):
        url = "http://" + url

    req = store.new_request(name=_name_from_url(url), url=url)
    if get_flag and data_parts:
        sep = "&" if "?" in req["url"] else "?"
        req["url"] += sep + "&".join(data_parts)
        data_parts = []
    ctype = ""
    for h in headers:
        k, _, v = h.partition(":")
        if not k.strip() or k.strip().lower() in _SKIP_HEADERS or h.startswith(":"):
            continue
        if k.strip().lower() == "content-type":
            ctype = v.strip().lower()
        req["headers"].append(store.kv(k.strip(), v.strip()))
    if json_flag and not ctype:
        req["headers"].append(store.kv("Content-Type", "application/json"))
        ctype = "application/json"

    if user is not None:
        name, _, pw = user.partition(":")
        req["auth"].update({"type": "basic", "username": name, "password": pw})
    else:
        for h in list(req["headers"]):
            if h["key"].lower() == "authorization" and h["value"].lower().startswith("bearer "):
                req["auth"].update({"type": "bearer", "token": h["value"][7:].strip()})
                req["headers"].remove(h)

    body = req["body"]
    if form:
        body["mode"] = "form-data"
        for f in form:
            k, _, v = f.partition("=")
            if v.startswith("@"):
                body["form"].append(store.kv(k, v[1:], type="file"))
            else:
                body["form"].append(store.kv(k, v, type="text"))
        req["headers"] = [h for h in req["headers"] if h["key"].lower() != "content-type"]
    elif data_parts:
        joined = "&".join(data_parts)
        if "application/x-www-form-urlencoded" in ctype or (not ctype and not joined.lstrip().startswith(("{", "["))):
            body["mode"] = "x-www-form-urlencoded"
            for pair in joined.split("&"):
                k, _, v = pair.partition("=")
                body["urlencoded"].append(store.kv(urllib.parse.unquote_plus(k), urllib.parse.unquote_plus(v)))
            req["headers"] = [h for h in req["headers"] if h["key"].lower() != "content-type"]
        else:
            body["mode"] = "raw"
            body["raw"] = joined
            body["raw_type"] = _raw_type(ctype, joined)
            if body["raw_type"] == "JSON":
                req["headers"] = [h for h in req["headers"] if h["key"].lower() != "content-type"]
    req["method"] = method or ("POST" if (data_parts or form) and not get_flag else "GET")
    _split_query(req)
    return req


def _balanced(text, start):
    """Index just past the {...} / (...) group opening at `start`, ignoring brackets inside strings."""
    pairs = {"{": "}", "(": ")", "[": "]"}
    stack, quote, i = [], "", start
    while i < len(text):
        c = text[i]
        if quote:
            if c == "\\":
                i += 1
            elif c == quote:
                quote = ""
        elif c in "\"'`":
            quote = c
        elif c in pairs:
            stack.append(pairs[c])
        elif stack and c == stack[-1]:
            stack.pop()
            if not stack:
                return i + 1
        i += 1
    raise CurlError("The fetch() call is not complete (unbalanced brackets).")


def _loose_json(text):
    """Parse a JavaScript object literal: comments, bare keys, single quotes, trailing commas, JSON.stringify()."""
    text = re.sub(r"(?m)^\s*//.*$", "", text)
    while True:                                  # JSON.stringify(<literal>) -> a JSON string holding that literal
        m = re.search(r"JSON\.stringify\s*\(", text)
        if not m:
            break
        end = _balanced(text, m.end() - 1)
        inner = _loose_json(text[m.end():end - 1].split(",null,")[0])
        text = text[:m.start()] + json.dumps(json.dumps(inner)) + text[end:]
    text = re.sub(r"'((?:[^'\\\"]|\\.)*)'", lambda m: json.dumps(m.group(1).replace("\\'", "'")), text)
    text = re.sub(r"([{,]\s*)([A-Za-z_$][\w$]*)\s*:", r'\1"\2":', text)
    text = re.sub(r",(\s*[}\]])", r"\1", text)
    try:
        return json.loads(text)
    except ValueError as e:
        raise CurlError(f"Could not read the fetch() options: {e}")


def parse_fetch(text):
    """Chrome / Firefox 'Copy as fetch': fetch("url", { method, headers, body })."""
    m = re.match(r"\s*(?:await\s+)?fetch\s*\(\s*", text)
    if not m:
        raise CurlError("Not a fetch() call.")
    rest = text[m.end():]
    q = re.match(r"""(["'`])((?:\\.|(?!\1).)*)\1""", rest, re.S)
    if not q:
        raise CurlError("fetch() needs a URL string as its first argument.")
    url = json.loads(f'"{q.group(2)}"') if q.group(1) == '"' else q.group(2).replace("\\'", "'")
    rest = rest[q.end():].lstrip()
    opts = {}
    if rest.startswith(","):
        rest = rest[1:].lstrip()
        if rest.startswith("{"):
            opts = _loose_json(rest[:_balanced(rest, 0)])
    headers = opts.get("headers") or {}
    if isinstance(headers, list):
        headers = {k: v for k, v in headers}
    body = opts.get("body")
    method = (opts.get("method") or "").upper() or None
    return _from_parts(url, method, [f"{k}: {v}" for k, v in headers.items()],
                       [body] if isinstance(body, str) else [], [], None, False, False)


def looks_like_import(text):
    """Quick test used while typing / pasting: is this text a cURL command, a fetch() call or an API document?"""
    t = text.strip()
    low = t.lower()
    if low.startswith(("curl ", "curl.exe ")):
        return True
    if re.match(r"(?:await\s+)?fetch\s*\(", t):
        return True
    return len(t) > 20 and t[0] in "{[" and t[-1] in "}]"


def parse_any(text):
    """('request' | 'collection' | 'environment', object) for pasted cURL, fetch(), Postman / OpenAPI / Zapi JSON."""
    from . import importers
    t = text.strip()
    low = t.lower()
    if low.startswith(("curl ", "curl.exe ")) or low in ("curl", "curl.exe"):
        return "request", parse(t)
    if re.match(r"(?:await\s+)?fetch\s*\(", t):
        return "request", parse_fetch(t)
    if t[:1] in ("{", "["):
        try:
            data = json.loads(t)
            kind, obj = importers.import_data(data)
        except ValueError as e:                  # includes importers.ImportError_
            raise CurlError(str(e))
        return kind, obj
    raise CurlError("Not recognised. Paste a cURL command, a fetch() call, or a Postman / OpenAPI / Zapi file.")


def _raw_type(ctype, text):
    if "json" in ctype or (not ctype and text.lstrip().startswith(("{", "["))):
        return "JSON"
    if "xml" in ctype:
        return "XML"
    if "html" in ctype:
        return "HTML"
    if "javascript" in ctype:
        return "JavaScript"
    return "Text"


def _name_from_url(url):
    path = urllib.parse.urlsplit(url).path.strip("/")
    return path or urllib.parse.urlsplit(url).netloc or "Imported request"


def _split_query(req):
    """Move ?a=b into the Params table so it can be edited row by row."""
    url = req["url"]
    if "?" not in url or "{{" in url.split("?", 1)[1]:
        return
    base, _, query = url.partition("?")
    query, _, frag = query.partition("#")
    req["url"] = base + ("#" + frag if frag else "")
    for k, v in urllib.parse.parse_qsl(query, keep_blank_values=True):
        req["params"].append(store.kv(k, v))


# ---- code generation --------------------------------------------------------
from . import codegen_more

LANGUAGES = ["cURL", "Python (requests)", "JavaScript (fetch)", "PowerShell"] + list(codegen_more.GENERATORS)


def _resolved(req, values):
    url = http_client.build_url(req, values)
    headers = http_client.build_headers(req, values)
    mode = (req.get("body") or {}).get("mode", "none")
    data, ctype = (None, None)
    if mode != "form-data":
        data, ctype = http_client.build_body(req, values)
    names = {k.lower() for k, _ in headers}
    if ctype and "content-type" not in names:
        headers.append(("Content-Type", ctype))
    return url, headers, data


def _sq(s):
    return "'" + s.replace("'", "'\"'\"'") + "'"


def generate(req, values, language):
    url, headers, data = _resolved(req, values)
    method = req.get("method", "GET").upper()
    body = req.get("body") or {}
    form = [r for r in body.get("form", []) if r.get("enabled", True) and r.get("key")] \
        if body.get("mode") == "form-data" else []
    text = data.decode("utf-8", errors="replace") if data else None
    if language == "cURL":
        parts = ["curl" + (f" -X {method}" if method != "GET" else "") + " " + _sq(url)]
        for k, v in headers:
            parts.append("-H " + _sq(f"{k}: {v}"))
        for r in form:
            val = variables.substitute(r.get("value", ""), values)
            parts.append("-F " + _sq(f"{r['key']}=" + ("@" if r.get("type") == "file" else "") + val))
        if text is not None:
            parts.append("--data-raw " + _sq(text))
        return " \\\n  ".join(parts)
    if language == "Python (requests)":
        lines = ["import requests", "", f"url = {json.dumps(url)}"]
        if headers:
            lines.append("headers = " + json.dumps(dict(headers), indent=4))
        args = [f"{method!r}".replace("'", '"'), "url"]
        if headers:
            args.append("headers=headers")
        if form:
            lines.append("data = " + json.dumps({r["key"]: r.get("value", "") for r in form if r.get("type") != "file"}))
            args.append("data=data")
        if text is not None:
            lines.append(f"payload = {json.dumps(text)}")
            args.append("data=payload")
        lines += ["", f"response = requests.request({', '.join(args)})", "print(response.status_code)", "print(response.text)"]
        return "\n".join(lines)
    if language == "JavaScript (fetch)":
        opts = [f'  method: "{method}"']
        if headers:
            opts.append("  headers: " + json.dumps(dict(headers), indent=4).replace("\n", "\n  "))
        if text is not None:
            opts.append(f"  body: {json.dumps(text)}")
        return (f"const response = await fetch({json.dumps(url)}, {{\n" + ",\n".join(opts) +
                "\n});\nconsole.log(response.status, await response.text());")
    if language == "PowerShell":
        def ps(s):
            return "'" + s.replace("'", "''") + "'"
        lines = []
        if headers:
            lines.append("$headers = @{\n" + "\n".join(f"  {ps(k)} = {ps(v)}" for k, v in headers) + "\n}")
        cmd = f"Invoke-RestMethod -Method {method} -Uri {ps(url)}"
        if headers:
            cmd += " -Headers $headers"
        if text is not None:
            lines.append(f"$body = {ps(text)}")
            cmd += " -Body $body"
        lines.append(cmd)
        return "\n".join(lines)
    if language in codegen_more.GENERATORS:
        return codegen_more.GENERATORS[language](method, url, headers, text)
    raise ValueError(f"Unknown language: {language}")
