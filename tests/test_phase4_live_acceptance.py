# -*- coding: utf-8 -*-
"""فاز ۴ — ابزارِ پذیرشِ زنده (workflow دستیِ qa-live) و نگهبان‌های فایل‌های آن.

این تست‌ها آفلاین‌اند: هیچ تماسِ شبکه‌ای و هیچ توکنی وجود ندارد. هدفشان قفل‌کردنِ
قراردادهای ابزار است (ورودی/خروجی، انتسابِ per-row، کدهای خروج) و شکلِ فایل‌های
workflow و رانبوک.
"""
import json
import os

from tools import qa_live_acceptance as acc

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKEN = "123456789:AA" + "x" * 34


def _src(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


# ── Task 2: env، شکلِ توکن، پیش‌شرط‌ها ─────────────────────────────────────────────
def test_env_file_values_are_loaded_and_process_env_wins(tmp_path, monkeypatch):
    p = tmp_path / "run.env"
    p.write_text('BOT_TOKEN="from-file"\nSECRETS_KEY=from-file\n# comment\n\n', encoding="utf-8")
    monkeypatch.setenv("BOT_TOKEN", "from-process")
    env = acc.load_env(str(p))
    assert env["BOT_TOKEN"] == "from-process" and env["SECRETS_KEY"] == "from-file"


def test_the_token_shape_is_checked():
    assert acc.token_shape_ok(TOKEN)
    assert not acc.token_shape_ok("not-a-token")
    assert not acc.token_shape_ok("123456789:short")


def test_admin_ids_accept_commas_and_spaces():
    assert acc.admin_ids(" 1, 22 33 ") == {1, 22, 33}


def test_secret_presence_reports_names_only():
    names = acc.secret_values_present("x " + TOKEN + " y",
                                      {"BOT_TOKEN": TOKEN, "SECRETS_KEY": "k" * 64})
    assert names == ["BOT_TOKEN"]


def test_check_rejects_a_missing_token(tmp_path):
    env = {"BOT_TOKEN": "", "SUPER_ADMIN_IDS": "1", "DB_FILE": str(tmp_path / "db.sqlite")}
    code, out = acc.run_check(env, http_get=lambda url, timeout=10: {"ok": True})
    assert code == 2 and "BOT_TOKEN_MISSING" in out["problems"]


def test_check_requires_secrets_key_when_the_db_exists(tmp_path):
    db = tmp_path / "db.sqlite"
    db.write_bytes(b"")
    env = {"BOT_TOKEN": TOKEN, "SUPER_ADMIN_IDS": "1", "SECRETS_KEY": "", "DB_FILE": str(db)}
    code, out = acc.run_check(env, http_get=lambda url, timeout=10: {"ok": True})
    assert code == 2 and "SECRETS_KEY_REQUIRED" in out["problems"]


def test_check_requires_secrets_key_even_for_a_fresh_db(tmp_path):
    """کلیدِ خالی یعنی کلیدِ مدل به‌صورتِ متنِ ساده در DB کش‌شده می‌نشیند."""
    env = {"BOT_TOKEN": TOKEN, "SUPER_ADMIN_IDS": "1", "SECRETS_KEY": "",
           "DB_FILE": str(tmp_path / "db.sqlite")}
    code, out = acc.run_check(env, http_get=lambda url, timeout=10: {"ok": True})
    assert code == 2 and "SECRETS_KEY_REQUIRED" in out["problems"]


def test_check_calls_get_me_and_maps_failures_without_the_url(tmp_path):
    env = {"BOT_TOKEN": TOKEN, "SUPER_ADMIN_IDS": "1", "SECRETS_KEY": "k" * 64,
           "DB_FILE": str(tmp_path / "db.sqlite")}
    seen = {}

    def boom(url, timeout=10):
        seen["url"] = url
        raise RuntimeError("http 401")

    code, out = acc.run_check(env, http_get=boom)
    assert code == 2 and "GETME_FAILED" in out["problems"]
    assert TOKEN in seen["url"] and TOKEN not in json.dumps(out)


def test_check_passes_with_valid_inputs(tmp_path):
    env = {"BOT_TOKEN": TOKEN, "SUPER_ADMIN_IDS": "1", "SECRETS_KEY": "k" * 64,
           "DB_FILE": str(tmp_path / "db.sqlite")}
    code, out = acc.run_check(env, http_get=lambda url, timeout=10: {"ok": True, "result": {"id": 1}})
    assert code == 0 and out["ok"] is True and out["token_len"] == len(TOKEN)


# ── Task 3: اسنپ‌شات، انتسابِ per-row و بررسی‌ها ────────────────────────────────────
START = "2026-11-17T10:00:00.000000+00:00"
LATER = "2026-11-17T10:05:00.000000+00:00"
EARLIER = "2026-11-17T09:00:00.000000+00:00"


def _qa(chat=-555, msg=10, uid=900001, status="done", created=START, chid=-100111, error=""):
    return {"chat_id": chat, "msg_id": msg, "user_id": uid, "channel_id": chid, "post_id": 77,
                "status": status, "answer": "", "error": error, "created_at": created, "updated_at": created}


def _u(admin=900001, ai=1, day="2026-11-17"):
    return {"admin_id": admin, "day": day, "posts": 0, "tests": 0, "ai": ai}


def _g(gid=1, uid=900001, quota=1, used=1, granted=LATER):
    return {"id": gid, "user_id": uid, "plan_id": 1, "quota": quota, "used": used,
                "granted_at": granted, "expires_at": "2026-12-17T10:00:00.000000+00:00"}


def _snap(rows=(), usage=(), grants=(), channels=(), models=()):
    return acc.Snapshot(qa_rows=tuple(rows), usage=tuple(usage), grants=tuple(grants),
                        channels=tuple(channels), models=tuple(models))


def _tr(key=(-555, 10), kind="insert", to="done", usage=None, grants=None, ambiguous=False):
    return {"key": list(key), "kind": kind, "to": to, "usage": usage or {},
            "grants": grants or {}, "ambiguous": ambiguous}


def _ctx(snapshot, transitions=(), log="", scenario="core", precondition=None,
         super_ids=(), expected_group=-555, expected_channel=-100111):
    return acc.EvalContext(scenario=scenario, started_at=START, expected_group=expected_group,
                           expected_channel=expected_channel, transitions=list(transitions),
                           snapshot=snapshot, log_text=log, super_ids=set(super_ids),
                           secret_values={}, precondition=precondition)


def _by_name(ctx):
    return {c["name"]: c for c in acc.evaluate(ctx)}


def test_snapshot_reads_the_real_schema(isolated_db):
    import sqlite3
    c = sqlite3.connect(isolated_db)
    c.execute("INSERT INTO qa_answers(chat_id,msg_id,user_id,channel_id,post_id,status,answer,error,"
              "created_at,updated_at) VALUES(-555,10,900001,-100111,77,'done','متنِ پاسخ','',?,?)",
              (START, START))
    c.execute("INSERT INTO usage(admin_id,day,posts,tests,ai) VALUES(900001,'2026-11-17',0,0,1)")
    c.execute("INSERT INTO ai_plan_grants(id,user_id,plan_id,quota,used,granted_at,expires_at) "
              "VALUES(1,900001,1,1,1,?,?)", (LATER, "2026-12-17T10:00:00.000000+00:00"))
    c.execute("INSERT INTO channels(id,admin_id,chat_id,title) VALUES(1,900001,-100111,'کانال')")
    c.execute("INSERT INTO ai_models(id,name,active,status,owner_id) VALUES(1,'m',1,'ok',NULL)")
    c.commit()
    c.close()
    snap = acc.read_snapshot(isolated_db)
    assert len(snap.qa_rows) == 1 and snap.qa_rows[0]["status"] == "done"
    assert len(snap.usage) == 1 and snap.usage[0]["ai"] == 1
    assert len(snap.grants) == 1 and snap.grants[0]["quota"] == 1
    assert len(snap.channels) == 1 and snap.channels[0]["chat_id"] == -100111
    assert len(snap.models) == 1 and snap.models[0]["owner_id"] is None


def test_a_missing_db_reads_as_empty(isolated_db, tmp_path):
    assert acc.read_snapshot(str(tmp_path / "nope" / "missing.db")) == acc.EMPTY_SNAPSHOT


def test_rows_from_an_earlier_run_are_ignored():
    snap = _snap(rows=[_qa(created=EARLIER)], usage=[_u(ai=1)], channels=[_c_row()],
                 models=[_m_row()])
    got = _by_name(_ctx(snap, transitions=[_tr(usage={900001: 1})], log=_answered()))
    assert got["answer_delivered"]["status"] == "NOT_OBSERVED"
    assert got["asker_charged_only"]["status"] == "NOT_OBSERVED"
    assert got["answered_exactly_once"]["status"] == "NOT_OBSERVED"


def _c_row(cid=1, chat=-100111):
    return {"id": cid, "admin_id": 900001, "chat_id": chat, "title": "کانال"}


def _m_row(mid=1, owner=None, active=1):
    return {"id": mid, "name": "m", "active": active, "status": "ok", "owner_id": owner}


def _answered(chat=-555, msg=10, uid=900001):
    return ("2026-11-17 10:00:01 | INFO | qa: answered chat=%s msg=%s user=%s qlen=5 alen=4\n"
            % (chat, msg, uid))


def test_answer_delivered_and_channel_id_are_checked():
    snap = _snap(rows=[_qa()], channels=[_c_row()], models=[_m_row()])
    got = _by_name(_ctx(snap, transitions=[_tr(usage={900001: 1})], log=_answered()))
    assert got["answer_delivered"]["status"] == "OBSERVED"
    assert got["channel_id_is_telegram_chat_id"]["status"] == "OBSERVED"
    assert got["public_model_active"]["status"] == "OBSERVED"
    bad = _snap(rows=[_qa(chid=1)], channels=[_c_row()], models=[_m_row()])
    got = _by_name(_ctx(bad, transitions=[_tr(usage={900001: 1})], log=_answered()))
    assert got["channel_id_is_telegram_chat_id"]["status"] == "NOT_OBSERVED"


def test_two_rows_in_one_window_are_ambiguous():
    prev = _snap()
    cur = _snap(rows=[_qa(msg=10), _qa(msg=11, uid=900002)],
                usage=[_u(900001, 1), _u(900002, 1)])
    trs = acc.attribute(prev, cur)
    assert len(trs) == 2 and all(t["ambiguous"] for t in trs)
    assert all(t["usage"] == {} and t["grants"] == {} for t in trs)
    snap = _snap(rows=[_qa(msg=10), _qa(msg=11, uid=900002)], channels=[_c_row()],
                 models=[_m_row()])
    got = _by_name(_ctx(snap, transitions=trs, log=_answered() + _answered(msg=11, uid=900002)))
    assert got["asker_charged_only"]["status"] == "ERROR"


def test_one_row_with_insert_and_status_in_one_window_is_not_ambiguous():
    prev = _snap()
    cur = _snap(rows=[_qa()], usage=[_u(900001, 1)])
    trs = acc.attribute(prev, cur)
    assert len(trs) == 1 and trs[0]["usage"] == {900001: 1} and not trs[0]["ambiguous"]


def test_asker_charged_only_requires_exactly_one_unit_for_that_user():
    snap = _snap(rows=[_qa()], channels=[_c_row()], models=[_m_row()])
    ok = _ctx(snap, transitions=[_tr(usage={900001: 1})], log=_answered())
    assert _by_name(ok)["asker_charged_only"]["status"] == "OBSERVED"
    for usage in ({900001: 2}, {900001: 1, 5: 1}, {}):
        bad = _ctx(snap, transitions=[_tr(usage=usage)], log=_answered())
        assert _by_name(bad)["asker_charged_only"]["status"] == "NOT_OBSERVED", usage


def test_a_done_row_needs_exactly_one_answered_line():
    snap = _snap(rows=[_qa()], channels=[_c_row()], models=[_m_row()])
    one = _ctx(snap, transitions=[_tr(usage={900001: 1})], log=_answered())
    assert _by_name(one)["answered_exactly_once"]["status"] == "OBSERVED"
    for log in ("", _answered() + _answered()):
        bad = _ctx(snap, transitions=[_tr(usage={900001: 1})], log=log)
        assert _by_name(bad)["answered_exactly_once"]["status"] == "NOT_OBSERVED"


def test_a_foreign_group_ignore_line_and_no_row_is_observed():
    log = "2026-11-17 10:00:02 | INFO | qa: ignored chat=-777 msg=20 user=900001 reason=foreign_group\n"
    snap = _snap(rows=[_qa()], channels=[_c_row()], models=[_m_row()])
    ok = _ctx(snap, transitions=[_tr(usage={900001: 1})], log=_answered() + log, scenario="full")
    assert _by_name(ok)["unrelated_group_silent"]["status"] == "OBSERVED"
    assert _by_name(ok)["ignored_with_a_reason"]["status"] == "OBSERVED"
    with_row = _snap(rows=[_qa(), _qa(chat=-777, msg=20)], channels=[_c_row()], models=[_m_row()])
    bad = _ctx(with_row, transitions=[_tr(usage={900001: 1})], log=_answered() + log,
               scenario="full")
    assert _by_name(bad)["unrelated_group_silent"]["status"] == "NOT_OBSERVED"


def test_toggle_off_requires_a_line_and_no_row():
    log = "2026-11-17 10:00:03 | INFO | qa: ignored chat=-555 msg=21 user=900001 reason=toggle_off\n"
    snap = _snap(rows=[_qa()], channels=[_c_row()], models=[_m_row()])
    ok = _ctx(snap, transitions=[_tr(usage={900001: 1})], log=_answered() + log, scenario="full")
    assert _by_name(ok)["toggle_off_silent"]["status"] == "OBSERVED"
    with_row = _snap(rows=[_qa(), _qa(msg=21, status="failed", error="ai_error")],
                     channels=[_c_row()], models=[_m_row()])
    bad = _ctx(with_row, transitions=[_tr(usage={900001: 1})], log=_answered() + log,
               scenario="full")
    assert _by_name(bad)["toggle_off_silent"]["status"] == "NOT_OBSERVED"


def _ai_limit_line(chat=-555, msg=12, uid=900001):
    return ("2026-11-17 10:00:04 | WARNING | qa: ai failed chat=%s msg=%s user=%s "
            "err=ai_limit\n" % (chat, msg, uid))


def test_quota_exhaustion_needs_a_failed_row_and_no_movement():
    log = _answered() + _ai_limit_line()
    snap = _snap(rows=[_qa(), _qa(msg=12, status="failed", error="ai_limit")],
                 usage=[_u(ai=1)], grants=[_g()], channels=[_c_row()], models=[_m_row()])
    trs = [_tr(usage={900001: 1}), _tr(key=(-555, 12), usage={}, grants={})]
    ok = _ctx(snap, transitions=trs, log=log, scenario="full")
    assert _by_name(ok)["quota_exhausted_message"]["status"] == "OBSERVED"
    moved = _ctx(snap, transitions=[_tr(usage={900001: 1}),
                                    _tr(key=(-555, 12), usage={900001: 1})],
                 log=log, scenario="full")
    assert _by_name(moved)["quota_exhausted_message"]["status"] == "NOT_OBSERVED"
    twice = _ctx(snap, transitions=trs, log=log + _ai_limit_line(), scenario="full")
    assert _by_name(twice)["quota_exhausted_message"]["status"] == "NOT_OBSERVED"


def test_an_earlier_grant_period_does_not_satisfy_the_quota_check():
    snap = _snap(rows=[_qa(), _qa(msg=12, status="failed", error="ai_limit", created=EARLIER)],
                 usage=[_u(ai=1)], grants=[_g(granted=EARLIER)], channels=[_c_row()],
                 models=[_m_row()])
    trs = [_tr(usage={900001: 1}), _tr(key=(-555, 12))]
    got = _by_name(_ctx(snap, transitions=trs, log=_answered() + _ai_limit_line(),
                        scenario="full"))
    assert got["quota_exhausted_message"]["status"] == "NOT_OBSERVED"


def test_no_text_row_is_free_and_optional():
    snap = _snap(rows=[_qa(msg=13, status="failed", error="no_text")], channels=[_c_row()],
                 models=[_m_row()])
    got = _by_name(_ctx(snap, log=""))
    assert got["no_text_no_ai"]["status"] == "OBSERVED"
    empty = _by_name(_ctx(_snap(channels=[_c_row()], models=[_m_row()])))
    assert empty["no_text_no_ai"]["status"] == "NOT_APPLICABLE"


def test_a_super_asker_is_a_precondition_failure():
    got = _by_name(_ctx(_snap(), precondition="SUPER_ASKER"))
    assert got["preconditions"]["status"] == "ERROR"
    hinted = _by_name(_ctx(_snap(rows=[_qa()]), super_ids={900001}))
    assert hinted["preconditions"]["status"] == "ERROR"
    assert "non-super" in hinted["preconditions"]["detail"]


def test_the_secret_check_reports_absent_values_only():
    ctx = _ctx(_snap(), log="boot " + TOKEN + " done")
    ctx.secret_values = {"BOT_TOKEN": TOKEN}
    got = _by_name(ctx)["no_secret_in_log"]
    assert got["status"] == "NOT_OBSERVED" and "BOT_TOKEN" in got["detail"]
    assert TOKEN not in got["detail"]
