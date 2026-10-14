import json
import unittest
from concurrent import futures

try:
    import grpc
    from google.protobuf import descriptor_pb2, descriptor_pool, message_factory
    from grpc_reflection.v1alpha import reflection
    HAVE_GRPC = True
except ImportError:                     # pragma: no cover
    HAVE_GRPC = False

from zapi import grpc_client


def build_service():
    fdp = descriptor_pb2.FileDescriptorProto(name="zapi_test/hello.proto", package="demo", syntax="proto3")
    req = fdp.message_type.add(name="HelloRequest")
    req.field.add(name="name", number=1, type=9, label=1)
    req.field.add(name="times", number=2, type=5, label=1)
    rep = fdp.message_type.add(name="HelloReply")
    rep.field.add(name="message", number=1, type=9, label=1)
    svc = fdp.service.add(name="Greeter")
    svc.method.add(name="SayHello", input_type=".demo.HelloRequest", output_type=".demo.HelloReply")
    svc.method.add(name="Count", input_type=".demo.HelloRequest", output_type=".demo.HelloReply", server_streaming=True)
    svc.method.add(name="Collect", input_type=".demo.HelloRequest", output_type=".demo.HelloReply",
                   client_streaming=True)
    svc.method.add(name="Chat", input_type=".demo.HelloRequest", output_type=".demo.HelloReply",
                   client_streaming=True, server_streaming=True)
    pool = descriptor_pool.Default()
    pool.Add(fdp)
    Req = message_factory.GetMessageClass(pool.FindMessageTypeByName("demo.HelloRequest"))
    Rep = message_factory.GetMessageClass(pool.FindMessageTypeByName("demo.HelloReply"))

    def say_hello(request, context):
        if request.name == "missing":
            context.abort(grpc.StatusCode.NOT_FOUND, "no such person")
        token = dict(context.invocation_metadata()).get("x-token", "")
        context.set_trailing_metadata((("x-trailer", "t1"),))
        return Rep(message=f"Hello {request.name} [{token}]")

    def count(request, context):
        for i in range(request.times):
            yield Rep(message=f"{request.name}-{i}")

    def collect(request_iterator, context):
        return Rep(message=",".join(r.name for r in request_iterator))

    def chat(request_iterator, context):
        for r in request_iterator:
            yield Rep(message=r.name.upper())

    des = Req.FromString
    ser = lambda m: m.SerializeToString()      # noqa: E731
    handlers = {
        "SayHello": grpc.unary_unary_rpc_method_handler(say_hello, des, ser),
        "Count": grpc.unary_stream_rpc_method_handler(count, des, ser),
        "Collect": grpc.stream_unary_rpc_method_handler(collect, des, ser),
        "Chat": grpc.stream_stream_rpc_method_handler(chat, des, ser),
    }
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    server.add_generic_rpc_handlers((grpc.method_handlers_generic_handler("demo.Greeter", handlers),))
    reflection.enable_server_reflection(("demo.Greeter", reflection.SERVICE_NAME), server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    return server, port


@unittest.skipUnless(HAVE_GRPC, "grpcio is not installed")
class GrpcTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server, cls.port = build_service()
        cls.target = f"127.0.0.1:{cls.port}"
        cls.catalog = grpc_client.list_services(cls.target, timeout=10)

    @classmethod
    def tearDownClass(cls):
        cls.server.stop(0)

    def call(self, method, body, **kw):
        return grpc_client.invoke(self.catalog, self.target, "demo.Greeter", method, body, timeout=10, **kw)

    def test_reflection_lists_services_and_kinds(self):
        self.assertEqual(list(self.catalog.services), ["demo.Greeter"])
        kinds = {m.name: m.kind for m in self.catalog.services["demo.Greeter"]}
        self.assertEqual(kinds, {"SayHello": "unary", "Count": "server stream", "Collect": "client stream",
                                 "Chat": "bidirectional stream"})
        self.assertEqual(self.catalog.method("demo.Greeter", "SayHello").input_type, "demo.HelloRequest")

    def test_unary_with_metadata_and_trailers(self):
        r = self.call("SayHello", '{"name": "Asha"}', metadata=[("X-Token", "abc")])
        self.assertTrue(r.ok, r.details)
        self.assertEqual(json.loads(r.messages[0]), {"message": "Hello Asha [abc]"})
        self.assertIn(("x-trailer", "t1"), r.trailers)

    def test_streaming_shapes(self):
        r = self.call("Count", '{"name": "n", "times": 3}')
        self.assertEqual([json.loads(m)["message"] for m in r.messages], ["n-0", "n-1", "n-2"])
        r = self.call("Collect", '[{"name": "a"}, {"name": "b"}]')
        self.assertEqual(json.loads(r.messages[0])["message"], "a,b")
        r = self.call("Chat", '[{"name": "x"}, {"name": "y"}]')
        self.assertEqual([json.loads(m)["message"] for m in r.messages], ["X", "Y"])

    def test_errors_are_results_not_exceptions(self):
        r = self.call("SayHello", '{"name": "missing"}')
        self.assertEqual((r.code, r.details), ("NOT_FOUND", "no such person"))
        with self.assertRaises(grpc_client.GrpcError):
            self.call("SayHello", "{nope")
        with self.assertRaises(grpc_client.GrpcError) as cm:
            self.call("SayHello", '{"bogus": 1}')
        self.assertIn("HelloRequest", str(cm.exception))
        with self.assertRaises(grpc_client.GrpcError):
            self.call("Collect", '{"name": "not-an-array"}')
        with self.assertRaises(grpc_client.GrpcError):
            self.catalog.method("demo.Greeter", "Nope")

    def test_sample_request_and_connection_errors(self):
        self.assertEqual(json.loads(grpc_client.sample_request(self.catalog, "demo.Greeter", "SayHello")),
                         {"name": "", "times": 0})
        self.assertEqual(json.loads(grpc_client.sample_request(self.catalog, "demo.Greeter", "Collect")),
                         [{"name": "", "times": 0}])
        with self.assertRaises(grpc_client.GrpcError) as cm:
            grpc_client.list_services("127.0.0.1:1", timeout=2)
        self.assertIn("Could not connect", str(cm.exception))
        with self.assertRaises(grpc_client.GrpcError):
            grpc_client.list_services("  ")


if __name__ == "__main__":
    unittest.main()
