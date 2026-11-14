# -*- coding: utf-8 -*-
"""جدول‌های چرخش باید در مهاجرتِ D1 هم کپی شوند (فاز ۳ · schema v11).

اگر `ai_rotation`/`ai_rotation_log` در `TABLES` نباشند، مهاجرت به D1 مکان‌نما و audit را
صفر می‌کند: چرخش از اول شروع می‌شود و تاریخِ درخواست‌ها گم می‌شود.
"""
import os

import tools.migrate_sqlite_to_d1 as mig
from newsbot.db import SCHEMA


def test_rotation_tables_are_in_the_d1_migration_list():
    for t in ("ai_rotation", "ai_rotation_log"):
        assert t in mig.TABLES, "%s در TABLES نیست ⇒ مهاجرتِ D1 آن را خالی می‌گذارد" % t


def test_every_rotation_table_exists_in_the_schema():
    src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "newsbot", "db.py")
    text = open(src, encoding="utf-8").read()
    assert "CREATE TABLE IF NOT EXISTS ai_rotation(" in text
    assert "CREATE TABLE IF NOT EXISTS ai_rotation_log(" in text
    assert "idx_ai_rotation_log_scope" in text
    assert SCHEMA.strip()
