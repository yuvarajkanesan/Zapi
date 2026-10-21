"""Load testing: many virtual users (VUs) run a collection's requests in a loop for a set time, following a load profile.
Pure Python threads + the same executor as everywhere else, so it is testable without a UI. Shared by the dialog and the CLI.

Only point this at servers you own or have permission to test."""
import copy
import json
import math
import threading
import time
from urllib.parse import urlparse
from collections import Counter
from dataclasses import dataclass, field

from . import executor, http_client, runner, variables

MAX_VUS = 500
MAX_DURATION_S = 3600
PROFILES = ("Fixed", "Ramp up", "Spike")
PROFILE_HELP = {
    "Fixed": "All virtual users run for the whole duration.",
    "Ramp up": "Starts with one user and adds users steadily until the maximum is reached halfway, then holds.",
    "Spike": "A quiet baseline (a fifth of the users), a sudden burst of all users in the middle, then back to baseline.",
}


@dataclass
class LoadConfig:
    profile: str = "Fixed"
    vus: int = 10
    duration_s: int = 30
    think_ms: int = 0                 # pause between a user's requests
    run_scripts: bool = False         # pre-request / test scripts (each runs in a helper process: much slower)

    def clamp(self):
        self.vus = max(1, min(int(self.vus), MAX_VUS))
        self.duration_s = max(1, min(int(self.duration_s), MAX_DURATION_S))
        self.think_ms = max(0, int(self.think_ms))
        if self.profile not in PROFILES:
            self.profile = "Fixed"
        return self


def target_vus(cfg, t):
    """How many users should be active `t` seconds into the test."""
    t = max(0.0, t)
    if cfg.profile == "Ramp up":
        ramp = max(1.0, cfg.duration_s / 2)
        return max(1, min(cfg.vus, math.ceil(1 + (cfg.vus - 1) * min(1.0, t / ramp))))
    if cfg.profile == "Spike":
        base = max(1, cfg.vus // 5)
        return cfg.vus if 0.4 * cfg.duration_s <= t < 0.6 * cfg.duration_s else base
    return cfg.vus


def percentile(sorted_values, p):
    """Nearest-rank percentile of an already sorted list (0 when empty)."""
    if not sorted_values:
        return 0
    k = max(1, math.ceil(p / 100 * len(sorted_values)))
    return sorted_values[min(k, len(sorted_values)) - 1]


def percentiles_from_counts(counts, ps):
    """Nearest-rank percentiles from {ms: how many}. Memory stays small however long the test runs."""
    total = sum(counts.values())
    if not total:
        return [0] * len(ps)
    wanted = sorted((max(1, math.ceil(p / 100 * total)), i) for i, p in enumerate(ps))
    out, seen, w = [0] * len(ps), 0, 0
    for ms in sorted(counts):
        seen += counts[ms]
        while w < len(wanted) and wanted[w][0] <= seen:
            out[wanted[w][1]] = ms
            w += 1
        if w == len(wanted):
            break
    return out


@dataclass
class Stats:
    elapsed: float = 0.0
    running: bool = False
    active_vus: int = 0
    requests: int = 0
    errors: int = 0
    bytes: int = 0
    min_ms: int = 0
    avg_ms: float = 0.0
    max_ms: int = 0
    p50: int = 0
    p90: int = 0
    p95: int = 0
    p99: int = 0
    rps: float = 0.0
    buckets: list = field(default_factory=list)       # [{"t", "count", "avg_ms", "errors", "vus"}] one per second
    per_request: list = field(default_factory=list)   # [{"name", "method", "count", "errors", "avg_ms", "p95", "max_ms"}]
    statuses: dict = field(default_factory=dict)
    top_errors: list = field(default_factory=list)    # [(message, count)]

    @property
    def error_rate(self):
        return self.errors / self.requests * 100 if self.requests else 0.0


class LoadTest:
    def __init__(self, items, scopes, opts, cfg, data=None):
        self.items = list(items)
        self.base_scopes = {k: dict(v) for k, v in scopes.items()}
        self.opts = opts
        self.cfg = cfg.clamp()
        self.data = data or []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._threads = []
        self._started = 0.0
        self._finished = 0.0
        self._lat = Counter()                           # response time in ms -> how many (a histogram: bounded memory)
        self._sum_ms = 0
        self._bytes = 0
        self._errors = 0
        self._buckets = {}                              # second -> [count, sum_ms, errors, vus]
        self._req = {}                                  # index -> [name, method, [ms...], errors]
        self._status = Counter()
        self._err_text = Counter()
        self._row = 0

    # ---- control ----
    def start(self):
        if not self.items:
            raise ValueError("There are no requests to run.")
        self._started = time.perf_counter()
        for n in range(self.cfg.vus):
            th = threading.Thread(target=self._vu, args=(n,), name=f"zapi-vu-{n}", daemon=True)
            self._threads.append(th)
            th.start()
        threading.Thread(target=self._watch, daemon=True).start()

    def stop(self):
        self._stop.set()

    @property
    def running(self):
        return any(t.is_alive() for t in self._threads) and not self._finished

    def wait(self, timeout=None):
        end = None if timeout is None else time.perf_counter() + timeout
        while self.running:
            if end is not None and time.perf_counter() > end:
                return False
            time.sleep(0.02)
        return True

    # ---- the virtual users ----
    def _elapsed(self):
        return time.perf_counter() - self._started

    def _watch(self):
        """Ends the test when the duration is over, and records how many users were active every second."""
        while not self._stop.is_set() and self._elapsed() < self.cfg.duration_s:
            time.sleep(0.05)
        self._stop.set()
        for t in list(self._threads):
            t.join(timeout=max(5.0, float(self.opts.get("timeout", 30)) + 2))
        self._finished = self._elapsed()

    def _next_row(self):
        if not self.data:
            return {}
        with self._lock:
            row = self.data[self._row % len(self.data)]
            self._row += 1
        return row

    def _vu(self, n):
        scopes = {k: dict(v) for k, v in self.base_scopes.items()}
        jar = http_client.new_cookie_jar()
        iteration = 0
        while not self._stop.is_set():
            if n >= target_vus(self.cfg, self._elapsed()):
                self._stop.wait(0.1)                    # not active yet (ramp / spike): idle
                continue
            row = self._next_row()
            for i, req in enumerate(self.items):
                if self._stop.is_set() or n >= target_vus(self.cfg, self._elapsed()):
                    break
                out = executor.execute(req, scopes, self.opts, jar=jar, iteration_data=row,
                                       info={"iteration": iteration, "iterationCount": 0},
                                       run_scripts=self.cfg.run_scripts)
                if self._stop.is_set():
                    return                              # the test is over: do not count a request that was cut off
                if self.cfg.run_scripts:
                    scopes = out.scopes
                self._record(i, req, out.resp)
                if self.cfg.think_ms:
                    self._stop.wait(self.cfg.think_ms / 1000)
            iteration += 1

    def _record(self, index, req, resp):
        ok = resp.ok and resp.status < 400
        ms = int(resp.elapsed_ms)
        sec = int(self._elapsed())
        with self._lock:
            self._lat[ms] += 1
            self._sum_ms += ms
            self._bytes += resp.size or 0
            if not ok:
                self._errors += 1
                self._err_text[resp.error or f"HTTP {resp.status} {resp.reason}".strip()] += 1
            self._status[str(resp.status) if resp.ok else "error"] += 1
            b = self._buckets.setdefault(sec, [0, 0, 0, 0])
            b[0] += 1
            b[1] += ms
            b[2] += 0 if ok else 1
            b[3] = max(b[3], target_vus(self.cfg, sec))
            r = self._req.setdefault(index, [req.get("name", ""), req["method"], Counter(), 0, 0])
            r[2][ms] += 1
            r[3] += 0 if ok else 1
            r[4] += ms

    # ---- results ----
    def snapshot(self):
        with self._lock:
            lat, total_ms = dict(self._lat), self._sum_ms
            reqs = {k: (v[0], v[1], dict(v[2]), v[3], v[4]) for k, v in self._req.items()}
            buckets = dict(self._buckets)
            statuses, errs = dict(self._status), self._err_text.most_common(5)
            errors, size = self._errors, self._bytes
        elapsed = (self._finished or self._elapsed()) if self._started else 0.0
        s = Stats(elapsed=elapsed, running=self.running, active_vus=min(
            self.cfg.vus, target_vus(self.cfg, elapsed)) if self.running else 0)
        n = sum(lat.values())
        s.requests, s.errors, s.bytes = n, errors, size
        if n:
            s.min_ms, s.max_ms, s.avg_ms = min(lat), max(lat), total_ms / n
            s.p50, s.p90, s.p95, s.p99 = percentiles_from_counts(lat, (50, 90, 95, 99))
            s.rps = n / max(elapsed, 0.001)
        s.buckets = [{"t": t, "count": b[0], "avg_ms": round(b[1] / b[0], 1) if b[0] else 0, "errors": b[2], "vus": b[3]}
                     for t, b in sorted(buckets.items())]
        for _, (name, method, counts, e, total) in sorted(reqs.items()):
            c = sum(counts.values())
            s.per_request.append({"name": name, "method": method, "count": c, "errors": e,
                                  "avg_ms": round(total / c, 1) if c else 0,
                                  "p95": percentiles_from_counts(counts, (95,))[0], "max_ms": max(counts) if counts else 0})
        s.statuses, s.top_errors = statuses, errs
        return s


# ---- reporting ---------------------------------------------------------------------------------------------
def report_json(name, cfg, stats):
    return json.dumps({
        "collection": name,
        "config": {"profile": cfg.profile, "virtualUsers": cfg.vus, "durationSeconds": cfg.duration_s,
                   "thinkTimeMs": cfg.think_ms, "scripts": cfg.run_scripts},
        "summary": {"elapsedSeconds": round(stats.elapsed, 2), "requests": stats.requests, "errors": stats.errors,
                    "errorRatePercent": round(stats.error_rate, 2), "requestsPerSecond": round(stats.rps, 2),
                    "bytes": stats.bytes, "responseTimeMs": {"min": stats.min_ms, "avg": round(stats.avg_ms, 1),
                                                             "p50": stats.p50, "p90": stats.p90, "p95": stats.p95,
                                                             "p99": stats.p99, "max": stats.max_ms}},
        "statuses": stats.statuses, "topErrors": [{"message": m, "count": c} for m, c in stats.top_errors],
        "perRequest": stats.per_request, "perSecond": stats.buckets}, indent=2)


def report_csv(stats):
    lines = ["second,requests,avg_ms,errors,virtual_users"]
    lines += [f"{b['t']},{b['count']},{b['avg_ms']},{b['errors']},{b['vus']}" for b in stats.buckets]
    return "\n".join(lines) + "\n"


def format_summary(name, cfg, stats):
    lines = [f"Load test: {name}  ({cfg.profile}, up to {cfg.vus} users, {cfg.duration_s} s)",
             f"  requests      {stats.requests}  ({stats.rps:.1f}/s)",
             f"  errors        {stats.errors}  ({stats.error_rate:.1f}%)",
             f"  response time min {stats.min_ms} · avg {stats.avg_ms:.0f} · p50 {stats.p50} · p90 {stats.p90} · "
             f"p95 {stats.p95} · p99 {stats.p99} · max {stats.max_ms} ms"]
    for r in stats.per_request:
        lines.append(f"    {r['method']:7} {r['name'][:40]:40} {r['count']:>6}  err {r['errors']:<4} "
                     f"avg {r['avg_ms']:.0f}  p95 {r['p95']}  max {r['max_ms']} ms")
    for msg, n in stats.top_errors:
        lines.append(f"  ! {n} × {msg}")
    return "\n".join(lines)


def build_items(collection, folder=""):
    """The requests a load test cycles through (deep copies, so scripts never touch the workspace)."""
    return [copy.deepcopy(r) for r in runner.select_requests(collection, folder)]


def hosts_of(items, values):
    """The hosts a test would hit, with variables filled in (for the confirmation)."""
    out = []
    for r in items:
        try:
            host = urlparse(variables.substitute(r.get("url", ""), values)).hostname or ""
        except ValueError:
            host = ""
        if host and host not in out:
            out.append(host)
    return out


def with_collection_variables(collection, scopes):
    scopes = {k: dict(v) for k, v in scopes.items()}
    for v in collection.get("variables", []):
        if v.get("enabled", True) and v.get("key") and v["key"] not in scopes["collection"]:
            scopes["collection"][v["key"]] = v.get("value", "")
    return scopes
