"""gRPC calls without .proto files: services are discovered through server reflection and invoked with JSON.

Needs grpcio, grpcio-reflection and protobuf (bundled in the installer). Supports unary, server-streaming,
client-streaming and bidirectional-streaming methods; streams are collected and shown as a list of messages.
"""
import json
import time
from dataclasses import dataclass, field


class GrpcError(Exception):
    pass


def available():
    try:
        import grpc  # noqa: F401
        import grpc_reflection  # noqa: F401
        from google.protobuf import json_format  # noqa: F401
        return True
    except ImportError:
        return False


def _need():
    if not available():
        raise GrpcError("gRPC support needs the grpcio, grpcio-reflection and protobuf packages "
                        "(pip install grpcio grpcio-reflection protobuf).")


@dataclass
class MethodInfo:
    service: str
    name: str
    input_type: str
    output_type: str
    client_streaming: bool
    server_streaming: bool

    @property
    def path(self):
        return f"/{self.service}/{self.name}"

    @property
    def kind(self):
        return {(False, False): "unary", (False, True): "server stream", (True, False): "client stream",
                (True, True): "bidirectional stream"}[(self.client_streaming, self.server_streaming)]


@dataclass
class Catalog:
    pool: object
    services: dict = field(default_factory=dict)       # service name -> [MethodInfo]

    def method(self, service, name):
        for m in self.services.get(service, []):
            if m.name == name:
                return m
        raise GrpcError(f"Unknown method {service}/{name}")


@dataclass
class InvokeResult:
    code: str = "OK"
    details: str = ""
    messages: list = field(default_factory=list)       # JSON strings
    trailers: list = field(default_factory=list)       # [(key, value)]
    elapsed_ms: int = 0

    @property
    def ok(self):
        return self.code == "OK"


def _channel(target, tls):
    import grpc
    target = target.replace("grpc://", "").replace("grpcs://", "").replace("http://", "").replace("https://", "")
    if tls:
        return grpc.secure_channel(target, grpc.ssl_channel_credentials())
    return grpc.insecure_channel(target)


def _md(metadata):
    return [(k.lower(), v) for k, v in (metadata or []) if k]


def _rpc_message(e):
    code = e.code().name if hasattr(e, "code") and e.code() else "UNKNOWN"
    return code, (e.details() if hasattr(e, "details") else str(e)) or ""


def list_services(target, tls=False, metadata=None, timeout=10):
    """Returns a Catalog. Raises GrpcError with a readable message when reflection is not available."""
    _need()
    import grpc
    from google.protobuf import descriptor_pb2, descriptor_pool
    from grpc_reflection.v1alpha import reflection_pb2, reflection_pb2_grpc
    if not target.strip():
        raise GrpcError("Enter the server address, for example localhost:50051")
    channel = _channel(target.strip(), tls)
    try:
        stub = reflection_pb2_grpc.ServerReflectionStub(channel)

        def ask(**kw):
            stream = stub.ServerReflectionInfo(iter([reflection_pb2.ServerReflectionRequest(**kw)]),
                                               timeout=timeout, metadata=_md(metadata))
            return next(iter(stream))

        try:
            names = [s.name for s in ask(list_services="").list_services_response.service]
        except grpc.RpcError as e:
            code, details = _rpc_message(e)
            if code == "UNIMPLEMENTED":
                raise GrpcError("This server does not offer reflection, so its services cannot be listed.")
            if code in ("UNAVAILABLE", "DEADLINE_EXCEEDED"):
                raise GrpcError(f"Could not connect to {target}: {details}")
            raise GrpcError(f"{code}: {details}")
        pool = descriptor_pool.DescriptorPool()
        protos = {}

        def fetch(response):
            for raw in response.file_descriptor_response.file_descriptor_proto:
                fdp = descriptor_pb2.FileDescriptorProto.FromString(raw)
                protos[fdp.name] = fdp

        for n in names:
            fetch(ask(file_containing_symbol=n))
        added = set()

        def add(name):
            if name in added:
                return
            if name not in protos:
                fetch(ask(file_by_filename=name))
            fdp = protos.get(name)
            if fdp is None:
                return
            added.add(name)
            for dep in fdp.dependency:
                add(dep)
            pool.Add(fdp)

        for name in list(protos):
            add(name)
        catalog = Catalog(pool)
        for n in names:
            if n.startswith("grpc.reflection."):
                continue
            sd = pool.FindServiceByName(n)
            catalog.services[n] = [MethodInfo(n, m.name, m.input_type.full_name, m.output_type.full_name,
                                              m.client_streaming, m.server_streaming) for m in sd.methods]
        return catalog
    finally:
        channel.close()


def _json_dump(msg):
    from google.protobuf import json_format
    try:
        text = json_format.MessageToJson(msg, always_print_fields_with_no_presence=True,
                                         preserving_proto_field_name=True)
    except TypeError:
        text = json_format.MessageToJson(msg, including_default_value_fields=True, preserving_proto_field_name=True)
    return text


def _msg_class(pool, full_name):
    from google.protobuf import message_factory
    return message_factory.GetMessageClass(pool.FindMessageTypeByName(full_name))


def sample_request(catalog, service, method):
    """A JSON skeleton of the request message (arrays for client-streaming methods)."""
    info = catalog.method(service, method)
    text = _json_dump(_msg_class(catalog.pool, info.input_type)())
    return f"[{text}]" if info.client_streaming else text


def invoke(catalog, target, service, method, request_json, tls=False, metadata=None, timeout=30):
    """Always returns an InvokeResult; protocol errors are reported in `code` / `details`."""
    _need()
    import grpc
    from google.protobuf import json_format
    info = catalog.method(service, method)
    in_cls = _msg_class(catalog.pool, info.input_type)
    out_cls = _msg_class(catalog.pool, info.output_type)
    try:
        payload = json.loads(request_json) if request_json.strip() else ({} if not info.client_streaming else [])
    except ValueError as e:
        raise GrpcError(f"The request is not valid JSON: {e}")
    items = payload if info.client_streaming else [payload]
    if not isinstance(items, list):
        raise GrpcError("A client-streaming request must be a JSON array of messages.")
    try:
        msgs = [json_format.ParseDict(i, in_cls()) for i in items]
    except json_format.ParseError as e:
        raise GrpcError(f"The request does not match {info.input_type}: {e}")
    channel = _channel(target.strip(), tls)
    out = InvokeResult()
    started = time.perf_counter()
    try:
        ser, de = (lambda m: m.SerializeToString()), out_cls.FromString
        md = _md(metadata)
        if not info.client_streaming and not info.server_streaming:
            call = channel.unary_unary(info.path, ser, de).with_call(msgs[0], timeout=timeout, metadata=md)
            out.messages.append(_json_dump(call[0]))
            call = call[1]
        else:
            if info.client_streaming and not info.server_streaming:
                reply, call = channel.stream_unary(info.path, ser, de).with_call(iter(msgs), timeout=timeout,
                                                                                  metadata=md)
                out.messages.append(_json_dump(reply))
            else:
                factory = channel.stream_stream if info.client_streaming else channel.unary_stream
                call = factory(info.path, ser, de)(iter(msgs) if info.client_streaming else msgs[0],
                                                   timeout=timeout, metadata=md)
                for m in call:
                    out.messages.append(_json_dump(m))
        out.trailers = [(k, v if isinstance(v, str) else repr(v)) for k, v in (call.trailing_metadata() or [])]
        out.code = call.code().name if call.code() else "OK"
        out.details = call.details() or ""
    except grpc.RpcError as e:
        out.code, out.details = _rpc_message(e)
        try:
            out.trailers = [(k, v if isinstance(v, str) else repr(v)) for k, v in (e.trailing_metadata() or [])]
        except Exception:  # noqa: BLE001
            pass
    finally:
        channel.close()
    out.elapsed_ms = int((time.perf_counter() - started) * 1000)
    return out
