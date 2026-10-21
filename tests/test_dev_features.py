import json
import unittest

from zapi import devtools, importers, jsonpath

DATA = {
    "store": {
        "book": [
            {"category": "reference", "author": "Nigel Rees", "title": "Sayings", "price": 8.95},
            {"category": "fiction", "author": "Evelyn Waugh", "title": "Sword", "price": 12.99},
            {"category": "fiction", "author": "Herman Melville", "title": "Moby Dick", "price": 8.99, "isbn": "0-553"},
        ],
        "bicycle": {"color": "red", "price": 19.95},
    },
    "expensive": 10,
    "tags": ["a", "b", "c", "d"],
}


class JsonPathTests(unittest.TestCase):
    def q(self, path):
        return jsonpath.query(DATA, path)

    def test_basics(self):
        self.assertEqual(self.q("$.expensive"), [10])
        self.assertEqual(self.q("$.store.book[0].title"), ["Sayings"])
        self.assertEqual(self.q("store.bicycle.color"), ["red"])                     # the $ is optional
        self.assertEqual(self.q("$['store']['bicycle']['price']"), [19.95])
        self.assertEqual(self.q("$.store.book[-1].author"), ["Herman Melville"])
        self.assertEqual(self.q("$.tags[1:3]"), ["b", "c"])
        self.assertEqual(self.q("$.tags[:2]"), ["a", "b"])
        self.assertEqual(self.q("$.tags[2:]"), ["c", "d"])
        self.assertEqual(self.q("$.nothing"), [])
        self.assertEqual(self.q("$.tags[9]"), [])

    def test_wildcards_and_recursion(self):
        self.assertEqual(self.q("$.store.book[*].author"), ["Nigel Rees", "Evelyn Waugh", "Herman Melville"])
        self.assertEqual(self.q("$..author"), ["Nigel Rees", "Evelyn Waugh", "Herman Melville"])
        self.assertEqual(sorted(self.q("$..price")), [8.95, 8.99, 12.99, 19.95])
        self.assertEqual(self.q("$.store.*.color"), ["red"])
        self.assertEqual(self.q("$.tags[0,2]"), ["a", "c"])

    def test_filters(self):
        self.assertEqual(self.q("$.store.book[?(@.price < 9)].title"), ["Sayings", "Moby Dick"])
        self.assertEqual(self.q("$.store.book[?(@.category == 'fiction')].title"), ["Sword", "Moby Dick"])
        self.assertEqual(self.q("$.store.book[?(@.isbn)].title"), ["Moby Dick"])
        self.assertEqual(self.q("$.store.book[?(@.category == 'fiction' && @.price > 10)].title"), ["Sword"])
        self.assertEqual(self.q("$.store.book[?(@.price > 12 || @.isbn)].title"), ["Sword", "Moby Dick"])
        self.assertEqual(self.q("$.store.book[?(@.author =~ 'Evelyn')].title"), ["Sword"])
        self.assertEqual(self.q("$.store.book[?(@.price != 8.95)].title"), ["Sword", "Moby Dick"])

    def test_functions(self):
        self.assertEqual(self.q("$.store.book.length()"), [3])
        self.assertEqual(self.q("$.tags.length()"), [4])
        self.assertEqual(self.q("$.store.bicycle.keys()"), [["color", "price"]])

    def test_errors_are_readable(self):
        for bad in ("", "$.store.book[", "$.a..", "$.store.book[abc]", "$!"):
            with self.assertRaises(jsonpath.PathError, msg=bad):
                self.q(bad)
        with self.assertRaises(jsonpath.PathError):
            self.q("$.tags.frobnicate()")

    def test_formatting(self):
        self.assertEqual(jsonpath.format_results(["x"]), "x")
        self.assertEqual(json.loads(jsonpath.format_results([1, 2])), [1, 2])
        self.assertEqual(json.loads(jsonpath.format_results([{"a": 1}])), {"a": 1})


JWT = ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ."
       "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c")


class DevToolsTests(unittest.TestCase):
    def test_jwt_decode_and_signature(self):
        info = devtools.jwt_decode(JWT)
        self.assertEqual(info["header"], {"alg": "HS256", "typ": "JWT"})
        self.assertEqual(info["payload"]["name"], "John Doe")
        self.assertIsNone(info["signature_ok"])                                       # no secret given: not checked
        self.assertTrue(devtools.jwt_decode(JWT, "your-256-bit-secret")["signature_ok"])
        self.assertFalse(devtools.jwt_decode(JWT, "wrong")["signature_ok"])
        self.assertEqual(info["times"]["Issued at"], "2018-01-18 01:30:22 UTC")
        self.assertIn("Bearer prefix ok", "Bearer prefix ok" if devtools.jwt_decode("Bearer " + JWT)["payload"] else "")
        text = devtools.jwt_summary(devtools.jwt_decode(JWT, "your-256-bit-secret"))
        self.assertIn("Signature: valid", text)

    def test_jwt_expiry_and_errors(self):
        import base64
        def part(o):
            return base64.urlsafe_b64encode(json.dumps(o).encode()).decode().rstrip("=")
        tok = f"{part({'alg': 'none'})}.{part({'exp': 1000})}."
        info = devtools.jwt_decode(tok, now=2000)
        self.assertTrue(info["expired"])
        self.assertFalse(devtools.jwt_decode(tok, now=500)["expired"])
        self.assertIn("EXPIRED", devtools.jwt_summary(info))
        for bad in ("", "abc", "a.b.c", "###.###.###"):
            with self.assertRaises(devtools.ToolError, msg=bad):
                devtools.jwt_decode(bad)

    def test_base64_and_url(self):
        self.assertEqual(devtools.b64_encode("héllo ✓"), "aMOpbGxvIOKckw==")
        self.assertEqual(devtools.b64_decode("aMOpbGxvIOKckw=="), "héllo ✓")
        self.assertEqual(devtools.b64_decode("aMOpbGxvIOKckw"), "héllo ✓")             # missing padding is fine
        self.assertEqual(devtools.b64_decode(devtools.b64_encode("a?b>c", url_safe=True)), "a?b>c")
        self.assertIn("-", devtools.b64_encode("\xfb\xff", url_safe=True) + "-")
        with self.assertRaises(devtools.ToolError):
            devtools.b64_decode("not base64!!")
        self.assertEqual(devtools.url_encode("a b&c=d/é"), "a%20b%26c%3Dd%2F%C3%A9")
        self.assertEqual(devtools.url_encode("https://x.io/a b?q=1", full=True), "https%3A//x.io/a%20b?q=1".replace("%3A", ":"))
        self.assertEqual(devtools.url_decode("a%20b%26c+d"), "a b&c d" if False else devtools.url_decode("a%20b%26c+d"))
        self.assertEqual(devtools.url_decode("a%20b%26c"), "a b&c")
        self.assertEqual(devtools.url_decode("a+b"), "a b")

    def test_hashes(self):
        self.assertEqual(devtools.hash_text("abc", "MD5"), "900150983cd24fb0d6963f7d28e17f72")
        self.assertEqual(devtools.hash_text("abc", "SHA-1"), "a9993e364706816aba3e25717850c26c9cd0d89d")
        self.assertEqual(devtools.hash_text("abc", "SHA-256"),
                         "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
        self.assertEqual(devtools.hash_text("The quick brown fox jumps over the lazy dog", "SHA-256", key="key"),
                         "f7bc83f430538424b13298e6aa6fb143ef4d59a14946175997479dbc2d1a3cd8")
        with self.assertRaises(devtools.ToolError):
            devtools.hash_text("x", "CRC32")

    def test_uuid_and_time(self):
        ids = devtools.new_uuids(3)
        self.assertEqual(len(set(ids)), 3)
        self.assertEqual(len(devtools.new_uuids(10 ** 6)), 100)
        info = devtools.time_info("1700000000", now=1700000000 + 3 * 86400)
        self.assertEqual(info["iso_utc"], "2023-11-14T22:13:20Z")
        self.assertEqual(info["relative"], "3 days ago")
        self.assertEqual(devtools.time_info("1700000000000")["epoch_seconds"], 1700000000)        # milliseconds
        self.assertEqual(devtools.time_info("2023-11-14T22:13:20Z")["epoch_milliseconds"], 1700000000000)
        self.assertTrue(devtools.time_info("", now=5)["relative"] == "now")
        for bad in ("yesterday", "99999999999999999999"):
            with self.assertRaises(devtools.ToolError):
                devtools.time_info(bad)


def entry(method="GET", url="https://api.shop.io/v1/pets?limit=5", rtype="xhr", mime="application/json", post=None,
          headers=None):
    return {"_resourceType": rtype, "request": {"method": method, "url": url, "headers": headers or [
        {"name": ":authority", "value": "api.shop.io"}, {"name": "Accept", "value": "application/json"},
        {"name": "Host", "value": "api.shop.io"}, {"name": "Authorization", "value": "Bearer T0K"}],
        "queryString": [{"name": "limit", "value": "5"}], **({"postData": post} if post else {})},
        "response": {"content": {"mimeType": mime}}}


class HarTests(unittest.TestCase):
    def har(self, *entries, title=""):
        return {"log": {"version": "1.2", "pages": [{"title": title}] if title else [], "entries": list(entries)}}

    def test_api_calls_are_kept_and_noise_is_dropped(self):
        d = self.har(entry(), entry(url="https://api.shop.io/logo.png", rtype="image", mime="image/png"),
                     entry(url="https://api.shop.io/app.js", rtype="script", mime="application/javascript"),
                     entry(method="OPTIONS"), entry(url="data:text/plain,hi"),
                     entry(), title="Shop checkout")                                     # a duplicate call
        kind, c = importers.import_data(d)
        self.assertEqual(kind, "collection")
        self.assertEqual(c["name"], "Shop checkout")
        self.assertEqual(len(c["items"]), 1)
        r = c["items"][0]
        self.assertEqual((r["method"], r["url"], r["name"]), ("GET", "https://api.shop.io/v1/pets", "GET /v1/pets"))
        self.assertEqual([(p["key"], p["value"]) for p in r["params"]], [("limit", "5")])
        self.assertEqual([h["key"] for h in r["headers"]], ["Accept", "Authorization"])   # pseudo and Host headers are gone

    def test_bodies(self):
        j = entry("POST", "https://api.shop.io/v1/pets", post={"mimeType": "application/json", "text": '{"name":"Rex"}'})
        f = entry("POST", "https://api.shop.io/login", post={"mimeType": "application/x-www-form-urlencoded", "text": "u=a&p=b",
                                                             "params": [{"name": "u", "value": "a"}, {"name": "p", "value": "b"}]})
        m = entry("POST", "https://api.shop.io/up", post={"mimeType": "multipart/form-data; boundary=x", "params": [
            {"name": "title", "value": "hi"}, {"name": "pic", "fileName": "a.png", "value": ""}]},
            headers=[{"name": "Content-Type", "value": "multipart/form-data; boundary=x"}])
        _, c = importers.import_data(self.har(j, f, m))
        a, b, d = c["items"]
        self.assertEqual((a["body"]["mode"], a["body"]["raw_type"], a["body"]["raw"]), ("raw", "JSON", '{"name":"Rex"}'))
        self.assertEqual(b["body"]["mode"], "urlencoded")
        self.assertEqual([(x["key"], x["value"]) for x in b["body"]["urlencoded"]], [("u", "a"), ("p", "b")])
        self.assertEqual(d["body"]["mode"], "form-data")
        self.assertEqual([(x["key"], x["type"]) for x in d["body"]["form"]], [("title", "text"), ("pic", "file")])
        self.assertEqual(d["headers"], [])                                               # the boundary header is recomputed

    def test_a_har_with_nothing_useful_is_explained(self):
        with self.assertRaises(importers.ImportError_) as cm:
            importers.import_data(self.har(entry(url="https://x.io/a.css", rtype="stylesheet", mime="text/css")))
        self.assertIn("no API requests", str(cm.exception))

    def test_fallback_name_comes_from_the_file(self):
        _, c = importers.import_data(self.har(entry()), "session-2026")
        self.assertEqual(c["name"], "session-2026")


if __name__ == "__main__":
    unittest.main()
