"""cURL form fields (-F / --form / --form-string) and multipart bodies <-> Zapi form-data rows.

A row is {"key", "value", "enabled", "type": "text" | "file"} and, when known, "content_type" and "filename"."""
import re

from . import store, variables

_MODS = re.compile(r";(?=\s*(?:type|filename|headers|encoder)\s*=)", re.I)
BOUNDARY = "----ZapiBoundary"


def _unquote(text):
    """'"a ""quoted"" value"' -> (value, what is left after the closing quote). Only when text starts with a quote."""
    out, i = [], 1
    while i < len(text):
        c = text[i]
        if c == "\\" and i + 1 < len(text) and text[i + 1] in '"\\':
            out.append(text[i + 1])
            i += 2
            continue
        if c == '"':
            return "".join(out), text[i + 1:]
        out.append(c)
        i += 1
    return text, ""                                    # no closing quote: take it literally


def form_row(spec, literal=False):
    """One -F argument: name=value, name=@file, name=<file, with optional ;type=..;filename=.. (--form-string: literal)."""
    name, _, rest = spec.partition("=")
    row_extra = {}
    if literal:
        return store.kv(name, rest, type="text")
    mods = ""
    if rest.startswith('"'):
        rest, mods = _unquote(rest)
    else:
        parts = _MODS.split(rest, maxsplit=1)
        rest, mods = parts[0], (parts[1] if len(parts) > 1 else "")
    is_file = rest[:1] in ("@", "<")
    if is_file and rest[1:2] == '"':                       # Postman writes file=@"/path with spaces/a.png";type=...
        path, more = _unquote(rest[1:])
        rest, mods = rest[:1] + path, more.lstrip() if not mods else mods + more
    for m in re.finditer(r"\s*(type|filename)\s*=\s*(\"[^\"]*\"|[^;]*)", mods or "", re.I):
        key = "content_type" if m.group(1).lower() == "type" else "filename"
        row_extra[key] = m.group(2).strip().strip('"')
    if is_file:
        row = store.kv(name, rest[1:], type="file")
    else:
        row = store.kv(name, rest, type="text")
    row.update({k: v for k, v in row_extra.items() if v})
    return row


def _header_params(line):
    """'form-data; name="a"; filename="b.png"' -> {"name": "a", "filename": "b.png"}"""
    return {m.group(1).lower(): m.group(2) if m.group(2) is not None else m.group(3)
            for m in re.finditer(r';\s*([\w-]+)\s*=\s*(?:"([^"]*)"|([^;\s]*))', line)}


def parse_multipart(body, boundary):
    """The parts of a multipart/form-data body as form rows. A part with a filename becomes a file row whose path is
    empty (the file's bytes are not a path on this computer: choose the file again). [] when it is not multipart."""
    if not boundary or ("--" + boundary) not in body:
        return []
    rows = []
    for chunk in body.split("--" + boundary):
        if chunk.strip() in ("", "--"):
            continue
        chunk = chunk[2:] if chunk.startswith("\r\n") else chunk[1:] if chunk.startswith("\n") else chunk
        sep = "\r\n\r\n" if "\r\n\r\n" in chunk else "\n\n"
        head, _, value = chunk.partition(sep)
        if not _:
            continue
        for tail in ("\r\n", "\n"):
            if value.endswith(tail):
                value = value[:-len(tail)]
                break
        disp, ctype = {}, ""
        for line in head.splitlines():
            k, _, v = line.partition(":")
            if k.strip().lower() == "content-disposition":
                disp = _header_params(v)
            elif k.strip().lower() == "content-type":
                ctype = v.strip()
        name = disp.get("name")
        if name is None:
            continue
        if "filename" in disp:
            row = store.kv(name, "", type="file")
            if disp["filename"]:
                row["filename"] = disp["filename"]
        else:
            row = store.kv(name, value, type="text")
        if ctype and (row["type"] == "file" or not ctype.lower().startswith("text/plain")):
            row["content_type"] = ctype
        rows.append(row)
    return rows


def boundary_of(content_type):
    m = re.search(r'boundary\s*=\s*(?:"([^"]+)"|([^;\s]+))', content_type or "", re.I)
    return (m.group(1) or m.group(2)) if m else ""


def multipart_text(rows, values):
    """A readable multipart body for code snippets (file parts show a placeholder instead of their bytes)."""
    out = []
    for r in rows:
        name = variables.substitute(r.get("key", ""), values)
        val = variables.substitute(r.get("value", ""), values)
        out.append(f"--{BOUNDARY}\r\n")
        if r.get("type") == "file":
            fname = r.get("filename") or val.replace("\\", "/").split("/")[-1] or "file"
            out.append(f'Content-Disposition: form-data; name="{name}"; filename="{fname}"\r\n'
                       f"Content-Type: {r.get('content_type') or 'application/octet-stream'}\r\n\r\n<contents of {val or fname}>")
        else:
            out.append(f'Content-Disposition: form-data; name="{name}"\r\n'
                       + (f"Content-Type: {r['content_type']}\r\n" if r.get("content_type") else "") + f"\r\n{val}")
        out.append("\r\n")
    out.append(f"--{BOUNDARY}--\r\n")
    return "".join(out), f"multipart/form-data; boundary={BOUNDARY}"
