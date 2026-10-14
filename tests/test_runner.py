import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import xml.dom.minidom
from http.server import BaseHTTPRequestHandler, HTTPServer

from zapi import runner, store

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Handler(BaseHTTPRequestHandler):
    hits = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        Handler.hits.append(self.path)
        code = 500 if self.path.startswith("/fail") else 200
        body = json.dumps({"path": self.path, "n": len(Handler.hits)}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class RunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), Handler)
        cls.base = f"http://127.0.0.1:{cls.srv.server_port}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        Handler.hits.clear()
        self.tmp = tempfile.mkdtemp()

    OPTS = {"verify_ssl": True, "follow_redirects": True, "timeout": 10}

    def scopes(self):
        return {"environment": {"base": self.base}, "globals": {}, "collection": {}}

    def coll(self, *requests):
        c = store.new_collection("Demo")
        c["items"] = list(requests)
        return c

    def req(self, name, path, test="", pre="", folder=""):
        r = store.new_request(name, url="{{base}}" + path)
        r["scripts"]["test"], r["scripts"]["pre"], r["folder"] = test, pre, folder
        return r

    def test_basic_run_and_summary(self):
        c = self.coll(self.req("A", "/a", "pm.test('ok', () => pm.response.to.have.status(200));"),
                      self.req("B", "/fail", "pm.test('status is 200', () => pm.response.to.have.status(200));"))
        seen = []
        s = runner.run_collection(c, self.scopes(), self.OPTS, on_step=seen.append)
        self.assertEqual((s.requests, s.tests_total, s.tests_failed), (2, 2, 1))
        self.assertFalse(s.success)
        self.assertEqual(len(seen), 2)
        self.assertEqual(Handler.hits, ["/a", "/fail"])

    def test_variables_flow_between_requests(self):
        c = self.coll(self.req("first", "/one", "pm.environment.set('id', pm.response.json().n);"),
                      self.req("second", "/two/{{id}}", "pm.test('uses id', () => pm.expect(pm.response.json().path).to.equal('/two/1'));"))
        s = runner.run_collection(c, self.scopes(), self.OPTS)
        self.assertTrue(s.success, [st.tests for st in s.steps])
        self.assertEqual(s.scopes["environment"]["id"], "1")

    def test_iterations_and_data_files(self):
        c = self.coll(self.req("row", "/item/{{name}}"))
        csv_path = os.path.join(self.tmp, "d.csv")
        with open(csv_path, "w", encoding="utf-8") as f:
            f.write("name,other\nalpha,1\nbeta,2\ngamma,3\n")
        data = runner.load_iteration_data(csv_path)
        self.assertEqual(data[1], {"name": "beta", "other": "2"})
        runner.run_collection(c, self.scopes(), self.OPTS, data=data)
        self.assertEqual(Handler.hits, ["/item/alpha", "/item/beta", "/item/gamma"])
        Handler.hits.clear()
        json_path = os.path.join(self.tmp, "d.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump([{"name": "x"}, {"name": "y", "n": 5}], f)
        runner.run_collection(c, self.scopes(), self.OPTS, data=runner.load_iteration_data(json_path))
        self.assertEqual(Handler.hits, ["/item/x", "/item/y"])
        Handler.hits.clear()
        s = runner.run_collection(self.coll(self.req("n", "/n")), self.scopes(), self.OPTS, iterations=3)
        self.assertEqual(s.requests, 3)
        with self.assertRaises(ValueError):
            with open(json_path, "w", encoding="utf-8") as f:
                f.write('"nope"')
            runner.load_iteration_data(json_path)

    def test_iteration_data_visible_to_scripts(self):
        c = self.coll(self.req("r", "/x", "pm.test('data', () => pm.expect(pm.iterationData.get('name')).to.equal('alpha'));"))
        s = runner.run_collection(c, self.scopes(), self.OPTS, data=[{"name": "alpha"}])
        self.assertTrue(s.success, s.steps[0].tests)

    def test_set_next_request_jumps_and_stops(self):
        c = self.coll(self.req("one", "/1", "postman.setNextRequest('three');"),
                      self.req("two", "/2"),
                      self.req("three", "/3", "postman.setNextRequest(null);"),
                      self.req("four", "/4"))
        runner.run_collection(c, self.scopes(), self.OPTS)
        self.assertEqual(Handler.hits, ["/1", "/3"])

    def test_bail_folder_and_script_errors(self):
        c = self.coll(self.req("ok", "/ok", folder="A"), self.req("bad", "/fail", "pm.expect(1).to.equal(2)", folder="B"),
                      self.req("never", "/never", folder="B"))
        s = runner.run_collection(c, self.scopes(), self.OPTS, folder="B")
        self.assertEqual(Handler.hits, ["/fail", "/never"])
        Handler.hits.clear()
        s = runner.run_collection(c, self.scopes(), self.OPTS, bail=True, folder="B")
        # an assertion thrown outside pm.test() makes the test script fail, and --bail stops there
        self.assertEqual(s.requests, 1)
        self.assertTrue(s.stopped)
        c2 = self.coll(self.req("e", "/e", test="pm.test('t', () => { throw new Error('x'); });"), self.req("f", "/f"))
        s = runner.run_collection(c2, self.scopes(), self.OPTS, bail=True)
        self.assertEqual(s.requests, 1)
        self.assertTrue(s.stopped)

    def test_stop_callback(self):
        c = self.coll(self.req("a", "/a"), self.req("b", "/b"))
        s = runner.run_collection(c, self.scopes(), self.OPTS, should_stop=lambda: len(Handler.hits) >= 1)
        self.assertEqual(s.requests, 1)
        self.assertTrue(s.stopped)

    def test_reporters(self):
        c = self.coll(self.req("A", "/a", "pm.test('ok', () => {});pm.test('no', () => pm.expect(1).to.equal(2));"),
                      self.req("down", "/x"))
        c["items"][1]["url"] = "http://127.0.0.1:1/"
        s = runner.run_collection(c, self.scopes(), {**self.OPTS, "timeout": 2})
        j = json.loads(runner.report_json(s))
        self.assertEqual(j["stats"], {"requests": 2, "failedRequests": 1, "tests": 2, "failedTests": 1})
        doc = xml.dom.minidom.parseString(runner.report_junit(s))
        self.assertEqual(doc.documentElement.getAttribute("failures"), "1")
        self.assertEqual(doc.documentElement.getAttribute("errors"), "1")
        self.assertIn("✗ no", runner.format_step_cli(s.steps[0]))
        self.assertIn("ERROR", runner.format_step_cli(s.steps[1]))


class CliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        RunnerTests.setUpClass.__func__(cls)
        cls.tmp = tempfile.mkdtemp()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def cli(self, *args):
        p = subprocess.run([sys.executable, os.path.join(ROOT, "main.py"), "run", *args], capture_output=True,
                           text=True, encoding="utf-8", cwd=ROOT, timeout=120)
        return p.returncode, p.stdout, p.stderr

    def write(self, name, obj):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f)
        return path

    def postman(self, tests):
        return self.write("c.json", {"info": {"name": "CLI demo"}, "item": [
            {"name": "ping", "request": {"method": "GET", "url": "{{base}}/ping"},
             "event": [{"listen": "test", "script": {"exec": tests}}]}]})

    def test_exit_codes_and_reporters(self):
        env = self.write("e.json", {"name": "E", "values": [{"key": "base", "value": self.base}]})
        good = self.postman(["pm.test('status', () => pm.response.to.have.status(200));",
                             "pm.environment.set('seen', 'yes');"])
        out_json = os.path.join(self.tmp, "r.json")
        out_env = os.path.join(self.tmp, "env-out.json")
        code, out, err = self.cli(good, "-e", env, "--reporters", "cli,json", "--reporter-json-export", out_json,
                                  "--export-environment", out_env)
        self.assertEqual(code, 0, out + err)
        self.assertIn("✓ status", out)
        self.assertEqual(json.load(open(out_json))["stats"]["failedTests"], 0)
        exported = {v["key"]: v["value"] for v in json.load(open(out_env))["values"]}
        self.assertEqual(exported["seen"], "yes")
        bad = self.postman(["pm.test('status', () => pm.response.to.have.status(201));"])
        code, out, _ = self.cli(bad, "--env-var", f"base={self.base}")
        self.assertEqual(code, 1)
        self.assertIn("✗ status", out)

    def test_usage_errors_exit_2(self):
        self.assertEqual(self.cli(os.path.join(self.tmp, "missing.json"))[0], 2)
        self.assertEqual(self.cli()[0], 2)
        code, out, _ = subprocess.run([sys.executable, os.path.join(ROOT, "main.py"), "run", "--help"],
                                      capture_output=True, text=True, cwd=ROOT).returncode, "", ""
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
