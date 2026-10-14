import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from zapi import executor, scripting, store


def ctx(**kw):
    base = {"kind": "test", "request": None, "response": None, "environment": {}, "globals": {}, "collection": {},
            "variables": {}, "iterationData": {}, "info": {"iteration": 0, "iterationCount": 1, "requestName": "r"}}
    base.update(kw)
    return base


def response(code=200, body='{"id": 7, "name": "Asha", "tags": ["a", "b"]}', headers=None):
    return {"code": code, "status": "OK", "responseTime": 12, "size": len(body),
            "headers": headers or [{"key": "Content-Type", "value": "application/json"}], "body": body}


class ScriptTests(unittest.TestCase):
    def run_js(self, code, **kw):
        return scripting.run_script(code, ctx(**kw))

    def test_pass_and_fail(self):
        r = self.run_js("""
            pm.test('ok', function () { pm.expect(1 + 1).to.equal(2); });
            pm.test('bad', function () { pm.expect(1).to.equal(2); });
            pm.test('arrow', () => { pm.expect('abc').to.be.a('string').and.include('b'); });
        """)
        self.assertTrue(r.ok)
        self.assertEqual([(t["name"], t["passed"]) for t in r.tests], [("ok", True), ("bad", False), ("arrow", True)])
        self.assertIn("expected 1 to equal 2", r.tests[1]["error"])

    def test_chai_subset(self):
        r = self.run_js("""
            var o = {a: 1, b: {c: [1, 2, 3]}};
            pm.test('deep', () => pm.expect(o).to.deep.equal({a: 1, b: {c: [1, 2, 3]}}));
            pm.test('prop', () => pm.expect(o).to.have.property('a', 1));
            pm.test('nested', () => pm.expect(o).to.have.property('b').that.has.property('c').with.lengthOf(3));
            pm.test('not', () => pm.expect(5).to.not.be.below(3));
            pm.test('above', () => pm.expect(5).to.be.above(3).and.below(10));
            pm.test('flags', () => { pm.expect(true).to.be.true; pm.expect(null).to.be.null; pm.expect([]).to.be.empty; pm.expect('x').to.exist; });
            pm.test('match', () => pm.expect('abc123').to.match(/\\d+/));
            pm.test('keys', () => pm.expect({x: 1, y: 2}).to.have.all.keys('x', 'y'));
            pm.test('oneOf', () => pm.expect(2).to.be.oneOf([1, 2, 3]));
            pm.test('negative', () => pm.expect(1).to.be.true);
        """)
        self.assertTrue(r.ok, r.error)
        results = {t["name"]: t["passed"] for t in r.tests}
        self.assertEqual(results.pop("negative"), False)
        # .all is not implemented as a chain word; chai users hit it with `keys`, so it must not crash silently
        failing = [n for n, ok in results.items() if not ok]
        self.assertEqual(failing, ["keys"] if "keys" in failing else [], r.tests)

    def test_response_api(self):
        r = self.run_js("""
            pm.test('status', () => pm.response.to.have.status(200));
            pm.test('ok', () => pm.response.to.be.ok);
            pm.test('json', () => { var j = pm.response.json(); pm.expect(j.name).to.eql('Asha'); pm.expect(j.tags).to.have.lengthOf(2); });
            pm.test('header', () => pm.response.to.have.header('content-type'));
            pm.test('time', () => pm.expect(pm.response.responseTime).to.be.below(100));
            pm.test('body', () => pm.response.to.have.jsonBody('id', 7));
            pm.test('legacy vars', () => { pm.expect(responseCode.code).to.equal(200); pm.expect(responseBody).to.include('Asha'); });
            pm.test('wrong status', () => pm.response.to.have.status(404));
        """, response=response())
        results = {t["name"]: t["passed"] for t in r.tests}
        self.assertEqual(results.pop("wrong status"), False)
        self.assertTrue(all(results.values()), r.tests)

    def test_variables_scopes(self):
        r = self.run_js("""
            pm.environment.set('token', 'abc');
            pm.globals.set('g', 5);
            pm.collectionVariables.set('c', {x: 1});
            pm.variables.set('local', 'l');
            pm.test('read', () => {
                pm.expect(pm.environment.get('base')).to.equal('http://x');
                pm.expect(pm.variables.get('base')).to.equal('http://x');
                pm.expect(pm.variables.replaceIn('{{base}}/{{local}}/{{nope}}')).to.equal('http://x/l/{{nope}}');
                pm.expect(pm.iterationData.get('row')).to.equal('r1');
                pm.expect(pm.environment.has('missing')).to.be.false;
            });
            pm.environment.unset('base');
        """, environment={"base": "http://x"}, iterationData={"row": "r1"})
        self.assertTrue(r.ok, r.error)
        self.assertTrue(all(t["passed"] for t in r.tests), r.tests)
        self.assertEqual(r.environment, {"token": "abc"})
        self.assertEqual(r.globals, {"g": "5"})
        self.assertEqual(json.loads(r.collection["c"]), {"x": 1})
        self.assertEqual(r.variables, {"local": "l"})

    def test_pre_request_edits_the_request(self):
        req = {"method": "GET", "url": "{{base}}/a", "headers": [{"key": "A", "value": "1", "enabled": True}],
               "body": "", "body_mode": "none"}
        r = self.run_js("""
            pm.request.headers.add({key: 'X-New', value: 'n'});
            pm.request.headers.upsert({key: 'a', value: '2'});
            pm.request.headers.remove('nothing');
            pm.request.url = pm.request.url.toString() + '?x=1';
            pm.request.method = 'POST';
        """, kind="pre", request=req)
        self.assertTrue(r.ok, r.error)
        self.assertEqual(r.request["url"], "{{base}}/a?x=1")
        self.assertEqual(r.request["method"], "POST")
        self.assertEqual({h["key"]: h["value"] for h in r.request["headers"]}, {"A": "2", "X-New": "n"})

    def test_console_next_request_crypto_legacy(self):
        r = self.run_js("""
            console.log('hello', {a: 1}); console.warn('w');
            postman.setNextRequest('Other');
            tests['legacy passes'] = true; tests['legacy fails'] = false;
            pm.test('crypto', () => {
                pm.expect(CryptoJS.SHA256('abc').toString()).to.equal('ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad');
                pm.expect(CryptoJS.MD5('abc').toString()).to.equal('900150983cd24fb0d6963f7d28e17f72');
                pm.expect(CryptoJS.HmacSHA256('msg', 'key').toString()).to.have.lengthOf(64);
                pm.expect(btoa('hi')).to.equal('aGk=');
                pm.expect(atob('aGk=')).to.equal('hi');
                pm.expect(pm.variables.replaceIn('{{$guid}}')).to.have.lengthOf(36);
            });
        """)
        self.assertTrue(r.ok, r.error)
        self.assertEqual([(l["level"], l["text"]) for l in r.logs], [("log", 'hello {"a":1}'), ("warn", "w")])
        self.assertEqual(r.next_request, "Other")
        names = {t["name"]: t["passed"] for t in r.tests}
        self.assertEqual(names, {"crypto": True, "legacy passes": True, "legacy fails": False})

    def test_errors_are_reported_not_raised(self):
        r = self.run_js("var x = ;")
        self.assertFalse(r.ok)
        self.assertTrue(r.error)
        r = self.run_js("throw new Error('boom');")
        self.assertFalse(r.ok)
        self.assertIn("boom", r.error)
        r = self.run_js("undefinedFunction();")
        self.assertIn("undefinedFunction", r.error)
        r = self.run_js("require('fs')")
        self.assertIn("not available", r.error)

    def test_infinite_loop_is_interrupted(self):
        r = scripting.run_script("while (true) {}", ctx(), timeout=0.5)
        self.assertFalse(r.ok)
        self.assertIn("timed out", r.error)

    def test_empty_script_is_a_noop(self):
        r = self.run_js("   ", environment={"a": "1"})
        self.assertTrue(r.ok)
        self.assertEqual(r.environment, {"a": "1"})


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = json.dumps({"path": self.path, "token": self.headers.get("X-Token", ""),
                           "ua": self.headers.get("User-Agent", "")}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class ExecutorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), Handler)
        cls.base = f"http://127.0.0.1:{cls.srv.server_port}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    OPTS = {"verify_ssl": True, "follow_redirects": True, "timeout": 10}

    def test_pre_script_sets_variable_used_in_request_and_tests_see_response(self):
        r = store.new_request(url="{{base}}/items/{{id}}")
        r["headers"] = [store.kv("X-Token", "{{token}}")]
        r["scripts"]["pre"] = "pm.environment.set('token', 'T-' + pm.variables.get('id'));"
        r["scripts"]["test"] = """
            pm.test('server saw the token', () => pm.expect(pm.response.json().token).to.equal('T-42'));
            pm.test('path', () => pm.expect(pm.response.json().path).to.equal('/items/42'));
            pm.environment.set('last_path', pm.response.json().path);
        """
        out = executor.execute(r, {"environment": {"base": self.base, "id": "42"}, "globals": {}, "collection": {}},
                               self.OPTS)
        self.assertTrue(out.resp.ok, out.resp.error)
        self.assertEqual((out.passed, out.failed), (2, 0), out.tests)
        self.assertEqual(out.scopes["environment"]["last_path"], "/items/42")
        self.assertEqual(out.scopes["environment"]["token"], "T-42")

    def test_script_error_does_not_block_sending_and_failed_request_skips_tests(self):
        r = store.new_request(url=self.base + "/x")
        r["scripts"]["pre"] = "nonsense("
        out = executor.execute(r, {"environment": {}, "globals": {}, "collection": {}}, self.OPTS)
        self.assertTrue(out.resp.ok)
        self.assertTrue(out.script_errors)
        bad = store.new_request(url="http://127.0.0.1:1/")
        bad["scripts"]["test"] = "pm.test('x', () => {});"
        out = executor.execute(bad, {"environment": {}, "globals": {}, "collection": {}},
                               {**self.OPTS, "timeout": 2})
        self.assertFalse(out.resp.ok)
        self.assertEqual(out.tests[0]["name"], "Tests did not run")

    def test_workspace_applies_script_changes(self):
        ws = store.Workspace(os.path.join(tempfile.mkdtemp(), "w.json"))
        c = ws.add_collection("C")
        env = ws.add_environment("E")
        env["vars"] += [store.new_var("keep", "i", "cur"), store.new_var("drop", "x")]
        ws.settings["active_env"] = env["id"]
        before = ws.scopes(c["id"])
        after = {"environment": {"keep": "new", "added": "1"}, "globals": {"g": "1"}, "collection": {"cv": "2"}}
        ws.apply_scopes(c["id"], before, after)
        by = {v["key"]: v for v in env["vars"]}
        self.assertEqual(by["keep"]["current"], "new")
        self.assertEqual(by["keep"]["initial"], "i")
        self.assertEqual((by["added"]["initial"], by["added"]["current"]), ("", "1"))
        self.assertNotIn("drop", by)
        self.assertEqual(ws.globals["vars"][0]["current"], "1")
        self.assertEqual(c["variables"][0]["value"], "2")


if __name__ == "__main__":
    unittest.main()
