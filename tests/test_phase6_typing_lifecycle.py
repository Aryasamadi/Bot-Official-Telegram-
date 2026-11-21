# -*- coding: utf-8 -*-
"""فاز ۶ — چرخهٔ نشانگرِ «در حال تایپ» (typing lifecycle).

تست‌های پیکربندی: سه مقدارِ env-backed و مستندسازیِ آنها در `.env.example` و preflight.
"""
from pathlib import Path

from newsbot import config

ROOT = Path(__file__).resolve().parents[1]


def test_typing_config_defaults_are_the_designed_values():
    assert config.TYPING_ENABLED is True
    assert (config.TYPING_START_DELAY_S, config.TYPING_REFRESH_S) == (1.0, 4.0)


def test_env_example_and_preflight_carry_the_three_names():
    env = (ROOT / ".env.example").read_text(encoding="utf-8", errors="replace")
    app = (ROOT / "newsbot" / "app.py").read_text(encoding="utf-8", errors="replace")
    assert all(n in env for n in ("TYPING_ENABLED", "TYPING_START_DELAY_S", "TYPING_REFRESH_S"))
    assert "TYPING_START_DELAY_S" in app and "TYPING_REFRESH_S" in app
