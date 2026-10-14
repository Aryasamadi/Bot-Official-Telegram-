# -*- coding: utf-8 -*-
"""GAP-10/SEC-02/OPS-14: هیچ مسیر لاگی نباید راز عبور دهد.

پیش از این فقط log_event رازها را ماسک می‌کرد؛ هر log.* مستقیم و هر traceback
بی‌ماسک ثبت می‌شد. این تست‌ها روی RecordFactory سراسری قفل می‌گذارند."""
import logging

from newsbot.logutils import log, log_event  # noqa: F401 — import = نصب RecordFactory

# مقدار راز → بقایایی که *نباید* در هیچ لاگی دیده شوند
LEAKS = {
    "token=ghp_a1b2c3d4e5f6g7h8i9j0": ["ghp_a1b2c3d4e5f6g7h8i9j0"],
    "BOT_TOKEN=1234567890:AAxyzEXAMPLEtokenABCdef": ["AAxyzEXAMPLEtokenABCdef", "1234567890:AA"],
    "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payloadsig": ["eyJhbGciOiJIUzI1NiJ9"],
    "api_key=AIzaSyAbCdEfGhIjKlMnOpQrStUvWxYz012345678": ["AIzaSyAbCdEfGh"],
    "sk-proj-abcdefghijklmnop123456": ["sk-proj-abcdefghijklmnop"],
    "password=hunter2secretvalue": ["hunter2secretvalue"],
    "?key=AAAAsecretvalue&token=BBBBsecretvalue": ["AAAAsecretvalue", "BBBBsecretvalue"],
}


def _all_text(rec):
    return f"{rec.getMessage()}\n{rec.exc_text or ''}\n{rec.stack_info or ''}"


def _assert_no_leak(text, label):
    for frag in LEAKS[label]:
        assert frag not in text, f"نشت راز در لاگ: {frag!r} در {text!r}"


def test_direct_log_calls_are_redacted(caplog):
    """log.* مستقیم (بدون log_event) هم باید ماسک شود — قلب OPS-14."""
    with caplog.at_level(logging.DEBUG, logger="core"):
        for label in LEAKS:
            log.error("failed to send: %s", label)
            log.warning("cfg dump %s", label)
    assert caplog.records
    for rec in caplog.records:
        for label in LEAKS:
            _assert_no_leak(_all_text(rec), label)


def test_exception_traceback_is_redacted(caplog):
    """متن استثنا (traceback) هم باید ماسک شود، نه فقط پیام اصلی."""
    secret = "API_KEY=supersecretvalue123456 and sk-proj-abcdefghijkl"
    with caplog.at_level(logging.ERROR, logger="core"):
        try:
            raise RuntimeError(f"upstream rejected {secret}")
        except RuntimeError:
            log.exception("model call failed")
    rec = caplog.records[-1]
    assert "supersecretvalue123456" not in _all_text(rec)
    assert "sk-proj-abcdefghijkl" not in _all_text(rec)
    assert "RuntimeError" in (rec.exc_text or ""), "استثنا نباید حذف شود، فقط راز پاک شود"


def test_log_event_still_redacted(caplog):
    with caplog.at_level(logging.INFO, logger="core"):
        log_event("ERROR", "publish failed token=deadbeefcafe1234", admin_id=1)
    for rec in caplog.records:
        assert "deadbeefcafe1234" not in _all_text(rec)


def test_normal_log_text_survives(caplog):
    """ضد-نشت نباید متن عادی لاگ را ببلعد (over-redaction)."""
    with caplog.at_level(logging.INFO, logger="core"):
        log.info("cycle finished in 4200ms, 17 articles, 3 channels")
    assert any("cycle finished in 4200ms" in rec.getMessage() for rec in caplog.records)


def test_factory_installed_globally():
    """RecordFactory سراسری باید نصب باشد (وگرنه همهٔ تست‌های بالا بی‌معنی‌اند)."""
    assert getattr(logging, "_newsbot_redact_factory", False)
