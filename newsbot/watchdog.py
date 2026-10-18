# -*- coding: utf-8 -*-
"""پل sd_notify برای systemd: اعلام آمادگی، پینگ watchdog و اعلام خاموشی (OPS-22/OPS-23)."""
import asyncio
import os
import socket

from newsbot.logutils import log

_NOTIFY_SOCKET = "NOTIFY_SOCKET"
_WATCHDOG_USEC = "WATCHDOG_USEC"


def _address() -> str | None:
    path = os.environ.get(_NOTIFY_SOCKET, "")
    if not path:
        return None
    return "\0" + path[1:] if path.startswith("@") else path


def _send(message: str) -> bool:
    addr = _address()
    if not addr or not hasattr(socket, "AF_UNIX"):
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.sendto(message.encode("utf-8"), addr)
        return True
    except OSError as exc:
        log.debug("sd_notify(%s) failed: %s", message.split("=", 1)[0], exc)
        return False


def notify_ready() -> bool:
    return _send("READY=1")


def notify_stopping() -> bool:
    return _send("STOPPING=1")


def watchdog_interval() -> float | None:
    try:
        usec = int(os.environ.get(_WATCHDOG_USEC, ""))
    except (TypeError, ValueError):
        return None
    if usec <= 0:
        return None
    return max(1.0, usec / 3_000_000.0)


async def watchdog_loop(interval: float | None = None) -> None:
    interval = watchdog_interval() if interval is None else interval
    if not interval or interval <= 0:
        return
    while True:
        _send("WATCHDOG=1")
        await asyncio.sleep(interval)


def _on_done(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log.warning("watchdog متوقف شد: %s", exc)


def start() -> asyncio.Task | None:
    if not _address():
        return None
    interval = watchdog_interval()
    if not interval:
        return None
    task = asyncio.create_task(watchdog_loop(interval), name="watchdog")
    task.add_done_callback(_on_done)
    log.info("systemd watchdog فعال است (پینگ هر %.0f ثانیه)", interval)
    return task
