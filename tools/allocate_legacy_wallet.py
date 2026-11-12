#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""تخصیصِ صریح و مجازِ موجودیِ سطلِ منجمدِ «legacy» به واحدِ پولِ قابل‌خرج (FIN-025).

پیش‌زمینه (PH-01 / Q-1c): موجودی‌های قدیمیِ بدون‌واحد (``users.credit``) به سطلِ منجمدِ
``legacy`` منتقل شدند تا بدون تصمیمِ صریح خرج یا تبدیل نشوند. این ابزار تنها مسیرِ مجاز
برای تخصیصِ آن‌ها به ``toman`` یا ``usd`` است:

  * پیش‌نمایش بدون تغییر: ``--dry-run``
  * اجرای واقعی نیازمند ``--confirm`` است؛
  * برای یک کاربر (``--user UID``) یا همهٔ دارندگانِ موجودیِ legacy (``--all``)؛
  * هر انتقال در یک تراکنشِ اتمیک همراه با رسیدِ ``credit_log`` انجام می‌شود و idempotent است.

مثال:
  python tools/allocate_legacy_wallet.py --all --cur toman --dry-run
  python tools/allocate_legacy_wallet.py --user 123456 --cur toman --confirm
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _candidates(uid: int | None):
    from newsbot.core_wallet import CUR_LEGACY
    from newsbot.db import q
    if uid is not None:
        r = q("SELECT user_id, amount FROM wallet WHERE user_id=? AND cur=? AND amount>0", (uid, CUR_LEGACY), one=True)
        return [(int(r["user_id"]), round(float(r["amount"]), 2))] if r else []
    rows = q("SELECT user_id, amount FROM wallet WHERE cur=? AND amount>0 ORDER BY user_id", (CUR_LEGACY,))
    return [(int(r["user_id"]), round(float(r["amount"]), 2)) for r in rows if float(r["amount"] or 0) > 0]


def run_allocation(*, uid: int | None = None, all_users: bool = False, cur: str, dry_run: bool = False, ref: str = "cli") -> dict:
    from newsbot.core_wallet import is_spendable, wallet_allocate_legacy, WalletError
    if not is_spendable(cur):
        raise WalletError(f"invalid target currency {cur!r}; expected 'toman' or 'usd'")
    if (uid is None) == (not all_users):
        raise ValueError("specify exactly one of uid or all_users=True")
    items = _candidates(uid)
    allocated = []
    for u, amt in items:
        if dry_run:
            allocated.append({"user_id": u, "amount": amt, "cur": cur})
        else:
            moved = wallet_allocate_legacy(u, cur, ref=ref)
            if moved > 0:
                allocated.append({"user_id": u, "amount": moved, "cur": cur})
    return {"dry_run": dry_run, "target_cur": cur, "count": len(allocated), "items": allocated}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    grp = ap.add_mutually_exclusive_group(required=True)
    grp.add_argument("--user", type=int, help="شناسهٔ کاربر برای تخصیص")
    grp.add_argument("--all", action="store_true", help="تخصیصِ همهٔ موجودی‌های مثبتِ سطلِ legacy")
    ap.add_argument("--cur", required=True, choices=("toman", "usd"), help="واحدِ پولِ مقصد")
    ap.add_argument("--ref", default="cli", help="برچسبِ مرجع در credit_log")
    ap.add_argument("--dry-run", action="store_true", help="فقط گزارش بده؛ هیچ تغییری نده")
    ap.add_argument("--confirm", action="store_true", help="تأییدِ صریح برای اجرای واقعی")
    a = ap.parse_args(argv)
    if not a.dry_run and not a.confirm:
        print("refused: pass --confirm to execute, or --dry-run to preview", file=sys.stderr)
        return 2
    try:
        res = run_allocation(uid=a.user, all_users=a.all, cur=a.cur, dry_run=a.dry_run, ref=a.ref)
    except Exception as e:
        print(f"allocation failed: {e}", file=sys.stderr)
        return 1
    print(("DRY-RUN " if a.dry_run else "ALLOCATED ") + str(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
