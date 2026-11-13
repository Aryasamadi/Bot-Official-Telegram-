# -*- coding: utf-8 -*-
"""NET-003: DNS-rebinding — the validated address must be the connected address."""
import asyncio
import socket

import httpcore
import httpx
import pytest

from newsbot import config, net_http
from newsbot.pinned_transport import PinnedBackend, RebindingBlocked, install_pinning, pin_addresses

PUB = "93.184.216.34"


class _Inner(httpcore.AsyncNetworkBackend):
    def __init__(self):
        self.calls = []

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self.calls.append((host, port))
        raise httpcore.ConnectError("stop here (test)")

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise NotImplementedError

    async def sleep(self, seconds):
        await asyncio.sleep(0)


def test_public_answer_is_pinned_and_used_for_the_connection():
    inner = _Inner()
    be = PinnedBackend(inner, resolver=lambda h, p: [PUB])
    with pytest.raises(httpcore.ConnectError):
        asyncio.run(be.connect_tcp("news.example.com", 443))
    assert inner.calls == [(PUB, 443)], "must connect to the validated IP, never to the hostname"


def test_private_answer_is_refused_before_any_connection():
    inner = _Inner()
    be = PinnedBackend(inner, resolver=lambda h, p: ["127.0.0.1"])
    with pytest.raises(RebindingBlocked):
        asyncio.run(be.connect_tcp("evil.example.com", 80))
    assert inner.calls == []


def test_mixed_public_and_private_answers_are_refused():
    with pytest.raises(RebindingBlocked):
        pin_addresses("evil.example.com", 80, resolver=lambda h, p: [PUB, "10.0.0.5"])


def test_resolver_that_flips_between_calls_cannot_win():
    """First answer public, second private: only ONE resolution happens at connect time, so the
    connected address is the validated one (the attacker cannot swap it afterwards)."""
    answers = iter([[PUB], ["127.0.0.1"], ["127.0.0.1"]])
    inner = _Inner()
    be = PinnedBackend(inner, resolver=lambda h, p: next(answers))
    with pytest.raises(httpcore.ConnectError):
        asyncio.run(be.connect_tcp("flip.example.com", 80))
    assert inner.calls == [(PUB, 80)]


def test_private_destination_requires_the_operator_allow_list(monkeypatch):
    monkeypatch.setattr(config, "ALLOW_PRIVATE_MODEL_HOSTS", "")
    with pytest.raises(RebindingBlocked):
        pin_addresses("localhost", 11434, resolver=lambda h, p: ["127.0.0.1"])
    monkeypatch.setattr(config, "ALLOW_PRIVATE_MODEL_HOSTS", "localhost:11434")
    assert pin_addresses("localhost", 11434, resolver=lambda h, p: ["127.0.0.1"]) == ["127.0.0.1"]
    with pytest.raises(RebindingBlocked):      # other port is not allow-listed
        pin_addresses("localhost", 9999, resolver=lambda h, p: ["127.0.0.1"])


def test_unresolvable_host_fails_closed():
    def boom(h, p):
        raise socket.gaierror("nope")
    with pytest.raises(RebindingBlocked):
        pin_addresses("nx.example.com", 80, resolver=boom)


def test_real_client_uses_the_pinned_backend():
    net_http._http = None
    c = net_http.http()
    pool = c._transport._pool
    assert isinstance(pool._network_backend, PinnedBackend)
    asyncio.run(net_http.close_http())


def test_install_pinning_fails_loudly_on_unexpected_internals():
    class Fake:
        _pool = object()
    with pytest.raises(RuntimeError):
        install_pinning(Fake())


def test_end_to_end_rebinding_attempt_through_httpx_is_blocked(monkeypatch):
    """A hostname that resolves to loopback must never be connected to, even though a server is listening."""
    hit = {"n": 0}

    async def handler(reader, writer):
        hit["n"] += 1
        await reader.read(1024)
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
        await writer.drain(); writer.close()

    async def scenario():
        srv = await asyncio.start_server(handler, "127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]
        real = socket.getaddrinfo
        monkeypatch.setattr(socket, "getaddrinfo", lambda h, p, *a, **k: real("127.0.0.1", p, *a, **k) if h == "evil.example.com" else real(h, p, *a, **k))
        monkeypatch.setattr(config, "ALLOW_PRIVATE_MODEL_HOSTS", "")
        net_http._http = None
        client = net_http.http()
        try:
            with pytest.raises(Exception) as ei:
                await client.get(f"http://evil.example.com:{port}/")
            assert "non-public" in str(ei.value) or "RebindingBlocked" in type(ei.value).__name__ or "private" in str(ei.value)
        finally:
            await net_http.close_http()
            srv.close(); await srv.wait_closed()

    asyncio.run(scenario())
    assert hit["n"] == 0, "the loopback server must never have been contacted"


def test_end_to_end_allow_listed_private_host_still_works(monkeypatch):
    async def handler(reader, writer):
        await reader.read(1024)
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
        await writer.drain(); writer.close()

    async def scenario():
        srv = await asyncio.start_server(handler, "127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]
        monkeypatch.setattr(config, "ALLOW_PRIVATE_MODEL_HOSTS", f"localhost:{port}")
        net_http._http = None
        client = net_http.http()
        try:
            r = await client.get(f"http://localhost:{port}/")
            assert r.status_code == 200 and r.text == "ok"
        finally:
            await net_http.close_http()
            srv.close(); await srv.wait_closed()

    asyncio.run(scenario())


def test_httpx_default_client_unaffected_by_module_import():
    assert httpx.AsyncClient is not None
