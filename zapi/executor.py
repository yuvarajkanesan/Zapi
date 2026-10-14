"""Runs one request the way Zapi does everywhere (UI, runner, CLI): pre-request script, send, test script."""
import copy
from dataclasses import dataclass, field

from . import http_client, scripting, variables


@dataclass
class ExecResult:
    resp: http_client.Response
    tests: list = field(default_factory=list)
    logs: list = field(default_factory=list)
    script_errors: list = field(default_factory=list)
    next_request: object = "__unset__"
    scopes: dict = field(default_factory=dict)        # environment / globals / collection after the scripts ran

    @property
    def passed(self):
        return sum(1 for t in self.tests if t["passed"])

    @property
    def failed(self):
        return sum(1 for t in self.tests if not t["passed"])


def _rows(rows):
    return [{"key": r["key"], "value": r.get("value", ""), "enabled": r.get("enabled", True)}
            for r in rows if r.get("enabled", True) and r.get("key")]


def _context(kind, req, resp, scopes, local, iteration_data, info):
    body = req.get("body") or {}
    ctx = {
        "kind": kind,
        "request": {"method": req["method"], "url": req["url"], "headers": _rows(req.get("headers", [])),
                    "body": body.get("raw", "") if body.get("mode") == "raw" else "", "body_mode": body.get("mode", "none")},
        "environment": scopes["environment"], "globals": scopes["globals"], "collection": scopes["collection"],
        "variables": local, "iterationData": iteration_data,
        "info": {"iteration": info.get("iteration", 0), "iterationCount": info.get("iterationCount", 1),
                 "requestName": req.get("name", ""), "requestId": req.get("id", "")},
        "response": None,
    }
    if resp is not None:
        ctx["response"] = {"code": resp.status, "status": resp.reason, "responseTime": resp.elapsed_ms,
                           "size": resp.size, "headers": [{"key": k, "value": v} for k, v in resp.headers],
                           "body": resp.text()}
    return ctx


def _absorb(result, scopes, local, sink):
    scopes["environment"], scopes["globals"], scopes["collection"] = (
        result.environment, result.globals, result.collection)
    local.clear()
    local.update(result.variables)
    sink.logs.extend(result.logs)
    if not result.ok:
        sink.script_errors.append(result.error)
    if result.next_request != "__unset__":
        sink.next_request = result.next_request


def execute(req, scopes, opts, jar=None, iteration_data=None, info=None, run_scripts=True):
    """`scopes` = {"environment": {}, "globals": {}, "collection": {}} of plain name -> value dicts.
    Returns an ExecResult; `result.scopes` holds the (possibly script-modified) scopes."""
    info = info or {}
    scopes = {k: dict(v) for k, v in scopes.items()}
    local = {}
    iteration_data = dict(iteration_data or {})
    out = ExecResult(resp=http_client.Response())
    req = copy.deepcopy(req)
    scripts = req.get("scripts") or {}

    if run_scripts and (scripts.get("pre") or "").strip():
        pre = scripting.run_script(scripts["pre"], _context("pre", req, None, scopes, local, iteration_data, info))
        _absorb(pre, scopes, local, out)
        if pre.request:
            req["method"] = pre.request.get("method", req["method"])
            req["url"] = pre.request.get("url", req["url"])
            req["headers"] = [{"key": h["key"], "value": h["value"], "enabled": h.get("enabled", True)}
                              for h in pre.request.get("headers", [])]
            if (req.get("body") or {}).get("mode") == "raw":
                req["body"]["raw"] = pre.request.get("body", req["body"]["raw"])

    values = variables.merge_scopes(scopes["globals"], scopes["collection"], scopes["environment"],
                                    iteration_data, local)
    out.resp = http_client.send(req, values, **opts, jar=jar)

    if run_scripts and (scripts.get("test") or "").strip():
        if out.resp.ok:
            res = scripting.run_script(scripts["test"],
                                       _context("test", req, out.resp, scopes, local, iteration_data, info))
            _absorb(res, scopes, local, out)
            out.tests.extend(res.tests)
            if not res.ok:
                out.tests.append({"name": "Test script error", "passed": False, "error": res.error})
        else:
            out.tests.append({"name": "Tests did not run", "passed": False, "error": out.resp.error})
    out.scopes = scopes
    return out
