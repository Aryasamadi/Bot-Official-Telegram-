# -*- coding: utf-8 -*-
"""فاز ۱۵ — تضمین روشن‌ماندن: sd_notify/watchdog و سخت‌سازی systemd (OPS-22/OPS-23، QA-12)."""
import asyncio
from pathlib import Path

import pytest

from newsbot import watchdog

ROOT = Path(__file__).resolve().parent.parent


def _dropin(name):
    return (ROOT / "deploy" / "systemd" / name).read_text(encoding="utf-8")


def test_restart_dropin_disables_start_limit_and_always_restarts():
    text = _dropin("10-restart-always.conf")
    unit, service = text.split("[Service]", 1)
    assert "StartLimitIntervalSec=0" in unit
    assert "Restart=always" in service
    assert "RestartSec=5" in service
    assert "ExecStopPost=" in service


def test_watchdog_dropin_requires_notify_and_watchdog():
    text = _dropin("20-watchdog.conf")
    assert "Type=notify" in text
    assert "NotifyAccess=main" in text
    assert "WatchdogSec=300" in text
    assert "TimeoutStartSec=180" in text


def test_notify_ready_and_stopping_messages(monkeypatch):
    sent = []
    monkeypatch.setattr(watchdog, "_send", lambda message: sent.append(message) or True)
    assert watchdog.notify_ready() is True
    assert watchdog.notify_stopping() is True
    assert sent == ["READY=1", "STOPPING=1"]


def test_send_without_socket_is_noop(monkeypatch):
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    assert watchdog._send("READY=1") is False


def test_address_supports_abstract_socket(monkeypatch):
    monkeypatch.setenv("NOTIFY_SOCKET", "@/run/systemd/notify")
    assert watchdog._address() == "\0/run/systemd/notify"
    monkeypatch.delenv("NOTIFY_SOCKET")
    assert watchdog._address() is None


def test_watchdog_interval_reads_systemd_usec(monkeypatch):
    monkeypatch.setenv("WATCHDOG_USEC", "300000000")
    assert watchdog.watchdog_interval() == pytest.approx(100.0)
    monkeypatch.setenv("WATCHDOG_USEC", "bogus")
    assert watchdog.watchdog_interval() is None
    monkeypatch.setenv("WATCHDOG_USEC", "0")
    assert watchdog.watchdog_interval() is None


def test_start_disabled_without_socket(monkeypatch):
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    monkeypatch.setenv("WATCHDOG_USEC", "300000000")
    assert watchdog.start() is None


def test_start_creates_raw_named_task(monkeypatch):
    monkeypatch.setenv("NOTIFY_SOCKET", "/tmp/notify")
    monkeypatch.setenv("WATCHDOG_USEC", "3000000")

    async def _run():
        task = watchdog.start()
        assert task is not None
        assert task.get_name() == "watchdog"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(_run())


def test_watchdog_loop_pings_and_cancels(monkeypatch):
    sent = []
    monkeypatch.setattr(watchdog, "_send", lambda message: sent.append(message) or True)

    async def _run():
        task = asyncio.create_task(watchdog.watchdog_loop(0.01))
        await asyncio.sleep(0.035)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(_run())
    assert sent
    assert set(sent) == {"WATCHDOG=1"}
