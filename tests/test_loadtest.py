import json
import os
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["ZAPI_HOME"] = tempfile.mkdtemp(prefix="zapi-test-")

from zapi import loadtest, store  # noqa: E402


class Handler(BaseHTTPRequestHandler):
    hits = {}
    lock = threading.Lock()
    peak = [0, 0]

    def do_GET(self):
        with Handler.lock:
            Handler.hits[self.path] = Handler.hits.get(self.path, 0) + 1
            Handler.peak[0] += 1
            Handler.peak[1] = max(Handler.peak[1], Handler.peak[0])
        try:
            if self.path.startswith("/slow"):
                time.sleep(0.05)
            code = 500 if self.path.startswith("/boom") else 200
            body = json.dumps({"path": self.path, "x": self.headers.get("X-Row", "")}).encode()
            self.send_response(code)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        finally:
            with Handler.lock:
                Handler.peak[0] -= 1

    def log_message(self, *a):
        pass


def req(path, name=None, **kw):
    return store.new_request(name=name or path, url="{{base}}" + path, **kw)


class LoadTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.base = f"http://127.0.0.1:{cls.srv.server_port}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        Handler.hits.clear()
        Handler.peak[:] = [0, 0]
        self.scopes = {"environment": {"base": self.base}, "globals": {}, "collection": {}}
        self.opts = {"verify_ssl": True, "follow_redirects": True, "timeout": 5}

    def run_test(self, items, **cfg):
        t = loadtest.LoadTest(items, self.scopes, self.opts, loadtest.LoadConfig(**cfg))
        t.start()
        self.assertTrue(t.wait(30), "the load test did not finish")
        return t.snapshot(), t


class PureTests(unittest.TestCase):
    def test_percentile_nearest_rank(self):
        v = list(range(1, 101))
        self.assertEqual([loadtest.percentile(v, p) for p in (50, 90, 95, 99, 100)], [50, 90, 95, 99, 100])
        self.assertEqual(loadtest.percentile([], 95), 0)
        self.assertEqual(loadtest.percentile([7], 99), 7)

    def test_histogram_percentiles_match_the_sorted_list_version(self):
        import random
        rnd = random.Random(7)
        for size in (1, 2, 17, 1000):
            values = [rnd.choice([3, 5, 5, 8, 120, 121, 900]) for _ in range(size)]
            counts = {}
            for v in values:
                counts[v] = counts.get(v, 0) + 1
            ps = (50, 90, 95, 99, 100)
            self.assertEqual(loadtest.percentiles_from_counts(counts, ps),
                             [loadtest.percentile(sorted(values), p) for p in ps], size)
        self.assertEqual(loadtest.percentiles_from_counts({}, (50, 99)), [0, 0])

    def test_profiles(self):
        fixed = loadtest.LoadConfig("Fixed", 10, 20)
        self.assertEqual({loadtest.target_vus(fixed, t) for t in (0, 5, 19)}, {10})
        ramp = loadtest.LoadConfig("Ramp up", 10, 20)
        seq = [loadtest.target_vus(ramp, t) for t in (0, 2, 5, 10, 15)]
        self.assertEqual(seq[0], 1)
        self.assertEqual(seq[3:], [10, 10])
        self.assertEqual(seq, sorted(seq))
        spike = loadtest.LoadConfig("Spike", 50, 100)
        self.assertEqual([loadtest.target_vus(spike, t) for t in (0, 39, 40, 59, 60, 99)], [10, 10, 50, 50, 10, 10])
        self.assertEqual(loadtest.target_vus(loadtest.LoadConfig("Spike", 3, 10), 0), 1)       # never below one user

    def test_limits_are_enforced(self):
        c = loadtest.LoadConfig("Nonsense", 99999, 10 ** 9, -5).clamp()
        self.assertEqual((c.profile, c.vus, c.duration_s, c.think_ms),
                         ("Fixed", loadtest.MAX_VUS, loadtest.MAX_DURATION_S, 0))
        self.assertEqual(loadtest.LoadConfig("Fixed", 0, 0).clamp().vus, 1)

    def test_nothing_to_run(self):
        empty = {"environment": {}, "globals": {}, "collection": {}}
        with self.assertRaises(ValueError):
            loadtest.LoadTest([], empty, {}, loadtest.LoadConfig()).start()


class EngineTests(LoadTestBase):
    def test_fixed_users_hit_the_server_and_stats_add_up(self):
        s, _ = self.run_test([req("/ok", "ok"), req("/slow", "slow")], vus=5, duration_s=2)
        self.assertGreater(s.requests, 20)
        self.assertEqual(s.errors, 0)
        hits = sum(Handler.hits.values())
        self.assertLessEqual(s.requests, hits)                                    # every counted request reached the server
        self.assertLessEqual(hits - s.requests, 5)                                # only requests cut off at the end are not counted
        self.assertGreaterEqual(Handler.peak[1], 2)                               # users really ran in parallel
        self.assertLessEqual(Handler.peak[1], 5)
        self.assertTrue(s.min_ms <= s.p50 <= s.p90 <= s.p95 <= s.p99 <= s.max_ms)
        self.assertTrue(s.min_ms <= s.avg_ms <= s.max_ms)
        self.assertGreater(s.rps, 5)
        self.assertEqual(sum(b["count"] for b in s.buckets), s.requests)
        self.assertEqual({r["name"] for r in s.per_request}, {"ok", "slow"})
        slow = next(r for r in s.per_request if r["name"] == "slow")
        ok = next(r for r in s.per_request if r["name"] == "ok")
        self.assertGreater(slow["avg_ms"], ok["avg_ms"])                          # the 50 ms endpoint is slower
        self.assertGreaterEqual(slow["avg_ms"], 40)
        self.assertEqual(s.statuses.get("200"), s.requests)
        self.assertFalse(s.running)
        self.assertGreater(s.bytes, 0)

    def test_errors_are_counted_and_explained(self):
        s, _ = self.run_test([req("/ok"), req("/boom"), store.new_request(name="no url", url="")],
                             vus=3, duration_s=1)
        self.assertGreater(s.errors, 0)
        self.assertAlmostEqual(s.error_rate, s.errors / s.requests * 100)
        self.assertGreater(s.error_rate, 60)                                      # two of three requests fail
        messages = " ".join(m for m, _ in s.top_errors)
        self.assertIn("HTTP 500", messages)
        self.assertIn("500", s.statuses)
        self.assertIn("error", s.statuses)                                        # a request that could not be sent

    def test_think_time_slows_users_down(self):
        fast, _ = self.run_test([req("/ok")], vus=2, duration_s=1)
        slow, _ = self.run_test([req("/ok")], vus=2, duration_s=1, think_ms=250)
        self.assertLess(slow.requests, fast.requests / 3)
        self.assertLessEqual(slow.requests, 2 * 5)                                # 2 users, one request per 250 ms

    def test_ramp_up_starts_small(self):
        s, _ = self.run_test([req("/slow")], profile="Ramp up", vus=8, duration_s=4)
        early = sum(b["count"] for b in s.buckets if b["t"] < 1)
        late = sum(b["count"] for b in s.buckets if b["t"] >= 3)
        self.assertGreater(late, early)                                           # more users later: more requests per second
        self.assertEqual(s.buckets[0]["vus"], 1)
        self.assertEqual(max(b["vus"] for b in s.buckets), 8)

    def test_stop_ends_the_test_early_and_keeps_the_results(self):
        t = loadtest.LoadTest([req("/ok")], self.scopes, self.opts, loadtest.LoadConfig(vus=3, duration_s=60))
        t.start()
        time.sleep(0.6)
        started = time.perf_counter()
        t.stop()
        self.assertTrue(t.wait(10))
        self.assertLess(time.perf_counter() - started, 5)
        s = t.snapshot()
        self.assertGreater(s.requests, 0)
        self.assertLess(s.elapsed, 10)
        self.assertFalse(s.running)

    def test_data_rows_are_cycled_across_users(self):
        r = req("/ok")
        r["headers"] = [{"key": "X-Row", "value": "{{who}}", "enabled": True}]
        seen = set()
        real = loadtest.executor.execute

        def spy(request, scopes, opts, **kw):
            seen.add(kw["iteration_data"].get("who"))
            return real(request, scopes, opts, **kw)
        loadtest.executor.execute = spy
        try:
            t = loadtest.LoadTest([r], self.scopes, self.opts, loadtest.LoadConfig(vus=2, duration_s=1),
                                  data=[{"who": "a"}, {"who": "b"}, {"who": "c"}])
            t.start()
            t.wait(10)
        finally:
            loadtest.executor.execute = real
        self.assertEqual(seen, {"a", "b", "c"})

    def test_scripts_are_off_by_default(self):
        r = req("/ok")
        r["scripts"]["test"] = "pm.test('x', function(){ pm.expect(1).to.equal(1); });"
        calls = []
        real = loadtest.executor.execute

        def spy(request, scopes, opts, **kw):
            calls.append(kw["run_scripts"])
            return real(request, scopes, opts, **kw)
        loadtest.executor.execute = spy
        try:
            self.run_test([r], vus=1, duration_s=1)
        finally:
            loadtest.executor.execute = real
        self.assertTrue(calls and not any(calls))

    def test_reports(self):
        s, _ = self.run_test([req("/ok")], vus=2, duration_s=1)
        cfg = loadtest.LoadConfig(vus=2, duration_s=1)
        data = json.loads(loadtest.report_json("Shop", cfg, s))
        self.assertEqual(data["summary"]["requests"], s.requests)
        self.assertEqual(data["config"]["virtualUsers"], 2)
        self.assertEqual(len(data["perSecond"]), len(s.buckets))
        csv_text = loadtest.report_csv(s)
        self.assertTrue(csv_text.startswith("second,requests,avg_ms,errors,virtual_users"))
        self.assertEqual(len(csv_text.strip().splitlines()), 1 + len(s.buckets))
        self.assertIn("Load test: Shop", loadtest.format_summary("Shop", cfg, s))


if __name__ == "__main__":
    unittest.main()
