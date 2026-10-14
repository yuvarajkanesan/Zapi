"""{{name}} substitution for URLs, headers, params, auth and bodies."""
import re

_VAR = re.compile(r"\{\{\s*([^{}\s]+)\s*\}\}")
_DYNAMIC = {}


def _dyn(name):
    def deco(fn):
        _DYNAMIC[name] = fn
        return fn
    return deco


@_dyn("$guid")
def _guid():
    import uuid
    return str(uuid.uuid4())


@_dyn("$timestamp")
def _ts():
    import time
    return str(int(time.time()))


@_dyn("$isoTimestamp")
def _iso():
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


@_dyn("$randomInt")
def _rint():
    import random
    return str(random.randint(0, 1000))


def merge_scopes(*scopes):
    """Later scopes win. Each scope is a plain {name: value} dict."""
    out = {}
    for s in scopes:
        out.update(s or {})
    return out


def substitute(text, values):
    """Replace {{name}}; unknown names are left as written so the problem is visible."""
    if not text or "{{" not in text:
        return text

    def rep(m):
        name = m.group(1)
        if name in values:
            return str(values[name])
        fn = _DYNAMIC.get(name)
        return fn() if fn else m.group(0)

    return _VAR.sub(rep, text)


def find_names(text):
    return [m.group(1) for m in _VAR.finditer(text or "")]


def unresolved(text, values):
    return [n for n in find_names(text) if n not in values and n not in _DYNAMIC]
