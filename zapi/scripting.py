"""Pre-request and test scripts: a Postman-style `pm` API running on Qt's JavaScript engine (QJSEngine).

Scripts are plain ES2017 JavaScript. All data crosses the boundary as JSON, so a script can only touch what
the `pm` object exposes: variables, the request / response, tests and the console.
"""
import base64
import hashlib
import hmac
import json
import threading
import uuid
from dataclasses import dataclass, field

from PySide6.QtCore import QCoreApplication, QObject, Slot
from PySide6.QtQml import QJSEngine

SCRIPT_TIMEOUT = 10.0


@dataclass
class ScriptResult:
    ok: bool = True
    error: str = ""
    tests: list = field(default_factory=list)        # [{"name", "passed", "error"}]
    logs: list = field(default_factory=list)         # [{"level", "text"}]
    environment: dict = field(default_factory=dict)
    globals: dict = field(default_factory=dict)
    collection: dict = field(default_factory=dict)
    variables: dict = field(default_factory=dict)    # local scope, lives for one request
    request: dict = field(default_factory=dict)
    next_request: object = "__unset__"                # "__unset__" = not called, None = stop, str = name


class _Bridge(QObject):
    """Native helpers the JS side calls for things JavaScript cannot do alone."""

    @Slot(str, str, result=str)
    def digest(self, algo, text):
        return hashlib.new(algo, text.encode("utf-8")).hexdigest()

    @Slot(str, str, str, result=str)
    def hmac_(self, algo, key, text):
        return hmac.new(key.encode("utf-8"), text.encode("utf-8"), algo).hexdigest()

    @Slot(str, result=str)
    def b64enc(self, text):
        return base64.b64encode(text.encode("utf-8")).decode("ascii")

    @Slot(str, result=str)
    def b64dec(self, text):
        try:
            return base64.b64decode(text + "=" * (-len(text) % 4)).decode("utf-8", "replace")
        except ValueError:
            return ""

    @Slot(str, result=str)
    def dynamic(self, name):
        from . import variables
        fn = variables._DYNAMIC.get(name)
        return fn() if fn else ""

    @Slot(result=str)
    def uuid(self):
        return str(uuid.uuid4())


_SHIM = r"""
(function (__ctx, __b) {
var ctx = JSON.parse(__ctx);
var logs = [], tests = [], legacyTests = {}, nextRequest = "__unset__";
function fmt(a) { if (typeof a === 'string') return a; try { var s = JSON.stringify(a); return s === undefined ? String(a) : s; } catch (e) { return String(a); } }
var console = {};
['log', 'info', 'warn', 'error', 'debug'].forEach(function (l) {
  console[l] = function () { logs.push({level: l, text: Array.prototype.map.call(arguments, fmt).join(' ')}); };
});
function str(v) { return (v !== null && typeof v === 'object') ? JSON.stringify(v) : String(v); }

function Scope(data) { Object.defineProperty(this, '_d', {value: data, enumerable: false}); }
Scope.prototype.get = function (k) { return Object.prototype.hasOwnProperty.call(this._d, k) ? this._d[k] : undefined; };
Scope.prototype.set = function (k, v) { this._d[k] = str(v); };
Scope.prototype.unset = function (k) { delete this._d[k]; };
Scope.prototype.has = function (k) { return Object.prototype.hasOwnProperty.call(this._d, k); };
Scope.prototype.clear = function () { for (var k in this._d) delete this._d[k]; };
Scope.prototype.toObject = function () { return JSON.parse(JSON.stringify(this._d)); };
Scope.prototype.replaceIn = function (s) { return replaceVars(String(s)); };

var environment = ctx.environment || {}, globals = ctx.globals || {}, collection = ctx.collection || {};
var local = ctx.variables || {}, iteration = ctx.iterationData || {};
function lookup(k) {
  var scopes = [local, iteration, environment, collection, globals];
  for (var i = 0; i < scopes.length; i++) if (Object.prototype.hasOwnProperty.call(scopes[i], k)) return scopes[i][k];
  return undefined;
}
function replaceVars(s) {
  return s.replace(/\{\{\s*([^{}\s]+)\s*\}\}/g, function (m, name) {
    var v = lookup(name);
    if (v !== undefined) return String(v);
    if (name.charAt(0) === '$') { var d = __b.dynamic(name); if (d) return d; }
    return m;
  });
}
var variables = new Scope(local);
variables.get = function (k) { return lookup(k); };
variables.has = function (k) { return lookup(k) !== undefined; };
variables.toObject = function () { var o = {}; [globals, collection, environment, iteration, local].forEach(function (s) { for (var k in s) o[k] = s[k]; }); return o; };

// ---- chai-style assertions -------------------------------------------------------------
function AssertionError(msg) { this.name = 'AssertionError'; this.message = msg; }
AssertionError.prototype = Object.create(Error.prototype);
function deepEqual(a, b) {
  if (a === b) return true;
  if (typeof a !== typeof b || a === null || b === null || typeof a !== 'object') return a !== a && b !== b;
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  var ka = Object.keys(a), kb = Object.keys(b);
  if (ka.length !== kb.length) return false;
  for (var i = 0; i < ka.length; i++) if (!Object.prototype.hasOwnProperty.call(b, ka[i]) || !deepEqual(a[ka[i]], b[ka[i]])) return false;
  return true;
}
function typeOf(v) { if (v === null) return 'null'; if (Array.isArray(v)) return 'array'; return typeof v; }
function inspect(v) { return typeof v === 'string' ? JSON.stringify(v) : fmt(v); }
function Assertion(obj, neg, deep) { this._o = obj; this._n = !!neg; this._deep = !!deep; }
Assertion.prototype._assert = function (cond, msg, negMsg) {
  if (this._n ? cond : !cond) throw new AssertionError(this._n ? negMsg : msg);
  return this;
};
['to', 'be', 'been', 'is', 'that', 'which', 'and', 'has', 'have', 'with', 'at', 'of', 'same', 'but', 'does', 'still', 'also', 'itself', 'all', 'any'].forEach(function (w) {
  Object.defineProperty(Assertion.prototype, w, {get: function () { return this; }});
});
Object.defineProperty(Assertion.prototype, 'not', {get: function () { return new Assertion(this._o, !this._n, this._deep); }});
Object.defineProperty(Assertion.prototype, 'deep', {get: function () { return new Assertion(this._o, this._n, true); }});
function flag(name, test, text) {
  Object.defineProperty(Assertion.prototype, name, {get: function () {
    return this._assert(test(this._o), 'expected ' + inspect(this._o) + ' to be ' + text, 'expected ' + inspect(this._o) + ' not to be ' + text);
  }});
}
flag('ok', function (o) { return !!o; }, 'truthy');
flag('true', function (o) { return o === true; }, 'true');
flag('false', function (o) { return o === false; }, 'false');
flag('null', function (o) { return o === null; }, 'null');
flag('undefined', function (o) { return o === undefined; }, 'undefined');
flag('NaN', function (o) { return o !== o; }, 'NaN');
flag('exist', function (o) { return o !== null && o !== undefined; }, 'existing');
flag('empty', function (o) { return o == null ? true : (typeof o === 'object' && !Array.isArray(o) ? Object.keys(o).length === 0 : o.length === 0); }, 'empty');
var P = Assertion.prototype;
P.equal = P.equals = P.eq = function (v) {
  var ok = this._deep ? deepEqual(this._o, v) : this._o === v;
  return this._assert(ok, 'expected ' + inspect(this._o) + ' to ' + (this._deep ? 'deeply ' : '') + 'equal ' + inspect(v), 'expected ' + inspect(this._o) + ' not to ' + (this._deep ? 'deeply ' : '') + 'equal ' + inspect(v));
};
P.eql = function (v) { return this._assert(deepEqual(this._o, v), 'expected ' + inspect(this._o) + ' to deeply equal ' + inspect(v), 'expected ' + inspect(this._o) + ' not to deeply equal ' + inspect(v)); };
P.a = P.an = function (t) { return this._assert(typeOf(this._o) === String(t).toLowerCase(), 'expected ' + inspect(this._o) + ' to be a ' + t, 'expected ' + inspect(this._o) + ' not to be a ' + t); };
P.above = P.gt = P.greaterThan = function (n) { return this._assert(this._o > n, 'expected ' + inspect(this._o) + ' to be above ' + n, 'expected ' + inspect(this._o) + ' not to be above ' + n); };
P.below = P.lt = P.lessThan = function (n) { return this._assert(this._o < n, 'expected ' + inspect(this._o) + ' to be below ' + n, 'expected ' + inspect(this._o) + ' not to be below ' + n); };
P.least = P.gte = function (n) { return this._assert(this._o >= n, 'expected ' + inspect(this._o) + ' to be at least ' + n, 'expected ' + inspect(this._o) + ' to be below ' + n); };
P.most = P.lte = function (n) { return this._assert(this._o <= n, 'expected ' + inspect(this._o) + ' to be at most ' + n, 'expected ' + inspect(this._o) + ' to be above ' + n); };
P.within = function (a, b) { return this._assert(this._o >= a && this._o <= b, 'expected ' + inspect(this._o) + ' to be within ' + a + '..' + b, 'expected ' + inspect(this._o) + ' not to be within ' + a + '..' + b); };
P.include = P.includes = P.contain = P.contains = function (v) {
  var o = this._o, ok;
  if (typeof o === 'string') ok = o.indexOf(v) >= 0;
  else if (Array.isArray(o)) ok = this._deep ? o.some(function (x) { return deepEqual(x, v); }) : o.indexOf(v) >= 0;
  else if (o && typeof o === 'object') ok = Object.keys(v).every(function (k) { return deepEqual(o[k], v[k]); });
  else ok = false;
  return this._assert(ok, 'expected ' + inspect(o) + ' to include ' + inspect(v), 'expected ' + inspect(o) + ' not to include ' + inspect(v));
};
P.string = function (s) { return this._assert(typeof this._o === 'string' && this._o.indexOf(s) >= 0, 'expected ' + inspect(this._o) + ' to contain ' + inspect(s), 'expected ' + inspect(this._o) + ' not to contain ' + inspect(s)); };
P.match = function (re) { return this._assert(re.test(this._o), 'expected ' + inspect(this._o) + ' to match ' + re, 'expected ' + inspect(this._o) + ' not to match ' + re); };
P.oneOf = function (list) { return this._assert(list.indexOf(this._o) >= 0, 'expected ' + inspect(this._o) + ' to be one of ' + inspect(list), 'expected ' + inspect(this._o) + ' not to be one of ' + inspect(list)); };
P.instanceof = P.instanceOf = function (c) { return this._assert(this._o instanceof c, 'expected value to be an instance of ' + (c && c.name), 'expected value not to be an instance of ' + (c && c.name)); };
P.property = function (name, value) {
  var has = this._o !== null && this._o !== undefined && Object.prototype.hasOwnProperty.call(Object(this._o), name);
  if (arguments.length > 1) {
    var ok = has && (this._deep ? deepEqual(this._o[name], value) : this._o[name] === value);
    return this._assert(ok, 'expected ' + inspect(this._o) + ' to have property ' + inspect(name) + ' of ' + inspect(value), 'expected ' + inspect(this._o) + ' not to have property ' + inspect(name) + ' of ' + inspect(value));
  }
  this._assert(has, 'expected ' + inspect(this._o) + ' to have property ' + inspect(name), 'expected ' + inspect(this._o) + ' not to have property ' + inspect(name));
  return new Assertion(this._o[name], false, this._deep);
};
P.keys = P.key = function () {
  var want = Array.prototype.concat.apply([], arguments).slice().sort(), have = Object.keys(this._o).sort();
  return this._assert(deepEqual(want, have), 'expected ' + inspect(have) + ' to have keys ' + inspect(want), 'expected ' + inspect(have) + ' not to have keys ' + inspect(want));
};
P.members = function (list) {
  var o = this._o;
  var ok = Array.isArray(o) && o.length === list.length && list.every(function (x) { return o.some(function (y) { return deepEqual(x, y); }); });
  return this._assert(ok, 'expected ' + inspect(o) + ' to have members ' + inspect(list), 'expected ' + inspect(o) + ' not to have members ' + inspect(list));
};
function lengthAssert(self, n) {
  var len = self._o == null ? undefined : self._o.length;
  return self._assert(len === n, 'expected ' + inspect(self._o) + ' to have length ' + n + ' but got ' + len, 'expected ' + inspect(self._o) + ' not to have length ' + n);
}
P.lengthOf = function (n) { return lengthAssert(this, n); };
Object.defineProperty(P, 'length', {get: function () { var self = this; var fn = function (n) { return lengthAssert(self, n); }; fn.above = function (n) { return new Assertion(self._o.length, self._n).above(n); }; fn.below = function (n) { return new Assertion(self._o.length, self._n).below(n); }; fn.within = function (a, b) { return new Assertion(self._o.length, self._n).within(a, b); }; fn.at = {least: function (n) { return new Assertion(self._o.length, self._n).least(n); }, most: function (n) { return new Assertion(self._o.length, self._n).most(n); }}; return fn; }});
P.throw = P.throws = function () {
  var threw = false;
  try { this._o(); } catch (e) { threw = true; }
  return this._assert(threw, 'expected function to throw', 'expected function not to throw');
};
function expect(v) { return new Assertion(v, false, false); }

// ---- request / response ------------------------------------------------------------------
var rq = ctx.request || null;
function Headers(list) { this._h = list; }
Headers.prototype.get = function (k) { k = String(k).toLowerCase(); for (var i = 0; i < this._h.length; i++) if (this._h[i].key.toLowerCase() === k) return this._h[i].value; return undefined; };
Headers.prototype.has = function (k) { return this.get(k) !== undefined; };
Headers.prototype.add = function (h) { this._h.push({key: String(h.key), value: String(h.value), enabled: true}); };
Headers.prototype.upsert = function (h) {
  var k = String(h.key).toLowerCase();
  for (var i = 0; i < this._h.length; i++) if (this._h[i].key.toLowerCase() === k) { this._h[i].value = String(h.value); this._h[i].enabled = true; return; }
  this.add(h);
};
Headers.prototype.remove = function (k) { k = String(k).toLowerCase(); this._h = this._h.filter(function (h) { return h.key.toLowerCase() !== k; }); };
Headers.prototype.all = function () { return this._h.slice(); };
Headers.prototype.toObject = function () { var o = {}; this._h.forEach(function (h) { o[h.key] = h.value; }); return o; };
Headers.prototype.each = function (fn) { this._h.forEach(fn); };

function Url(raw) { this.raw = raw; }
Url.prototype.toString = function () { return this.raw; };
Url.prototype.getHost = function () { var m = /^[a-z]+:\/\/([^\/?#]+)/i.exec(this.raw); return m ? m[1] : ''; };
Url.prototype.getPath = function () { var m = /^(?:[a-z]+:\/\/[^\/?#]+)?([^?#]*)/i.exec(this.raw); return m ? m[1] : ''; };
Url.prototype.getQueryString = function () { var i = this.raw.indexOf('?'); return i < 0 ? '' : this.raw.slice(i + 1).split('#')[0]; };

var request = null;
if (rq) {
  request = {method: rq.method, url: new Url(rq.url), headers: new Headers(rq.headers), body: {mode: rq.body_mode, raw: rq.body}};
}
var response = null;
if (ctx.response) {
  var rs = ctx.response, rbody = rs.body;
  var rh = new Headers(rs.headers);
  response = {
    code: rs.code, status: rs.status, responseTime: rs.responseTime, responseSize: rs.size, headers: rh,
    text: function () { return rbody; },
    json: function () { return JSON.parse(rbody); },
    reason: function () { return rs.status; }
  };
  function respAssert(cond, msg) { if (!cond) throw new AssertionError(msg); return true; }
  var hv = {
    status: function (v) { return typeof v === 'number' ? respAssert(rs.code === v, 'expected response to have status code ' + v + ' but got ' + rs.code) : respAssert(rs.status === v, 'expected response to have status reason ' + v + ' but got ' + rs.status); },
    header: function (k, v) { var got = rh.get(k); return respAssert(got !== undefined && (v === undefined || got === v), 'expected response to have header ' + k + (v === undefined ? '' : ' with value ' + v)); },
    body: function (v) { return respAssert(v instanceof RegExp ? v.test(rbody) : rbody.indexOf(v) >= 0, 'expected response body to contain ' + String(v)); },
    jsonBody: function (path, v) {
      var j; try { j = JSON.parse(rbody); } catch (e) { throw new AssertionError('expected response body to be JSON'); }
      if (path === undefined) return true;
      var cur = j; String(path).split('.').forEach(function (p) { cur = cur == null ? undefined : cur[p]; });
      return respAssert(v === undefined ? cur !== undefined : deepEqual(cur, v), 'expected JSON body to have ' + path + (v === undefined ? '' : ' = ' + inspect(v)));
    }
  };
  var be = {};
  var kinds = {ok: [200, 200], success: [200, 299], redirection: [300, 399], clientError: [400, 499], serverError: [500, 599], error: [400, 599]};
  Object.keys(kinds).forEach(function (n) {
    Object.defineProperty(be, n, {get: function () { return respAssert(rs.code >= kinds[n][0] && rs.code <= kinds[n][1], 'expected response to be ' + n + ' but got ' + rs.code); }});
  });
  [['accepted', 202], ['badRequest', 400], ['unauthorized', 401], ['forbidden', 403], ['notFound', 404], ['rateLimited', 429]].forEach(function (p) {
    Object.defineProperty(be, p[0], {get: function () { return respAssert(rs.code === p[1], 'expected response to be ' + p[0] + ' (' + p[1] + ') but got ' + rs.code); }});
  });
  Object.defineProperty(be, 'withBody', {get: function () { return respAssert(rbody.length > 0, 'expected response to have a body'); }});
  response.to = {have: hv, be: be};
  Object.defineProperty(response.to, 'not', {get: function () { return {have: {
    status: function (v) { try { hv.status(v); } catch (e) { return true; } throw new AssertionError('expected response not to have status ' + v); },
    header: function (k) { if (rh.get(k) === undefined) return true; throw new AssertionError('expected response not to have header ' + k); },
    body: function (v) { try { hv.body(v); } catch (e) { return true; } throw new AssertionError('expected response body not to contain ' + String(v)); }
  }}; }});
}

function test(name, fn) {
  var t = {name: String(name), passed: true, error: ''};
  try { if (typeof fn === 'function') fn(); } catch (e) { t.passed = false; t.error = (e && e.message) ? e.message : String(e); }
  tests.push(t);
}
var pm = {
  info: {eventName: ctx.kind === 'pre' ? 'prerequest' : 'test', iteration: ctx.info.iteration, iterationCount: ctx.info.iterationCount, requestName: ctx.info.requestName, requestId: ctx.info.requestId},
  environment: new Scope(environment), globals: new Scope(globals), collectionVariables: new Scope(collection),
  variables: variables, iterationData: new Scope(iteration),
  request: request, response: response, test: test, expect: expect,
  execution: {setNextRequest: function (n) { nextRequest = n === undefined ? null : n; }}
};
pm.iterationData.toObject = function () { return JSON.parse(JSON.stringify(iteration)); };
pm.sendRequest = function () { throw new Error('pm.sendRequest is not supported in Zapi scripts'); };
var postman = {
  setEnvironmentVariable: function (k, v) { pm.environment.set(k, v); },
  getEnvironmentVariable: function (k) { return pm.environment.get(k); },
  clearEnvironmentVariable: function (k) { pm.environment.unset(k); },
  setGlobalVariable: function (k, v) { pm.globals.set(k, v); },
  getGlobalVariable: function (k) { return pm.globals.get(k); },
  setNextRequest: function (n) { nextRequest = n === undefined ? null : n; }
};
var tests_legacy = legacyTests;
var responseBody = response ? response.text() : undefined;
var responseCode = response ? {code: response.code, name: response.status} : undefined;
var responseTime = response ? response.responseTime : undefined;
function btoa(s) { return __b.b64enc(String(s)); }
function atob(s) { return __b.b64dec(String(s)); }
function setTimeout(fn) { if (typeof fn === 'function') fn(); return 0; }
function clearTimeout() {}
function hex(fn) { return function (m, k) { var v = k === undefined ? fn(String(m)) : fn(String(m), String(k)); return {toString: function () { return v; }}; }; }
var CryptoJS = {
  MD5: hex(function (m) { return __b.digest('md5', m); }), SHA1: hex(function (m) { return __b.digest('sha1', m); }),
  SHA256: hex(function (m) { return __b.digest('sha256', m); }), SHA512: hex(function (m) { return __b.digest('sha512', m); }),
  HmacSHA256: hex(function (m, k) { return __b.hmac_('sha256', k, m); }), HmacSHA1: hex(function (m, k) { return __b.hmac_('sha1', k, m); }),
  HmacMD5: hex(function (m, k) { return __b.hmac_('md5', k, m); })
};
function require(name) {
  if (name === 'crypto-js') return CryptoJS;
  throw new Error("require('" + name + "') is not available. Supported: crypto-js");
}
var error = '';
var userScript = function (pm, console, postman, tests, CryptoJS, require, atob, btoa, setTimeout, clearTimeout, responseBody, responseCode, responseTime) {
/*__USER__*/
};
try { userScript(pm, console, postman, legacyTests, CryptoJS, require, atob, btoa, setTimeout, clearTimeout, responseBody, responseCode, responseTime); }
catch (e) { error = (e && e.message) ? e.message : String(e); }
Object.keys(legacyTests).forEach(function (k) { tests.push({name: k, passed: !!legacyTests[k], error: legacyTests[k] ? '' : 'assertion is false'}); });
var out = {error: error, tests: tests, logs: logs, environment: environment, globals: globals, collection: collection, variables: local, nextRequest: nextRequest};
if (request) {
  var u = request.url; var raw = typeof u === 'string' ? u : (u && u.raw !== undefined ? u.raw : String(u));
  out.request = {method: request.method, url: raw, headers: request.headers._h, body: request.body.raw};
}
return JSON.stringify(out);
})
"""


def _run_inprocess(script, ctx):
    """Evaluate in this process. Only the worker calls this: a runaway script cannot be interrupted from here
    (PySide keeps the GIL while JavaScript runs), so the parent kills the worker on timeout instead."""
    if QCoreApplication.instance() is None:
        QCoreApplication([])
    engine = QJSEngine()
    bridge = _Bridge()
    holder = engine.newQObject(bridge)
    fn = engine.evaluate(_SHIM.replace("/*__USER__*/", script), "zapi-script")
    if fn.isError():
        return _failed(ctx, _js_error(fn))
    out = fn.call([json.dumps(ctx), holder])
    if out.isError():
        return _failed(ctx, _js_error(out))
    try:
        data = json.loads(out.toString())
    except ValueError:
        return _failed(ctx, "The script returned no result.")
    return _from_dict(data)


def _from_dict(data):
    return ScriptResult(ok=not data.get("error"), error=data.get("error", ""), tests=data.get("tests", []),
                        logs=data.get("logs", []), environment=data.get("environment", {}),
                        globals=data.get("globals", {}), collection=data.get("collection", {}),
                        variables=data.get("variables", {}), request=data.get("request") or {},
                        next_request=data.get("nextRequest", "__unset__"))


def _to_dict(r):
    return {"ok": r.ok, "error": r.error, "tests": r.tests, "logs": r.logs, "environment": r.environment,
            "globals": r.globals, "collection": r.collection, "variables": r.variables, "request": r.request,
            "nextRequest": r.next_request}


# ---- worker process ---------------------------------------------------------------------------------
def worker_main():
    """Entry point of `Zapi.exe --script-worker`: one JSON request per line on stdin, one JSON answer on stdout."""
    stdin = open(0, "rb", closefd=False)
    stdout = open(1, "wb", closefd=False)
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line.decode("utf-8"))
            result = _to_dict(_run_inprocess(msg["script"], msg["ctx"]))
        except Exception as e:  # noqa: BLE001
            result = {"ok": False, "error": f"Script engine error: {e}", "tests": [], "logs": [], "environment": {},
                      "globals": {}, "collection": {}, "variables": {}, "request": {}, "nextRequest": "__unset__"}
        stdout.write((json.dumps(result) + "\n").encode("utf-8"))
        stdout.flush()
    return 0


class _Worker:
    def __init__(self):
        self.proc = None
        self.lines = None
        self.lock = threading.Lock()

    def _command(self):
        import sys
        if getattr(sys, "frozen", False):
            return [sys.executable, "--script-worker"]
        return [sys.executable, "-m", "zapi.script_worker"]

    def _start(self):
        import os
        import queue
        import subprocess
        import sys
        cwd = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONIOENCODING="utf-8")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.proc = subprocess.Popen(self._command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, cwd=None if getattr(sys, "frozen", False) else cwd,
                                     env=env, creationflags=flags)
        self.lines = queue.Queue()
        proc, q = self.proc, self.lines

        def pump():
            for raw in proc.stdout:
                q.put(raw)
            q.put(None)
        threading.Thread(target=pump, daemon=True).start()

    def stop(self):
        proc, self.proc = self.proc, None
        if proc is not None:
            try:
                proc.kill()
            except OSError:
                pass

    def run(self, script, ctx, timeout):
        import queue
        with self.lock:
            for attempt in (0, 1):
                if self.proc is None or self.proc.poll() is not None:
                    self._start()
                try:
                    self.proc.stdin.write((json.dumps({"script": script, "ctx": ctx}) + "\n").encode("utf-8"))
                    self.proc.stdin.flush()
                    # the first answer also pays the worker's start-up time
                    raw = self.lines.get(timeout=timeout + (8 if attempt == 0 and self._fresh() else 0))
                except queue.Empty:
                    self.stop()
                    return _failed(ctx, f"Script timed out after {timeout:g} s (infinite loop?)")
                except OSError:
                    self.stop()
                    continue
                if raw is None:           # worker died (crash); retry once with a new one
                    self.stop()
                    continue
                try:
                    return _from_dict(json.loads(raw.decode("utf-8")))
                except ValueError:
                    self.stop()
                    continue
            return _failed(ctx, "The script engine could not be started.")

    def _fresh(self):
        return getattr(self, "_used", False) is False and not setattr(self, "_used", True)


_worker = _Worker()


def shutdown():
    _worker.stop()


import atexit  # noqa: E402
atexit.register(shutdown)


def run_script(script, ctx, timeout=SCRIPT_TIMEOUT):
    """Execute `script` with context `ctx` (dict). Always returns a ScriptResult; never raises.
    Runs in a helper process so that an endless loop can be killed after `timeout` seconds."""
    if not (script or "").strip():
        return _passthrough(ctx)
    return _worker.run(script, ctx, timeout)


def _js_error(v):
    line = v.property("lineNumber").toInt()
    msg = v.property("message").toString() or v.toString()
    return msg if not line else f"{msg} (line {line})"


def _passthrough(ctx):
    return ScriptResult(environment=dict(ctx.get("environment") or {}), globals=dict(ctx.get("globals") or {}),
                        collection=dict(ctx.get("collection") or {}), variables=dict(ctx.get("variables") or {}))


def _failed(ctx, message):
    r = _passthrough(ctx)
    r.ok, r.error = False, message
    return r
