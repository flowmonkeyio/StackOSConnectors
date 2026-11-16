"""Versioned wire controls and an owned loopback peer, never a real provider."""

import copy
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from stackos_connectors import ConnectorAuth, ConnectorClient
from stackos_connectors.catalog import load_registry

FIXTURES = Path(__file__).parent / "fixtures"
ORG = "73837d14-fbd6-40ca-81f4-19e2ce031fc9"
KEY = "synthetic-c09-canary"


def wire(name):
    return json.loads((FIXTURES / (name + ".json")).read_text())


@pytest.fixture
def catalog():
    return wire("catalog-v1")


@pytest.fixture
def identity():
    return wire("current-key-v1")


@pytest.fixture
def native():
    return ConnectorClient(registry=load_registry("connectors/cfobooth/catalog.json"))


def auth(base="https://owned.test/prefix", org=ORG, key=KEY):
    return ConnectorAuth(
        "api_key", {"api_key": key}, {"api_base_url": base, "organization_id": org}
    )


def descriptor(*, method="POST", mutation=False):
    item = wire("catalog-v1")["data"]["operations"][2]
    item["operationId"] = "future.synthetic"
    item["route"].update(method=method, path="/api/organizations/:organizationId/future/:recordId")
    item["schemas"]["params"]["properties"]["recordId"] = {"type": "string", "minLength": 1}
    item["schemas"]["params"]["required"].append("recordId")
    if method == "GET":
        item["route"]["body"] = "none"
        item["schemas"]["body"] = None
    if mutation:
        item.update(effect="mutation", replay="explicit-only")
    return copy.deepcopy(item)


@pytest.fixture(autouse=True)
def owned_network_only(monkeypatch):
    original = socket.socket.connect

    def connect(sock, address):
        if sock.family in {socket.AF_INET, socket.AF_INET6}:
            assert address[0] in {"127.0.0.1", "::1"}, "only owned loopback peers are permitted"
        return original(sock, address)

    monkeypatch.setattr(socket.socket, "connect", connect)


@pytest.fixture
def peer(catalog, identity):
    state = SimpleNamespace(calls=[], status=None, output=None, headers={})

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.respond()

        def do_POST(self):
            self.respond()

        def respond(self):
            body = self.rfile.read(int(self.headers.get("content-length", 0)))
            state.calls.append((self.command, self.path, dict(self.headers), body))
            output = state.output
            if output is None:
                output = (
                    identity
                    if self.path.endswith("/api-keys/current")
                    else catalog
                    if self.path.endswith("/operation-catalog")
                    else {"requestId": "operation-c09", "data": {"status": "ok"}}
                )
            raw = json.dumps(output).encode()
            self.send_response(state.status or 200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            for key, value in state.headers.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.base = f"http://127.0.0.1:{server.server_port}/prefix"
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()
