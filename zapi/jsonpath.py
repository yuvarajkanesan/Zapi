"""A small JSONPath for filtering responses: $ . .. [n] [-n] [a:b] [*] ['key'] [?(@.x > 1)] and .length() / .keys().

Not the whole of RFC 9535, but what people type when they look at an API response."""
import json
import re


class PathError(ValueError):
    pass


_NUM = re.compile(r"-?\d+")


def _tokens(path):
    p = path.strip()
    if not p:
        raise PathError("Enter a path such as $.data[0].id")
    if p[0] not in "$@":
        p = "$." + p if p[0] not in ".[" else "$" + p
    i, out = 1, []
    while i < len(p):
        c = p[i]
        if c == ".":
            if p.startswith("..", i):
                i += 2
                if i < len(p) and p[i] == "[":
                    out.append(("deep", None))
                    continue
                m = re.match(r"[^.\[\s()]+", p[i:])
                if not m:
                    raise PathError("Expected a name after '..'")
                out.append(("deep", None))
                out.append(("key", m.group(0)) if m.group(0) != "*" else ("wild", None))
                i += m.end()
                continue
            i += 1
            m = re.match(r"[^.\[\s()]+", p[i:])
            if not m:
                raise PathError(f"Expected a name after '.' at position {i}")
            name = m.group(0)
            i += m.end()
            if p.startswith("()", i):
                i += 2
                out.append(("func", name))
            elif name == "*":
                out.append(("wild", None))
            else:
                out.append(("key", name))
        elif c == "[":
            depth, j, quote = 0, i, None
            while j < len(p):
                ch = p[j]
                if quote:
                    if ch == quote:
                        quote = None
                elif ch in "'\"":
                    quote = ch
                elif ch == "[":
                    depth += 1
                elif ch == "]":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            if j >= len(p):
                raise PathError("Missing ']'")
            out.append(_bracket(p[i + 1:j].strip()))
            i = j + 1
        else:
            raise PathError(f"Unexpected '{c}' at position {i}")
    return out


def _bracket(inner):
    if inner == "*":
        return ("wild", None)
    if inner.startswith("?"):
        expr = inner[1:].strip()
        if expr.startswith("(") and expr.endswith(")"):
            expr = expr[1:-1].strip()
        return ("filter", _parse_filter(expr))
    if len(inner) >= 2 and inner[0] in "'\"" and inner[-1] == inner[0]:
        return ("key", inner[1:-1])
    if _NUM.fullmatch(inner):
        return ("index", int(inner))
    m = re.fullmatch(r"(-?\d*)\s*:\s*(-?\d*)", inner)
    if m:
        return ("slice", (int(m.group(1)) if m.group(1) else None, int(m.group(2)) if m.group(2) else None))
    if "," in inner:
        return ("union", [_bracket(x.strip()) for x in inner.split(",")])
    raise PathError(f"Cannot read [{inner}]")


_OPS = ("==", "!=", ">=", "<=", "=~", ">", "<")


def _parse_filter(expr):
    """@.a > 1 && @.b == 'x'  (|| and && of simple comparisons, or just @.a to test existence)."""
    for sep, kind in ((" || ", "or"), (" && ", "and")):
        if sep in expr:
            return (kind, [_parse_filter(x.strip()) for x in expr.split(sep)])
    for op in _OPS:
        idx = expr.find(op)
        if idx > 0:
            left, right = expr[:idx].strip(), expr[idx + len(op):].strip()
            return ("cmp", op, _tokens(left.replace("@", "$", 1)), _literal(right))
    return ("exists", _tokens(expr.replace("@", "$", 1)))


def _literal(text):
    if len(text) >= 2 and text[0] in "'\"" and text[-1] == text[0]:
        return text[1:-1]
    if text in ("true", "false", "null"):
        return {"true": True, "false": False, "null": None}[text]
    try:
        return json.loads(text)
    except ValueError:
        return text


def _children(node):
    if isinstance(node, dict):
        return list(node.values())
    if isinstance(node, list):
        return list(node)
    return []


def _descend(node):
    yield node
    for c in _children(node):
        yield from _descend(c)


def _test(node, f):
    kind = f[0]
    if kind == "and":
        return all(_test(node, x) for x in f[1])
    if kind == "or":
        return any(_test(node, x) for x in f[1])
    if kind == "exists":
        return bool(_apply([node], f[1]))
    _, op, toks, want = f
    for got in _apply([node], toks):
        try:
            if op == "==" and got == want:
                return True
            if op == "!=" and got != want:
                return True
            if op == "=~" and re.search(str(want), str(got)):
                return True
            if op in (">", "<", ">=", "<=") and not isinstance(got, (dict, list)) and got is not None:
                a, b = (got, want) if isinstance(got, type(want)) or (isinstance(got, (int, float)) and
                                                                      isinstance(want, (int, float))) else (str(got), str(want))
                if (op == ">" and a > b) or (op == "<" and a < b) or (op == ">=" and a >= b) or (op == "<=" and a <= b):
                    return True
        except TypeError:
            continue
    return False


def _apply(nodes, toks):
    for kind, arg in toks:
        out = []
        for n in nodes:
            if kind == "key":
                if isinstance(n, dict) and arg in n:
                    out.append(n[arg])
            elif kind == "index":
                if isinstance(n, list) and -len(n) <= arg < len(n):
                    out.append(n[arg])
            elif kind == "wild":
                out.extend(_children(n))
            elif kind == "slice":
                if isinstance(n, list):
                    out.extend(n[slice(*arg)])
            elif kind == "deep":
                out.extend(_descend(n))
            elif kind == "filter":
                out.extend(c for c in _children(n) if _test(c, arg))
            elif kind == "union":
                for tok in arg:
                    out.extend(_apply([n], [tok]))
            elif kind == "func":
                if arg == "length":
                    out.append(len(n) if isinstance(n, (list, dict, str)) else 0)
                elif arg == "keys" and isinstance(n, dict):
                    out.append(list(n.keys()))
                else:
                    raise PathError(f"Unknown function {arg}()")
        nodes = out
    return nodes


def query(data, path):
    """All values the path selects, in document order. Raises PathError for a path that cannot be read."""
    return _apply([data], _tokens(path))


def format_results(results):
    """One match shows as itself; several show as a list."""
    value = results[0] if len(results) == 1 else results
    if isinstance(value, str):
        return value
    return json.dumps(value, indent=2, ensure_ascii=False)
