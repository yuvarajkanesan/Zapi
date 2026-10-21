"""Collection runner: iterations, CSV / JSON data files, setNextRequest, reporters. Shared by the UI and the CLI."""
import copy
import csv
import json
import time
import xml.sax.saxutils as xml_escape
from dataclasses import dataclass, field

from . import executor, http_client, inherit

MAX_REQUESTS = 10000


@dataclass
class StepResult:
    iteration: int
    index: int
    name: str
    method: str
    url: str
    folder: str = ""
    status: int = 0
    reason: str = ""
    ms: int = 0
    size: int = 0
    error: str = ""
    tests: list = field(default_factory=list)
    logs: list = field(default_factory=list)
    script_errors: list = field(default_factory=list)

    @property
    def request_ok(self):
        return not self.error and self.status < 400

    @property
    def failed_tests(self):
        return [t for t in self.tests if not t["passed"]]

    @property
    def ok(self):
        return not self.error and not self.failed_tests and not self.script_errors


@dataclass
class RunSummary:
    name: str = ""
    steps: list = field(default_factory=list)
    duration_ms: int = 0
    scopes: dict = field(default_factory=dict)       # final variable scopes (after script changes)
    stopped: bool = False

    @property
    def requests(self):
        return len(self.steps)

    @property
    def failed_requests(self):
        return sum(1 for s in self.steps if s.error)

    @property
    def tests_total(self):
        return sum(len(s.tests) for s in self.steps)

    @property
    def tests_failed(self):
        return sum(len(s.failed_tests) for s in self.steps)

    @property
    def success(self):
        return all(s.ok for s in self.steps)


def load_iteration_data(path):
    """A CSV (header row = variable names) or a JSON array of objects -> list of dicts of strings."""
    with open(path, "rb") as f:
        raw = f.read().decode("utf-8-sig")
    if path.lower().endswith(".json") or raw.lstrip()[:1] in "[{":
        data = json.loads(raw)
        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
            raise ValueError("A JSON data file must be an array of objects.")
        return [{k: (v if isinstance(v, str) else json.dumps(v)) for k, v in r.items()} for r in data]
    rows = list(csv.DictReader(raw.splitlines()))
    if not rows:
        raise ValueError("The data file has no rows.")
    return [{(k or "").strip(): (v or "") for k, v in r.items() if k} for r in rows]


def select_requests(collection, folder=""):
    items = collection["items"]
    folder = (folder or "").strip("/")
    if folder:
        items = [r for r in items if r.get("folder", "") == folder or r.get("folder", "").startswith(folder + "/")]
    return items


def run_collection(collection, scopes, opts, iterations=1, data=None, delay_ms=0, bail=False, folder="",
                   on_step=None, should_stop=None):
    """Runs the requests in order. `scopes` = {"environment","globals","collection"} dicts, copied.
    `on_step(StepResult)` is called after every request. Returns a RunSummary."""
    summary = RunSummary(name=collection.get("name", ""))
    scopes = {k: dict(v) for k, v in scopes.items()}
    for v in collection.get("variables", []):
        if v.get("enabled", True) and v.get("key") and v["key"] not in scopes["collection"]:
            scopes["collection"][v["key"]] = v.get("value", "")
    items = select_requests(collection, folder)
    count = len(data) if data else max(1, int(iterations))
    jar = http_client.new_cookie_jar()
    started = time.perf_counter()
    executed = 0
    for it in range(count):
        row = data[it] if data else {}
        i = 0
        while i < len(items):
            if (should_stop and should_stop()) or executed >= MAX_REQUESTS:
                summary.stopped = True
                break
            req = items[i]
            out = executor.execute(inherit.effective(collection, req), scopes, opts, jar=jar, iteration_data=row,
                                   info={"iteration": it, "iterationCount": count})
            executed += 1
            scopes = out.scopes
            r = out.resp
            step = StepResult(it, i, req.get("name", ""), req["method"], r.url or req["url"],
                              req.get("folder", ""), r.status if r.ok else 0, r.reason, r.elapsed_ms, r.size,
                              "" if r.ok else r.error, out.tests, out.logs, out.script_errors)
            summary.steps.append(step)
            if on_step:
                on_step(step)
            if bail and not step.ok:
                summary.stopped = True
                break
            nxt = out.next_request
            if nxt == "__unset__":
                i += 1
            elif nxt is None:
                break
            else:
                j = next((k for k, x in enumerate(items) if x.get("name") == nxt or x.get("id") == nxt), None)
                if j is None:
                    step.script_errors.append(f"setNextRequest: no request named '{nxt}'")
                    i += 1
                else:
                    i = j
            if delay_ms:
                time.sleep(delay_ms / 1000)
        if summary.stopped:
            break
    summary.duration_ms = int((time.perf_counter() - started) * 1000)
    summary.scopes = scopes
    return summary


# ---- reporters -----------------------------------------------------------------------------------------
def report_json(summary):
    return json.dumps({
        "collection": summary.name, "durationMs": summary.duration_ms,
        "stats": {"requests": summary.requests, "failedRequests": summary.failed_requests,
                  "tests": summary.tests_total, "failedTests": summary.tests_failed},
        "executions": [{
            "iteration": s.iteration, "name": s.name, "folder": s.folder, "method": s.method, "url": s.url,
            "status": s.status, "reason": s.reason, "ms": s.ms, "size": s.size, "error": s.error,
            "tests": s.tests, "scriptErrors": s.script_errors,
        } for s in summary.steps]}, indent=2)


def report_junit(summary):
    q = xml_escape.quoteattr
    cases = []
    for s in summary.steps:
        label = (s.folder + "/" if s.folder else "") + s.name
        if s.error:
            cases.append(f'<testcase classname={q(summary.name)} name={q(label)} time="{s.ms / 1000:.3f}">'
                         f"<error message={q(s.error)}/></testcase>")
        for t in s.tests:
            body = "" if t["passed"] else f"<failure message={q(t.get('error', ''))}/>"
            cases.append(f'<testcase classname={q(summary.name + "." + label)} name={q(t["name"])} '
                         f'time="{s.ms / 1000:.3f}">{body}</testcase>')
        for e in s.script_errors:
            cases.append(f'<testcase classname={q(summary.name + "." + label)} name="script"><error message={q(e)}/></testcase>')
    failures = summary.tests_failed
    errors = summary.failed_requests + sum(len(s.script_errors) for s in summary.steps)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<testsuites name={q(summary.name)} tests="{len(cases)}" failures="{failures}" errors="{errors}" '
            f'time="{summary.duration_ms / 1000:.3f}">\n<testsuite name={q(summary.name)} tests="{len(cases)}" '
            f'failures="{failures}" errors="{errors}">\n' + "\n".join(cases) + "\n</testsuite>\n</testsuites>\n")


def format_step_cli(s, show_logs=True):
    lines = [f"→ {(s.folder + '/' if s.folder else '')}{s.name}"]
    if s.error:
        lines.append(f"  {s.method} {s.url}  ERROR: {s.error}")
    else:
        lines.append(f"  {s.method} {s.url}  [{s.status} {s.reason}, {s.size} B, {s.ms} ms]")
    if show_logs:
        for l in s.logs:
            lines.append(f"  console.{l['level']}: {l['text']}")
    for t in s.tests:
        lines.append(f"  {'✓' if t['passed'] else '✗'} {t['name']}" + ("" if t["passed"] else f" - {t['error']}"))
    for e in s.script_errors:
        lines.append(f"  ! script error: {e}")
    return "\n".join(lines)


def format_summary_cli(summary):
    ok = summary.requests - summary.failed_requests
    t = summary.tests_total
    return "\n".join([
        "-" * 52,
        f"  requests    {summary.requests:>5}   failed {summary.failed_requests}",
        f"  assertions  {t:>5}   failed {summary.tests_failed}",
        f"  duration    {summary.duration_ms / 1000:.2f} s",
        "-" * 52])


def env_scope_from_file(path):
    """Variables from a Postman / Zapi environment file as a name -> value dict."""
    from . import importers
    kind, env = importers.import_file(path)
    if kind != "environment":
        raise ValueError("That file is not an environment.")
    return {v["key"]: v.get("current", "") for v in env["vars"] if v.get("enabled", True) and v["key"]}


def deep_copy_scopes(scopes):
    return copy.deepcopy(scopes)
