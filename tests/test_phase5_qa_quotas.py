# -*- coding: utf-8 -*-
"""فاز ۵ — سهمیهٔ پرسش و پاسخ: cooldown، سقفِ روزانهٔ کاربر و سقفِ روزانهٔ پست."""
from pathlib import Path

from newsbot import config

ROOT = Path(__file__).resolve().parents[1]


def test_the_config_defaults_and_ranges_are_the_designed_values():
    assert (config.QA_COOLDOWN_S, config.QA_USER_DAILY_MAX,
            config.QA_POST_DAILY_MAX) == (30, 30, 50)


def test_the_env_example_and_self_check_include_all_three_names():
    env = (ROOT / ".env.example").read_text(encoding="utf-8", errors="replace")
    app = (ROOT / "newsbot" / "app.py").read_text(encoding="utf-8", errors="replace")
    for name in ("QA_COOLDOWN_S", "QA_USER_DAILY_MAX", "QA_POST_DAILY_MAX"):
        assert name in env and name in app
