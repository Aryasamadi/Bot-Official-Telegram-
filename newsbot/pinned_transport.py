# -*- coding: utf-8 -*-
"""IP pinning for outbound HTTP (NET-003).

Problem: `public_url*` validated DNS answers, but httpx/httpcore performed their *own* lookup when
connecting, so a TTL-0 authoritative server could answer the validation lookups with a public IP and the
connection lookup with 127.0.0.1 (DNS rebinding).

Fix: wrap httpcore's network backend so that **the address that is validated is the address that is
connected to**. Resolution + validation + connect happen in one place (`PinnedBackend.connect_tcp`).
TLS still uses the original hostname for SNI/certificate checks because httpcore starts TLS with
`server_hostname=origin.host`, independently of the host passed to `connect_tcp`; the `Host` header is
also untouched. A private/loopback/link-local answer is refused unless the operator allow-listed that
exact host:port (`ALLOW_PRIVATE_MODEL_HOSTS`, same gate as `egress_url_ok`).
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
import ipaddress
import socket
from urllib.parse import urlparse

import httpcore

from newsbot.net_http import _all_public, is_private_host, private_host_allowed

_TRUSTED_ORIGIN: ContextVar[tuple[str, int] | None] = ContextVar("pinned_trusted_origin", default=None)


@contextmanager
def trusted_origin(url: str):
    """Allow-list the exact (host, port) of an operator-configured internal endpoint (e.g. Bot.base_url)
    for the current async task context only."""
    try:
        p = urlparse(str(url))
        host = (p.hostname or "").strip("[]").lower().rstrip(".")
        port = p.port or (443 if p.scheme == "https" else 80)
        pair = (host, int(port)) if host else None
    except Exception:
        pair = None
    tok = _TRUSTED_ORIGIN.set(pair)
    try:
        yield
    finally:
        _TRUSTED_ORIGIN.reset(tok)


class RebindingBlocked(PermissionError):
    """Resolved/pinned address is not allowed for this destination."""


def pin_addresses(host: str, port: int, resolver=None) -> list[str]:
    """Resolve `host` once and return the ordered, validated addresses to connect to.

    Raises RebindingBlocked when any answer is non-public (fail-closed) and the destination is not on the
    operator's private allow-list. `resolver` is injectable for tests."""
    h = (host or "").strip("[]").lower().rstrip(".")
    if not h:
        raise RebindingBlocked("empty host")
    resolve = resolver or (lambda name, p: [i[4][0] for i in socket.getaddrinfo(name, p, type=socket.SOCK_STREAM)])
    try:
        raw = list(resolve(h, port))
    except OSError as e:
        raise RebindingBlocked(f"cannot resolve {h}: {e}") from e
    addrs: list[str] = []
    for a in raw:
        if a not in addrs:
            addrs.append(a)
    if not addrs:
        raise RebindingBlocked(f"no addresses for {h}")
    allowed_private = private_host_allowed(f"http://{h}:{port}") or (_TRUSTED_ORIGIN.get() == (h, int(port)))
    if is_private_host(h):
        # Named/literal private destination: only through the explicit allow-list.
        if not allowed_private:
            raise RebindingBlocked(f"{h}:{port} is private and not allow-listed")
        return addrs
    if not _all_public(addrs):
        if allowed_private:
            return addrs
        raise RebindingBlocked(f"{h} resolved to a non-public address {addrs}")
    return addrs


class PinnedBackend(httpcore.AsyncNetworkBackend):
    """Delegates to `inner`, but connects only to addresses validated at connect time."""

    def __init__(self, inner, resolver=None):
        self._inner = inner
        self._resolver = resolver

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        addrs = await asyncio.to_thread(pin_addresses, host, port, self._resolver)
        last: Exception | None = None
        for ip in addrs:
            try:
                ipaddress.ip_address(ip)
            except ValueError:
                continue
            try:
                return await self._inner.connect_tcp(ip, port, timeout=timeout, local_address=local_address,
                                                     socket_options=socket_options)
            except (httpcore.ConnectError, httpcore.ConnectTimeout, OSError) as e:
                last = e
        raise last or httpcore.ConnectError(f"no usable address for {host}")

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        return await self._inner.connect_unix_socket(path, timeout=timeout, socket_options=socket_options)

    async def sleep(self, seconds):
        await self._inner.sleep(seconds)


def install_pinning(transport, resolver=None):
    """Make an `httpx.AsyncHTTPTransport` connect only to validated, pinned addresses.

    Fails loudly (RuntimeError) if the installed httpx/httpcore no longer exposes the expected pool shape,
    rather than silently running without protection."""
    pool = getattr(transport, "_pool", None)
    backend = getattr(pool, "_network_backend", None)
    if pool is None or backend is None:
        raise RuntimeError("cannot install IP pinning: unexpected httpx/httpcore internals")
    pool._network_backend = PinnedBackend(backend, resolver=resolver)
    return transport
