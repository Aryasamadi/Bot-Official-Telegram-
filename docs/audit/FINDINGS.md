# FINDINGS REGISTER — Bot-Official-Telegram-

**Audited commit:** `08bc87655dc910a98076acd749ddf8f3e6bdca2c` (branch `main`)
**Audit date:** 2026-10-20 · **Verification pass:** 2026-10-20 (PH-00) — see §13
**Status:** 184 findings — 158 `OPEN`, 5 `DEFERRED`, 1 `FIXED_UNVERIFIED`, 20 `VERIFIED_FIXED`
**Total findings: 184** — CRITICAL 1 · HIGH 26 · MEDIUM 79 · LOW 66 · INFO 12

## Category legend

`CONFIRMED BUG` · `CONFIRMED SECURITY ISSUE` · `CONFIRMED CONFIGURATION DEFECT` ·
`REPRODUCED PERFORMANCE ISSUE` · `DESIGN WEAKNESS` · `MISSING TEST / UNKNOWN BEHAVIOR` ·
`DOCUMENTATION MISMATCH` · `IMPROVEMENT OPPORTUNITY`

## Verification legend

`STATIC` = proven by code reading only · `REPRODUCED` = executed and observed in this audit ·
`RUNTIME-REQUIRED` = needs a live system to confirm the outcome.

## Index

| Domain | IDs | Count | Worst |
|---|---|---|---|
| Financial correctness & business rules | FIN-001…024 | 24 | CRITICAL |
| Security & authorization | SEC-001…016 | 16 | HIGH |
| Database & data integrity | DB-001…011 | 11 | HIGH |
| Telegram UI/UX & user journeys | UI-001…026 | 26 | HIGH |
| Content pipeline & AI generation | PIPE-001…022 | 22 | HIGH |
| Networking & transport | NET-001…008 | 8 | HIGH |
| Performance & latency | PERF-001…007 | 7 | MEDIUM |
| Publishing & exactly-once | PUB-001…005 | 5 | MEDIUM |
| CI/CD, deployment, systemd, ops docs | OPS-001…031 | 31 | HIGH |
| Testing & QA | TEST-001…014 | 14 | HIGH |
| Observability & operations | OBS-001…016 | 16 | HIGH |
| Documentation integrity | DOC-001 | 1 | LOW |

---

# 1. FINANCIAL CORRECTNESS & BUSINESS RULES (`FIN-001 … FIN-024`)

## FIN-001 — The wallet has no currency dimension: referral credit is earned in the payer's unit and spent in the referrer's unit
- **Severity / Priority / Category:** **CRITICAL** · P0 · CONFIRMED BUG
- **Evidence:** `newsbot/core_referrals.py:100-101` — `amount = round(paid * pct / 100.0, 2)` where `paid` is `pay_requests.final_price`, produced in the *payer's* tariff unit; `newsbot/core_plans.py:186` — `bal = _user_credit(uid)` (unitless `users.credit`) is compared 1:1 against the *referrer's* tariff; `newsbot/db.py:88` — the trigger adds the delta to `users.credit` with no unit tag; `newsbot/db.py:26` / `:110` — `price_num` (USD, `en`) and `price_num_fa` (Toman, `fa`) are two different currencies.
- **Actual vs Expected:** Actual — a single `REAL` column holds two currencies, so a Toman-denominated reward can be spent against a USD tariff. Expected — wallet balances carry a currency (or are restricted to one unit) and a reward is denominated in the referrer's unit.
- **Impact:** an `en` referrer accumulating a Toman reward (e.g. 600 000) applies it to a `$5` plan: `used_balance = min(600000, 5) = 5` → `payable = 0` → **USD plans obtained for free** (~100 000x over-credit). The mirror case makes the reward numerically worthless. `ref_cap_amount` is likewise compared against mixed units, so the cap is meaningless across currencies.
- **Verification:** **REPRODUCED (2026-10-20, PH-00)** — executed against an isolated temporary SQLite database using the real `record_referral` → `credit_referral_on_payment` → `upgrade_quote` code path (no repository file touched). Observed: a `fa` payer paying 600 000 Toman produced `credit_referral_on_payment -> (referrer_id, 120000.0)`; the referrer's `users.credit` became `120000.0`; `credit_log` recorded `delta=120000.0, balance_after=120000.0, reason='referral'`; the `en` referrer's quote for a `$5.0` plan returned `new_cost=5.0, balance=120000.0, used_balance=5.0, **payable=0.0**` — the USD plan is free, a **24 000x** over-credit. The mirror case was confirmed too: a `fa` referrer holding a USD-denominated reward of `1.0` reduces a 600 000 Toman purchase by 1.0 Toman (numerically worthless).
- **Root cause:** no FX table and no currency discriminator anywhere in the schema; the tariff selection itself is correct (`plan_num` deliberately does *not* fall back across units, `newsbot/core_plans.py:151-159`) — the mixing happens only at the wallet.
- **Fix:** add a currency discriminator to `users.credit` / `credit_log` / `referrals.credit_amount` (or per-currency balances) and only spend credit whose unit matches the tariff unit; make the referral cap per-currency.
- **Regression test required:** yes — referrer `lang=en`, payer `lang=fa`; the referrer then buys a `$5` plan; assert `payable > 0` and that each balance is denominated.
- **Dependencies / risks:** schema change → `SCHEMA_VERSION` bump (currently 7, `newsbot/__init__.py:9`), `MIGRATIONS` list, D1 migrate/restore `TABLES`, and a **manual allocation decision for existing balances** whose unit is unknown.
- **Owner decision (2026-10-20, Q-1 → option c):** **per-currency balances, no conversion.** The implementation is a new `wallet(user_id, cur, amount)` table (a single `credit_cur` column would only allow one currency per user and is therefore *not* equivalent to the decision); `credit_log` and `referrals` gain a currency; the referral cap is compared per currency. Existing balances are **frozen** under `cur='legacy'`, are not spendable, and are **not** auto-converted — their allocation is a separate, explicitly-authorised one-off script. Accepted product consequence: a reward is spendable only on plans priced in the same currency as the payment that produced it, and the UI must state this.
- **Status:** **VERIFIED_FIXED (2026-10-20, PH-01)** — کیف پولِ واحد‌دار: جدولِ ``wallet(user_id,cur,amount)`` + واحد روی credit_log/referrals/pay_requests؛ پاداش فقط در واحدِ خودش قابل خرج است؛ سطلِ ``legacy`` برای موجودیِ قدیمی منجمد شد (Q-1c).

## FIN-002 — The approval applies a frozen quote against a changed wallet: the customer is charged cash for credit that no longer exists
- **Severity / Priority / Category:** HIGH · P1 · CONFIRMED BUG
- **Evidence:** `newsbot/core_plans.py:225-234` — `UPDATE users SET credit=MAX(0, COALESCE(credit,0) - ? + ?)`; the *applied* `used` value comes from the frozen quote `pc["used_balance"]` captured at request time (`newsbot/ui_views_user.py:200-201`) and replayed at approval (`newsbot/core_payments.py:96,104`).
- **Actual vs Expected:** Actual — with wallet 500 000 and two pending requests each quoting `used_balance = 500 000`, approving the first zeroes the wallet; approving the second computes `MAX(0, 0-500000) = 0` → **no wallet change and no `credit_log` row** (the `if delta:` guard is falsy) while `plan_changes.credit_used` still records 500 000 and the customer is charged the full cash `payable` that assumed a 500 000 credit. Expected — the wallet share and the amount charged are re-derived atomically at approval, or the request is invalidated when the wallet changes.
- **Impact:** customer overcharged by 500 000; `plan_changes` contradicts `credit_log`; in the other direction a referral credit arriving between request and approval is not used and the customer pays more than they should.
- **Fix:** inside the approval transaction, recompute the wallet share against the live balance and re-derive `payable`; abort with `PayAlreadyDecided` if the amount differs from `final_price`.
- **Regression test required:** yes — two pending requests with a wallet; approve both; assert the wallet, `credit_log` and `plan_changes.credit_used` reconcile and that the total charged equals the total quoted.
- **Dependencies / risks:** changes the frozen-quote contract that `tests/test_payment_atomicity.py::test_approve_uses_persisted_final_price_not_requote` deliberately pins ("PAY-2"). The two contracts (frozen cash price vs. live wallet share) must be split explicitly.
- **Owner decision (2026-10-20, Q-2 → option a):** **refuse the approval on a material difference, keeping the displayed cash amount.** The cash price stays frozen (the customer is charged the number they were shown); only the **wallet share** is re-derived at approval. A material difference (new knob `PAY_QUOTE_TOLERANCE`, absolute + percentage) raises a distinct `PayQuoteStale` and offers a re-quote; a trivial difference is accepted silently.
- **Status:** **VERIFIED_FIXED (2026-10-20, PH-01)** — مبلغِ نقدی یخ‌زده می‌ماند و فقط **سهمِ کیف پول** بازمحاسبه می‌شود؛ اختلافِ مؤثر ⇒ ``PayQuoteStale`` و توقفِ تأیید (Q-2a).

## FIN-003 — Two pending requests for one user: the later approval silently overwrites the earlier paid plan
- **Severity / Priority / Category:** HIGH · P1 · CONFIRMED BUG
- **Evidence:** `newsbot/core_plans.py:74-79` — `UPDATE users SET plan_id=?, next_plan_id=NULL, ...` (plan swap, expiry preserved); `newsbot/core_payments.py:96` — `st = _assign_state(...)` is evaluated **outside** the approval transaction.
- **Actual vs Expected:** Actual — approving request B after A replaces `plan_id` with B while keeping A's expiry; the customer paid for A and ends on B (or back on the older plan). There is no refund path and the approval notification does not reveal the change of entitlement. Expected — the second purchase is queued (`next_plan_id`/`next_plan_days` exist for this) or refused.
- **Impact:** lost paid entitlement with no refund and no audit trail; the stale read is also a genuine TOCTOU — two concurrent approvals can both decide "active & different plan" from the same pre-state.
- **Fix:** move `_assign_state` inside `_trx`; implement the chain via `next_plan_id`/`next_plan_days` (currently never written — see FIN-016) or reject a conflicting second approval.
- **Regression test required:** yes — two pending requests for two different plans; approve both; assert exactly one plan is active and the second is queued or explicitly refunded.
- **Dependencies / risks:** depends on FIN-016 and on the refund design of FIN-013.
- **Status:** **VERIFIED_FIXED (2026-10-20, PH-01)** — بازمحاسبهٔ «نوعِ خرید» در زمانِ تأیید، درخواستِ دوم را رد می‌کند (پلنِ اول برقرار می‌ماند).

## FIN-004 — The advertised discounted price is not the amount charged
- **Severity / Priority / Category:** HIGH · P1 · CONFIRMED BUG
- **Evidence:** `newsbot/core_plans.py:196-214` — `used_balance = min(bal, backend)`, `payable = backend - min(bal, backend)`, then `payable = payable * (100 - disc_percent) / 100` (discount applied to the **net** payable); `newsbot/ui_views_user.py:127` — the plan page renders `discount_price(price_text, percent)` (discount applied to the **gross** tariff).
- **Actual vs Expected:** Actual — plan 3 000 000, wallet 500 000, 10 % code: the plan page shows **2 700 000**; the receipt asks for **2 750 000**. Expected — `payable + used_balance` must equal `gross x (1 - pct/100)` and the displayed number must equal the charged number.
- **Impact:** displayed price != charged price — a user-consent/pricing defect that will generate disputes and chargebacks; visible in the super-admin receipt header too (`newsbot/ui_inputs.py:456-457`).
- **Fix:** apply the discount to the gross tariff, then derive the wallet share from the discounted amount, and render the same number in the plan page, the receipt prompt and `final_price`.
- **Regression test required:** yes — quote with wallet + discount; assert `payable + used_balance == round(gross*(1-pct/100), 2)` and that the plan-page text equals the charged amount.
- **Dependencies / risks:** changes the meaning of `pc["used_balance"]`/`pc["payable"]`; interacts with FIN-002 (re-quote at approval) and with the referral base `final_price`.
- **Owner decision (2026-10-20, Q-2a + Q-4c):** the discount is applied to the **gross** tariff and the wallet share is derived from the discounted amount, so `payable + used_balance == gross x (1 - pct/100)` and the number on the plan page equals the number charged. A plan with no numeric tariff is **not online-purchasable** and states that its price is negotiable.
- **Status:** **VERIFIED_FIXED (2026-10-20, PH-01)** — تخفیف روی **ناخالص** اعمال می‌شود و سهمِ کیف پول از مقدارِ تخفیف‌خورده می‌آید ⇒ ``payable + used_balance == ناخالص×(۱-درصد)``؛ صفحهٔ پلن همان ``payable`` را نشان می‌دهد.

## FIN-005 — A paying customer can claim the free plan and lose their paid entitlements
- **Severity / Priority / Category:** MEDIUM · P2 · CONFIRMED BUG
- **Evidence:** `newsbot/ui_views_user.py:131,149-157` — the free-plan button is shown while a paid plan is active; `do_free` checks only `u["free_used"]`, never `admin_limits(uid)["active"]`; `newsbot/core_plans.py:66-79` — the plan-swap branch keeps the paid expiry.
- **Actual vs Expected:** Actual — the customer silently drops to 1 post / 2 sources / 1 channel per day while keeping the paid expiry, and `free_used` is burnt. Expected — claiming the free plan is refused while a paid plan is active and `free_used` is not consumed.
- **Impact:** paid entitlement loss; the only recovery is to buy again.
- **Fix:** refuse in `do_free` and in `view_plan` when a paid plan is active; do not consume `free_used`.
- **Regression test required:** yes — paid plan active + `free_used=0` -> refuse, `free_used` stays 0.
- **Dependencies / risks:** decide the canonical paid->free rule first (interacts with FIN-016 and `s:ufree2`).
- **Status:** **VERIFIED_FIXED (2026-10-20, PH-01)** — در ``do_free`` اگر پلنِ فعالِ پولی وجود داشته باشد، فعال‌سازیِ رایگان رد می‌شود و ``free_used`` مصرف نمی‌شود.

## FIN-006 — `credit_log.balance_after` is a Python-rounded recomputation, and a sub-cent movement changes the wallet with no receipt
- **Severity / Priority / Category:** MEDIUM · P2 · DESIGN WEAKNESS
- **Evidence:** `newsbot/core_plans.py:225-232` — the SQL stores the **unrounded** value (`MAX(0, COALESCE(credit,0) - ? + ?)`), the receipt stores a **rounded** value, and the receipt is written only `if delta:` (a rounded delta of `0` or `-0.0` is falsy).
- **Actual vs Expected:** Actual — `credit_log.balance_after` can differ from `users.credit` by up to 0.005 per movement, `SUM(credit_log.delta)` cannot reconstruct the balance, and a movement whose rounded delta is zero changes the wallet **with no ledger entry at all**. Expected — the stored balance is rounded at write time and the receipt records the value actually stored.
- **Impact:** the wallet ledger is decorative, not authoritative; it cannot answer "what is this customer's balance and why". No production code reads `credit_log` today.
- **Fix:** `UPDATE users SET credit=ROUND(MAX(0, ...), 2)` and write the receipt from the value read back in the same transaction; always write a receipt when the stored value changed.
- **Regression test required:** yes — property test: for N random movements, `users.credit == SUM(credit_log.delta)` and `balance_after` is monotone per user.
- **Dependencies / risks:** changing stored precision changes existing wallet values -> needs a one-off reconciliation migration.
- **Status:** **VERIFIED_FIXED (2026-10-20, PH-01)** — مقدارِ ذخیره‌شده گرد (۲ رقم) می‌شود و رسید دقیقاً همان مقدارِ ذخیره‌شده را می‌نویسد ⇒ جمعِ رسیدها == موجودی، همیشه.

## FIN-007 — Purchases that do not move the wallet leave no `plan_changes` row: there is no purchase history
- **Severity / Priority / Category:** MEDIUM · P2 · DOCUMENTATION MISMATCH / DESIGN WEAKNESS
- **Evidence:** `newsbot/core_plans.py:223-234` — the `INSERT INTO plan_changes(...)` sits **inside** the `if used or surplus:` guard. Corroborated by `tests/test_payment_atomicity.py:84`, which asserts `plan_changes` count == 0 after a successful purchase.
- **Actual vs Expected:** Actual — a normal purchase writes nothing, so `plan_changes.paid`, `rem_days` and `kind` are never populated. Expected — one history row per applied payment.
- **Impact:** the table cannot answer "what did user X pay on date Y?"; combined with FIN-014 there is **no durable purchase ledger** at all.
- **Fix:** always insert the `plan_changes` row; keep the guard only for `credit_log`.
- **Regression test required:** yes — a purchase with no wallet movement must produce exactly one `plan_changes` row with `paid` = the charged amount.
- **Dependencies / risks:** the two existing assertions (`plan_changes` count == 0 / == 1) must be updated deliberately, not silently.
- **Status:** **VERIFIED_FIXED (2026-10-20, PH-01)** — ردیفِ ``plan_changes`` برای **هر** پرداختِ تأییدشده ثبت می‌شود (نه فقط وقتی کیف پول جابه‌جا شود).

## FIN-008 — Discount validity is re-checked for capacity only at approval: expired or deactivated codes are still honoured
- **Severity / Priority / Category:** MEDIUM · P2 · CONFIRMED BUG
- **Evidence:** `newsbot/core_payments.py:101-103` — `UPDATE discounts SET used=used+1 WHERE code=? AND (max_uses=0 OR used<max_uses)` (capacity only); `newsbot/core_discounts.py:22-38` — `disc_valid()` (active / expired / exhausted) is called only in the *user* flow (`newsbot/ui_views_user.py:116-117`, `newsbot/ui_inputs.py:428`).
- **Actual vs Expected:** Actual — a code deactivated (`s:disc_t`) or expired between request and approval is still applied. Expected — the approval re-validates the code or the request records an explicit validity decision with an expiry.
- **Impact:** revoking a leaked promo code is ineffective for in-flight receipts — a direct business loss the operator believes they prevented.
- **Fix:** add `AND active=1 AND (expires IS NULL OR expires > ?)` to the approval-time UPDATE, using the canonical `iso_fixed` format (see FIN-022).
- **Regression test required:** yes — deactivate/expire a code after the receipt is created, then approve.
- **Dependencies / risks:** low.
- **Status:** **VERIFIED_FIXED (2026-10-20, PH-01)** — کدِ تخفیف در زمانِ تأیید دوباره اعتبارسنجی می‌شود (فعال/منقضی/ظرفیت) ⇒ ``PayDiscountInvalid``.

## FIN-009 — The referral reward is granted outside the payment transaction, with no reconciler
- **Severity / Priority / Category:** MEDIUM · P2 · DESIGN WEAKNESS
- **Evidence:** `newsbot/ui_views_super.py:275-287` — `pay_approve_atomic(rid)` commits, **then** `credit_referral_on_payment(...)` runs in a separate `_trx` inside a `try/except` that only logs. Nothing re-drives it; `recover_stuck_payments` (`newsbot/core_plans.py:246-249`) only handles `processing`. The `settings` idempotency markers (`refpaid:pay:<rid>`) are **never purged** (`newsbot/core_articles.py:284-291` deletes no `settings` rows).
- **Actual vs Expected:** Actual — a crash between the payment commit and the referral transaction loses the reward permanently; the referrer is told nothing. Expected — the reward is part of the payment transaction, or a reconciler grants missing rewards.
- **Impact:** silent, permanent loss of the referrer's money; unbounded growth of the marker table.
- **Fix:** move the credit into the approval transaction (the trigger already makes it atomic) **or** add a reconciler in `scheduler._recover_state`; give the marker rows a TTL in `cleanup()`.
- **Regression test required:** yes — simulate a crash between the commit and the referral call; assert the reward is granted exactly once after recovery.
- **Dependencies / risks:** moving it inside couples two subsystems — the current "a referral failure never breaks the payment" property (`newsbot/ui_views_super.py:275-276`) must be preserved via the reconciler instead.
- **Status:** **VERIFIED_FIXED (2026-10-20, PH-01)** — پاداشِ ارجاع **داخلِ همان تراکنشِ تأیید** اعطا می‌شود ⇒ کرش دیگر پاداش را برای همیشه از بین نمی‌برد.

## FIN-010 — The referral cap is a read-then-write with no conditional UPDATE, and is compared against mixed-unit amounts
- **Severity / Priority / Category:** MEDIUM · P2 · DESIGN WEAKNESS
- **Evidence:** `newsbot/core_referrals.py:116-133` — `SELECT SUM(credit_amount) ...` then an unconditional `UPDATE referrals SET credited=1, credit_amount=...`; **no** `AND credited=0` guard and no `rowcount` check. On D1 the `SELECT` executes **outside** the batch (`newsbot/db.py:226-227`).
- **Actual vs Expected:** Actual — on the D1 backend the read-modify-write is not atomic and the cap can be exceeded by concurrent credits; `credited` is not a "has been paid" flag (rows with `credited=1, credit_amount=0` exist — `newsbot/core_referrals.py:97,104`). Expected — one conditional statement with a rowcount check.
- **Impact:** cap bypass on D1; misleading semantics on SQLite; the cap is meaningless across currencies (FIN-001).
- **Fix:** a single conditional `UPDATE ... WHERE referred_id=? AND (SELECT SUM(...)) + ? <= ?` with a rowcount check, or an explicit per-referrer lock; align `credited` semantics.
- **Regression test required:** yes — the SQLite case exists (`tests/test_phase1_fixes.py:49-72`); add a D1-simulated concurrent case.
- **Dependencies / risks:** depends on the D1 reopening checklist in `docs/D1_DECISION.md` and on FIN-001 (a per-currency cap needs a per-currency SUM).
- **Owner decision (2026-10-20, Q-1c + Q-3a):** the cap becomes **per-currency** and **atomic** (a single conditional statement with a rowcount check), and the `credited` flag is aligned with its meaning. Q-3a keeps the reward **per purchase** — the cap is the only bound, so making it per-currency and atomic is the whole fix.
- **Status:** **VERIFIED_FIXED (2026-10-20, PH-01)** — سقفِ ارجاع **به تفکیکِ واحد** و با یک دستورِ شرطیِ اتمیک اعمال می‌شود؛ واحد در ``referrals.credit_cur`` ثبت می‌گردد.

## FIN-011 — The D1 approval path is not atomic; `recover_stuck_payments` ignores `applied_at`
- **Severity / Priority / Category:** MEDIUM · P2 · CONFIRMED BUG (D1 only; the backend is officially unsupported per `docs/D1_DECISION.md:8-16`) + DOCUMENTATION MISMATCH
- **Evidence:** `newsbot/core_payments.py:113-134` (`_pay_approve_d1`) — `pay_claim` -> `disc_use` -> `apply_plan_change` (own `_trx`) -> `assign_plan` (own `_trx`) -> `_apply_applied_at` -> `pay_set`; `newsbot/core_plans.py:246-249` — the recovery UPDATE filters on `status='processing'` only.
- **Actual vs Expected:** three crash windows: (W-2) wallet debited, no plan granted, re-approval debits again; (W-3) plan applied but `applied_at` NULL -> recovery returns it to `pending` -> the plan is applied **again** and the wallet debited **again**; (W-c) `assign_plan` returns `exp is None` -> the wallet is debited for a plan never granted, with no reversal. `core_payments.py:73-74` claims the opposite. Expected — one transaction, or an `applied_at`-aware recovery.
- **Impact:** double wallet debit and double plan grant on the D1 path; the recovery routine is *dangerous* there while being dead code on SQLite.
- **Fix:** make `recover_stuck_payments` refuse rows with `applied_at IS NOT NULL`; wrap the D1 approval in one batch; refund the wallet when `exp is None`.
- **Regression test required:** yes — monkeypatch `assign_plan` / `_apply_applied_at` to raise and assert no double apply and no double debit.
- **Dependencies / risks:** D1 is unsupported; the cheapest mitigation is an `applied_at`-aware recovery plus a hard startup guard for `DB_BACKEND=d1`.
- **Owner decision (2026-10-20, Q-2a):** the D1 approval path is brought under the same contract — one transaction, or an `applied_at`-aware recovery — because Q-2a makes the approval re-derive and refuse, and that logic must not exist in two divergent forms.
- **Status:** OPEN

## FIN-012 — `price_num` / `price_num_fa` accept `inf` / `1e400`: an infinite tariff permanently corrupts a referrer's wallet
- **Severity / Priority / Category:** MEDIUM · P2 · CONFIRMED BUG
- **Evidence:** `newsbot/ui_inputs.py:144-150` — `float(text...)` with a `< 0` check but **no** `math.isfinite` and no upper bound; `newsbot/core_plans.py:151-159`; `newsbot/core_referrals.py:101` — `round(inf * pct/100, 2) = inf`.
- **Actual vs Expected:** Actual — a super-admin typing `inf` (or `1e400`) yields `payable = inf` -> `final_price = "inf"` -> referral reward `inf` -> the trigger sets the referrer's `users.credit = inf` **permanently**; any later quote sees `bal = inf` -> `used_balance = new_num` -> **all future plans free**. (`nan` is benign: SQLite stores it as NULL.) Expected — a finite, bounded tariff.
- **Impact:** a single super-admin input permanently gives one referrer unlimited free plans.
- **Fix:** `if not math.isfinite(value) or value < 0 or value > MAX_TARIFF: invalid(...)` at input time and `math.isfinite` guards in `plan_num` / `upgrade_quote` / `credit_referral_on_payment`.
- **Regression test required:** yes — inputs `inf`, `1e400`, `nan`, `-inf`, `1e15`; assert rejection and that no `inf` can reach `users.credit`.
- **Dependencies / risks:** super-admin-only input; also add a one-off DB scan for `credit = inf`.
- **Status:** **VERIFIED_FIXED (2026-10-20, PH-01)** — تعرفهٔ نامتناهی/بی‌کران در ``tariff()`` نادیده گرفته می‌شود ⇒ هرگز وارد کیف پول نمی‌گردد.

## FIN-013 — No refund or compensation path exists, while the README claims money operations are "atomic and reversible"
- **Severity / Priority / Category:** MEDIUM · P2 · DOCUMENTATION MISMATCH / MISSING FEATURE
- **Evidence:** `README.md:79` claims every money operation is atomic and reversible; the only reversal primitives are `revoke_plan` (`newsbot/core_plans.py:113-115`, removes the entitlement but keeps the spent credit), `s:ufree2` and `disc_refund`.
- **Actual vs Expected:** Actual — no automated or documented way to compensate a wrong, duplicate or over-charged payment; manual compensation by direct SQL creates **no `credit_log` row** and breaks the ledger. Expected — a `refund_payment(rid, amount)` that writes a `credit_log` receipt with `reason='refund'`.
- **Impact:** every defect in FIN-002/003/011 becomes a manual, unaudited SQL operation.
- **Fix:** implement the refund path (or correct the README claim if refunds are out of scope).
- **Regression test required:** yes — refund a payment; assert the wallet is restored, a receipt exists and `SUM(credit_log.delta) == users.credit`.
- **Dependencies / risks:** depends on FIN-006 (ledger correctness).
- **Status:** **VERIFIED_FIXED (2026-10-20, PH-01)** — ``refund_payment()`` اضافه شد: بازگشتِ وجه به کیف پولِ همان واحد + رسیدِ ``reason='refund'`` + ردیفِ سابقه.

## FIN-014 — The receipt/decision trail is purged after 90 days while the wallet ledger is kept forever
- **Severity / Priority / Category:** LOW · P3 · DESIGN WEAKNESS
- **Evidence:** `newsbot/core_articles.py:284-286` — `DELETE FROM pay_requests WHERE status!='pending' AND COALESCE(decided_at, created_at) < ?-90d`; `credit_log` / `plan_changes` have no TTL; `credit_log.ref` holds a *plan id* or a *referred user id* — never the payment id.
- **Actual vs Expected:** Actual — after 90 days a charge cannot be reconstructed and no wallet movement can ever be joined to the payment that caused it. Expected — consistent retention and a stable external reference id.
- **Impact:** disputes become unanswerable from the app.
- **Fix:** keep `pay_requests` as long as the ledger; store `pay:<rid>` in `credit_log.ref`.
- **Regression test required:** no (retention policy); add a traceability test once `ref` carries the id.
- **Dependencies / risks:** storage growth; coordinate with `DATA_TTL_HOURS` / `ARCHIVE_DAYS`.
- **Status:** OPEN

## FIN-015 — Money is stored as REAL/TEXT with banker's rounding; there is no minor-unit type
- **Severity / Priority / Category:** LOW · P2 · IMPROVEMENT OPPORTUNITY
- **Evidence:** `newsbot/db.py:44-47` — `plan_changes.credit_used REAL`, `credit_log.delta REAL`, `pay_requests.final_price TEXT`; `newsbot/core_plans.py:196-214`; `newsbot/core_referrals.py:101`.
- **Actual vs Expected:** Actual — all money is an IEEE-754 double rounded with Python's `round()` (half-to-even, not commercial half-up); a 20 % reward on 99.99 is `19.998 -> 20.00` (a systematic 0.002 overpay per transaction); `final_price` mixes a number-string with free text, parsed heuristically at `newsbot/ui_views_super.py:279-281`. Expected — integer minor units or `Decimal` with `ROUND_HALF_UP`.
- **Impact:** sub-cent drift accumulates; the ledger is not exactly reconstructible.
- **Fix:** integer minor units (Toman / USD-cents) or `Decimal` with `ROUND_HALF_UP`; make `final_price` numeric and move the free text to a separate field.
- **Regression test required:** yes — property test over 10 000 random (price, pct) pairs.
- **Dependencies / risks:** large refactor across `core_plans`, `core_referrals`, `core_discounts`, `core_payments`, `db`; best done together with FIN-001 and FIN-006.
- **Status:** **DEFERRED (2026-10-20, PH-01)** — مبالغ همچنان REAL با گردکردنِ half-even هستند. تغییر به واحدِ کوچک/Decimal یک بازآراییِ بزرگ است و به فازِ بعدی موکول شد (اصلاحِ interim = FIN-006 که انجام شد).

## FIN-016 — `next_plan_id` / `next_plan_days` are never written: the documented "chained plan" is not implemented
- **Severity / Priority / Category:** LOW · P3 · DOCUMENTATION MISMATCH
- **Evidence:** `newsbot/core_plans.py:85-88` — only `next_plan_id=NULL, next_plan_days=NULL` are ever written; `newsbot/scheduler.py:29,36` filters on them; the `queued` return flag is always `False`, so the UI's `pay_when_queued` message is unreachable dead text.
- **Actual vs Expected:** Actual — the docstring describes queuing the "next plan"; the code swaps the plan immediately. Expected — implement the queue or correct the docstring.
- **Impact:** maintainer/operator wrong expectations; dead UI text.
- **Fix:** implement the chain (applied at expiry by `scheduler._plan_notifications`) or remove the columns, the flag and the docstring.
- **Regression test required:** only if implemented.
- **Dependencies / risks:** natural home for FIN-003.
- **Status:** **VERIFIED_FIXED (2026-10-20, PH-01)** — دک‌استرینگِ ``assign_plan`` اصلاح شد: «زنجیرهٔ پلن» صریحاً پیاده‌نشده اعلام شد (ستون‌ها و پرچمِ ``queued`` بی‌استفاده‌اند).

## FIN-017 — The daily-usage CAS insert path bypasses the cap
- **Severity / Priority / Category:** LOW · P3 · CONFIRMED BUG
- **Evidence:** `newsbot/core_articles.py:122-130` and `newsbot/core_users.py:116-119` — `INSERT INTO usage(...) VALUES(...,1,0) ON CONFLICT(...) DO UPDATE SET posts=posts+1 WHERE posts < ?`. The `WHERE` guard applies only to the **update** branch; the **insert** branch writes `1` unconditionally.
- **Actual vs Expected:** Actual — with `daily_posts = 0` (no active plan) or `AI_DAILY_FREE = 0` (configurable, unbounded — `newsbot/config.py:128` has no clamp) the first reservation of the day writes a counter of 1 **above** the cap; the second attempt then hits the guarded branch and is refused. Expected — the insert branch respects the cap.
- **Impact:** the invariant "counter <= cap" is violated; the counter shown to the user ("used 1 / cap 0") contradicts the cap.
- **Fix:** conditional insert (`INSERT ... SELECT ... WHERE cap > 0`) or clamp `AI_DAILY_FREE/PAID` to >= 1.
- **Regression test required:** yes — `cap=0` -> no usage row.
- **Dependencies / risks:** none.
- **Status:** OPEN

## FIN-018 — The channel-level daily post cap is enforced only by a read-then-act pre-check
- **Severity / Priority / Category:** LOW · P3 · DESIGN WEAKNESS
- **Evidence:** `newsbot/core_users.py:163-174` (the channel cap folded into `remaining()`, a read); `newsbot/ui_dispatch.py:145-148` (the check runs *before* the publish, which then takes the channel lock); the plan cap itself is atomic (`newsbot/core_articles.py:124`).
- **Actual vs Expected:** Actual — two publishes on *different* channels of the same admin can both pass the pre-check and both publish, exceeding the channel cap by 1. Expected — a CAS reservation like the plan cap.
- **Impact:** the channel's daily cap is soft, not hard.
- **Fix:** reserve the channel cap in the same transaction as the plan cap (a dedicated counter column), or document the soft semantics.
- **Regression test required:** optional — two-thread test at the cap boundary.
- **Dependencies / risks:** `posted` is the source of truth and is pruned after 90 days, so a counter column is preferable.
- **Status:** OPEN

## FIN-019 — The downgrade block is enforced only at request time; the approval never re-checks it
- **Severity / Priority / Category:** LOW · P3 · DESIGN WEAKNESS
- **Evidence:** `newsbot/core_plans.py:200-204` (quote-time `kind="blocked"`); `newsbot/ui_views_user.py:179-180` (the request path refuses it); `newsbot/core_payments.py:96-108` (the approval applies whatever plan id is stored).
- **Actual vs Expected:** Actual — if the tariff is edited between the receipt and the approval, an approval that was an upgrade at request time can become a downgrade at approval time. The rule is also *rate-based* (`new_num/new_days < cur_num/cur_days`), which is undocumented. Expected — the approval re-validates.
- **Impact:** the customer can be granted less than they paid for.
- **Fix:** re-evaluate the quote inside the approval transaction and reject if the kind/payable no longer matches `final_price`; document the rate-based rule.
- **Regression test required:** yes — change the tariff after the receipt, then approve.
- **Dependencies / risks:** depends on FIN-002's re-quote design.
- **Status:** **VERIFIED_FIXED (2026-10-20, PH-01)** — قاعدهٔ نزول در زمانِ تأیید دوباره ارزیابی می‌شود ⇒ تأییدِ نزولِ تازه متوقف می‌شود.

## FIN-020 — `s:plan_free` clears `is_free` on **all** plans before re-marking one
- **Severity / Priority / Category:** LOW · P3 · DESIGN WEAKNESS
- **Evidence:** `newsbot/ui_dispatch.py:285` — `q("UPDATE plans SET is_free=0", commit=True); update_plan(int(c), is_free=1)`.
- **Actual vs Expected:** Actual — a single toggle converts the seeded base Free plan (`newsbot/core_plans.py:16-17`) into a paid plan and, if the chosen plan is not that one, leaves **no plan free at all** -> the free onboarding path (`u:free:<pid>`) becomes unreachable, with no audit trail. Expected — only the targeted plan changes.
- **Impact:** a one-click action silently removes free onboarding for every customer.
- **Fix:** `UPDATE plans SET is_free=0 WHERE id<>?` and log the change.
- **Regression test required:** yes — toggle on a paid plan; assert the others are unchanged.
- **Dependencies / risks:** super-admin only; low.
- **Status:** OPEN

## FIN-021 — The approval notification is best-effort; the DB can say `approved` while the user is never told
- **Severity / Priority / Category:** LOW · P3 · DESIGN WEAKNESS
- **Evidence:** `newsbot/ui_widgets.py:618-624` — `except Exception as e: log.warning("notify user %s: %s", ...)`; called from `newsbot/ui_views_super.py:288` after the commit; `pay_requests` has no `notified_at`.
- **Actual vs Expected:** Actual — the customer pays, the DB is `approved`, and the customer never learns it. Expected — a durable "notification owed" marker with retry.
- **Impact:** support tickets and perceived payment loss.
- **Fix:** add `pay_requests.notified_at`, set it only after a successful DM, and re-send from `scheduler._recover_state`.
- **Regression test required:** yes — simulate a failing `send_message` and assert the retry path.
- **Dependencies / risks:** low.
- **Status:** **VERIFIED_FIXED (2026-10-20, PH-01)** — ستونِ ``notified_at`` + ``pay_mark_notified`` + ``pay_unnotified`` و تلاشِ دوباره در هر تیکِ زمان‌بندی (``_renotify_payments``).

## FIN-022 — `recover_stuck_payments` mixes timestamp formats in a string comparison, and is effectively dead code on SQLite
- **Severity / Priority / Category:** LOW · P3 · DESIGN WEAKNESS
- **Evidence:** `newsbot/core_plans.py:246-249` — `cutoff = (now_utc() - timedelta(minutes=...)).isoformat()` (drops `.ffffff` when `microsecond == 0`) compared against `claimed_at`, which is always written with `now_iso()` -> `iso_fixed` (6 fractional digits).
- **Actual vs Expected:** Actual — mixed formats in a lexicographic comparison (up to 1 s of early recovery); on SQLite a `processing` row cannot exist (the claim CAS lives inside the approval `_trx`, `newsbot/core_payments.py:99`) so the routine is dead code there while being *dangerous* on D1 (FIN-011). Expected — both sides canonical (`iso_fixed`) and an `applied_at` predicate.
- **Impact:** format inconsistency that will bite at a format change; a recovery routine that is simultaneously dead and dangerous.
- **Fix:** use `iso_fixed(...)`; add `AND applied_at IS NULL`; make the routine a no-op assertion on SQLite.
- **Regression test required:** yes — a `processing` row with `applied_at` set must not return to `pending`.
- **Dependencies / risks:** none.
- **Status:** OPEN

## FIN-023 — `REFERRAL_MAX_PER_USER` is dead but still asserted by the test suite as if it were a safety cap
- **Severity / Priority / Category:** LOW · P3 · DOCUMENTATION MISMATCH
- **Evidence:** `newsbot/config.py:145-148` (documented as deprecated; "no logic path uses it") and `tests/test_business_logic.py:15,97-99` (asserts only that it exists and is >= 0); `.env.example:90-91` still shows it.
- **Actual vs Expected:** Actual — an operator may believe a per-referrer **count** cap is enforced; only an amount cap is, and the reward is paid on *every* purchase. Expected — remove the knob and the test, or implement the cap.
- **Impact:** a false sense of anti-abuse protection on a reward that recurs per purchase.
- **Fix:** delete the knob and its test; state "reward on every purchase, capped by amount only" in the super panel.
- **Regression test required:** no.
- **Dependencies / risks:** none.
- **Owner decision (2026-10-20, Q-3a):** the reward stays **per purchase at the same rate** — this is a *product* decision, not a defect. The knob and its test are removed and the super-admin panel wording is corrected so no operator believes a per-referrer **count** cap exists.
- **Status:** **VERIFIED_FIXED (2026-10-20, PH-01)** — ``REFERRAL_MAX_PER_USER`` از config حذف شد؛ README و ``.env.example`` و تستِ ``final_verification`` هم‌راستا شدند.

## FIN-024 — Missing regression coverage for the monetary boundary/precision cases and ledger reconciliation
- **Severity / Priority / Category:** MEDIUM · P2 · MISSING TEST / UNKNOWN BEHAVIOR
- **Evidence:** `tests/test_phase11_limits_boundaries.py` covers quota/discount boundaries but **no** monetary-field bound, no frozen-quote approval, no concurrent-request case, no displayed-vs-charged identity, no ledger reconciliation, no cross-unit wallet, no D1 crash window. `tests/test_payment_atomicity.py:84` and `:223-232` actively *pin* the two defective behaviours.
- **Actual vs Expected:** Actual — every CONFIRMED BUG in this section is invisible to CI. Expected — a regression test per bug.
- **Impact:** regressions in the money path are not caught; the defective behaviours are "protected" by tests.
- **Fix:** add the missing cases; update the two assertions that encode the defective contract.
- **Regression test required:** yes (this *is* the coverage finding).
- **Dependencies / risks:** FIN-002/FIN-004 fixes will intentionally break `test_approve_uses_persisted_final_price_not_requote` — update it to the new contract.
- **Status:** OPEN

---

# 2. SECURITY & AUTHORIZATION (`SEC-001 … SEC-016`)

## SEC-001 — AI-provider requests bypass all SSRF validation and follow redirects to private hosts
- **Severity / Priority / Category:** HIGH · P1 · CONFIRMED SECURITY ISSUE
- **Evidence:** `newsbot/ai_models.py:93-95` — `http().post(url, timeout=httpx.Timeout(120, connect=15), **kw)` with **no** `public_url_strict` call; `newsbot/net_http.py:26-27` — the shared client is built with `follow_redirects=True`; `newsbot/ui_inputs.py:92` validates the host **only at input time** (a string test) and never at request time.
- **Actual vs Expected:** Actual — role `admin` is granted automatically by the free plan (`newsbot/core_plans.py:81`) and the free plan is self-service (`newsbot/ui_views_user.py:149`), so **any anonymous Telegram user** can add a personal model with an arbitrary `base_url`; a 302 to `http://169.254.169.254/` is followed with no validation, and the response body of a >= 400 target is surfaced to super admins (`newsbot/ai_models.py:153-157` -> `notify_super`). Expected — every outbound request derived from user input is validated and every redirect hop is re-validated (as `newsbot/net_http.py:154,164` and `newsbot/publish.py:57,64` already do).
- **Impact:** blind SSRF from an untrusted tenant into the operator's network/cloud metadata, with a status oracle usable for internal port scanning; a provider API key may be forwarded across the redirect (see NET-002).
- **Fix:** route provider calls through a guarded requester: validate the resolved host, set `follow_redirects=False`, follow up to 5 hops manually, re-validating each, and strip `Authorization`/`x-api-key`/`apikey` on a host change.
- **Regression test required:** yes — mock a `base_url` that 302s to `169.254.169.254`; assert no request reaches it and no credential header crosses the hop.
- **Dependencies / risks:** providers that legitimately redirect must keep working.
- **Status:** **VERIFIED_FIXED (2026-10-20, PH-02)** — مسیرِ provider از `fetch_post` عبور می‌کند: هر مقصد (و هر redirect) با `egress_url_strict` اعتبارسنجی می‌شود و `follow_redirects=False` است ⇒ یک `base_url` دلخواه دیگر نمی‌تواند به میزبانِ داخلی/metadata برسد.

## SEC-002 — The prompt-injection fence is breakable through the article URL
- **Severity / Priority / Category:** MEDIUM · P1 · CONFIRMED SECURITY ISSUE
- **Evidence:** `newsbot/generate.py:52-55` — `safe_text, safe_title = strip_injection(text), strip_injection(art['title'])` but the URL is interpolated **raw**: `SOURCE: {art['url']}`; `newsbot/discover.py:27` (`clean_url`) does not percent-encode `<`/`>`; `newsbot/core_articles.py:35` stores it unchanged.
- **Actual vs Expected:** Actual — a URL containing `</article_data>` closes the "untrusted data" box and the rest of the URL is read by the model as instructions (reproduced: the prompt then contains two `<article_data>` / two `</article_data>` markers). Expected — every field inside the fence is neutralised by `strip_injection` (which explicitly matches `</article_data>` — `newsbot/text_format.py:24`).
- **Impact:** a malicious or compromised feed can inject instructions into the generation prompt; the output is still HTML-sanitised and ad-filtered, but its *content* becomes attacker-influenced and is published to the operator's channel.
- **Fix:** apply `strip_injection` to the URL as well; strip `<`/`>` at ingest.
- **Regression test required:** yes — extend `tests/test_security.py::test_build_prompt_keeps_single_data_boundary` with a URL containing `</article_data>` and assert exactly one boundary pair.
- **Dependencies / risks:** percent-encoding at ingest changes the stored URL and therefore the dedup hash.
- **Status:** **VERIFIED_FIXED (2026-10-20, PH-02)** — نشانیِ مقاله هم با `strip_injection` خنثی می‌شود و در ورودی، نویسه‌های زاویه‌ای از URL حذف می‌شوند ⇒ مرزِ `<article_data>` دیگر با URL قابل بستن نیست.

## SEC-003 — Customer source-API keys are stored in plaintext (SEC-09 encryption covers only `ai_models.api_key`)
- **Severity / Priority / Category:** LOW · P2 · DESIGN WEAKNESS
- **Evidence:** `newsbot/core_sources.py:44-46` — `q("UPDATE sources SET api_url=?, api_key=?, ...")` writes the customer's key verbatim; `newsbot/secret_store.py:67-87` (`migrate_ai_keys`) touches only `ai_models`.
- **Actual vs Expected:** Actual — customer feed credentials are cleartext in the SQLite file (mode 0600, but see SEC-004). Expected — the documented SEC-09 at-rest control applies to API keys.
- **Impact:** third-party customer credentials exposed if the DB file is copied (backups, archive snapshots).
- **Fix:** encrypt on write, decrypt at the single read site (`newsbot/discover.py:272-278`), and extend the startup migration to `sources.api_key`.
- **Regression test required:** yes — assert the raw column holds no plaintext and the request still carries the original value; the migration must be idempotent.
- **Dependencies / risks:** existing plaintext rows must be migrated transactionally; `decrypt()` must fail closed (it already returns `""` on error).
- **Status:** OPEN

## SEC-004 — SQLite WAL/SHM sidecars and in-app archive backups are created without restrictive permissions
- **Severity / Priority / Category:** LOW · P2 · CONFIRMED SECURITY ISSUE
- **Evidence:** `newsbot/db.py:319-321` — `os.chmod(config.DB_FILE, 0o600)` covers **only** the main file; `newsbot/core_articles.py:209-215` (`archive_backup`) writes full snapshots with the default 0644 into a 0755 directory, while `deploy/deploy.sh:57` *does* `chmod 600` its own backups.
- **Actual vs Expected:** Actual — `newsbot.db-wal` and `newsbot.db-shm` (same data, including freshly written API keys) and every archive snapshot are world-readable under the default umask. Expected — every artefact holding DB contents is owner-only, as the code comment itself asserts.
- **Impact:** on a multi-account host, other local users can read the wallet and API keys.
- **Fix:** chmod the sidecars at connection open; chmod archive backups 0600 and create the directory 0700.
- **Regression test required:** yes — assert the mode of the backup file and directory.
- **Dependencies / risks:** negligible.
- **Status:** OPEN

## SEC-005 — At-rest key encryption uses an unsalted single SHA-256 of an operator-chosen string
- **Severity / Priority / Category:** LOW · P2 · DESIGN WEAKNESS
- **Evidence:** `newsbot/secret_store.py:25` — `Fernet(base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest()))`; `.env.example:94-97` says only "any long string".
- **Actual vs Expected:** Actual — one unsalted SHA-256; a low-entropy `SECRETS_KEY` is brute-forceable offline against a stolen `ai_models` table (one SHA-256 + one Fernet attempt per guess). Expected — a stretched KDF (scrypt/PBKDF2) or a random 32-byte key.
- **Impact:** weakens the SEC-09 at-rest control.
- **Fix:** derive with `scrypt`/`pbkdf2_hmac` using a persisted random salt, or accept a base64 32-byte key; version the ciphertext prefix (`enc:v2:`) to keep old data readable.
- **Regression test required:** yes — round-trip under the new KDF plus a compatibility path.
- **Dependencies / risks:** changing the derivation invalidates existing ciphertexts without a version tag.
- **Status:** OPEN

## SEC-006 — AI-key encryption is optional and silently degrades to plaintext
- **Severity / Priority / Category:** LOW · P2 · DESIGN WEAKNESS
- **Evidence:** `newsbot/secret_store.py:41-42` (`if f is None: return value`), `:71-75` (only a WARNING), `.env.example:97` (`#SECRETS_KEY=` commented out), and `newsbot/app.py:114-170` (`preflight()` never mentions `SECRETS_KEY`, although it hard-fails on a missing `SUPER_ADMIN_IDS`).
- **Actual vs Expected:** Actual — an operator can believe the keys are encrypted when they are not. Expected — a new deployment is warned or forced to set the key.
- **Impact:** the documented security control is opt-in and unenforced.
- **Fix:** add `SECRETS_KEY` to the preflight warning list and report the number of plaintext rows at startup; make it fatal only for a fresh database.
- **Regression test required:** yes — no `SECRETS_KEY` -> warning; with the key -> no warning.
- **Dependencies / risks:** making it fatal breaks existing installs.
- **Status:** OPEN

## SEC-007 — `ALLOW_PRIVATE_MODEL_HOSTS=1` removes **all** private-host filtering for AI providers
- **Severity / Priority / Category:** MEDIUM · P2 · DESIGN WEAKNESS
- **Evidence:** `newsbot/ui_inputs.py:59-60,92` — `if not allow_private and is_private_host(host): raise`; `allow_private=(field == "base_url" and _ALLOW_PRIVATE_MODELS)`; `newsbot/config.py:154-155`.
- **Actual vs Expected:** Actual — a single global boolean admits `169.254.169.254`, any RFC1918 host, `127.0.0.1` on any port and IPv6 link-local, for **every** admin on the installation; combined with SEC-001 (redirects never validated) it reaches hosts that are not even "private" by the check. Expected — an explicit allowlist of local provider endpoints.
- **Impact:** an internal **read** primitive (the error body of a >= 400 target reaches the super-admin channel).
- **Fix:** replace the boolean with a `host:port` allowlist; keep the old behaviour only behind an explicit `=all` escape hatch.
- **Regression test required:** yes — with `127.0.0.1:11434` allowed, assert `169.254.169.254` and `10.0.0.5` are rejected.
- **Dependencies / risks:** breaks deployments relying on the blanket flag.
- **Owner decision (2026-10-20, Q-5c):** the allowlist is **empty by default** (private hosts blocked for everyone) and `=all` remains the documented escape hatch for local Ollama / LM Studio. The blast radius note stands: role `admin` is granted automatically by any plan, so this is not an admin-only surface.
- **Status:** **VERIFIED_FIXED (2026-10-20, PH-02)** — `ALLOW_PRIVATE_MODEL_HOSTS` از پرچمِ boolean به فهرستِ `host:port` تبدیل شد (پیش‌فرض: خالی) و در **زمانِ درخواست** هم اعمال می‌شود؛ `all` تنها راه فرارِ صریح و مستند است.

## SEC-008 — Repeated channel-claim attempts rotate the owner's transfer token and spam the owner
- **Severity / Priority / Category:** LOW · P2 · DESIGN WEAKNESS
- **Evidence:** `newsbot/ui_views_admin.py:109-115` issues a token on every attempt; `newsbot/core_channels.py:29-31` deletes all unconsumed tokens for the channel and inserts a new one; the only throttle is the global 40-updates/10 s window.
- **Actual vs Expected:** Actual — a co-administrator can repeatedly invalidate the legitimate owner's pending token and spam the owner with DMs. Expected — reuse an unconsumed token and rate-limit the action per channel.
- **Impact:** denial of the ownership-transfer flow plus notification spam.
- **Fix:** reuse an unconsumed, unexpired token; add `rate_ok(f"chadd:{cid}", 60)`.
- **Regression test required:** yes — two consecutive claims return the same token (or the second is rate-limited) and produce exactly one DM.
- **Dependencies / risks:** reusing a token widens the brute-force window slightly; compensate with the existing `tok:{uid}` limit.
- **Status:** OPEN

## SEC-009 — Unvalidated `int()` on attacker-controlled callback segments
- **Severity / Priority / Category:** LOW · P3 · CONFIRMED BUG
- **Evidence:** `newsbot/ui_dispatch.py:63,64,65,69,70,192,193,209,215,218,224,225,232,234,278,297,304-306,333,351,381` — `int(c)` / `int(d)` on raw callback segments; the correct pattern already exists at `newsbot/ui_dispatch.py:90,157,159` (`notfound`).
- **Actual vs Expected:** Actual — `a:qe:1:x`, `u:plan:x`, `a:catv:1:y` raise `ValueError` inside `dispatch`; the generic handler catches it so the process survives, but every malformed callback writes a stack trace to the journal. Expected — a graceful `notfound`.
- **Impact:** log flooding and a generic failure message instead of a clear one.
- **Fix:** add a `_i(seg)` helper returning `None` on failure and use the existing `notfound` pattern.
- **Regression test required:** yes — dispatch the malformed callbacks; expect `notfound` and no traceback.
- **Dependencies / risks:** none.
- **Status:** OPEN

## SEC-010 — `docs/SECURITY.md` states that all user-input egress goes through `fetch` / `_GuardedStream`
- **Severity / Priority / Category:** MEDIUM · P2 · DOCUMENTATION MISMATCH
- **Evidence:** `docs/SECURITY.md:77` states that outbound network access from user input only passes through those two functions; `newsbot/ai_models.py:93-95` is a third, unguarded egress.
- **Actual vs Expected:** Actual — the documented three-layer defence model does not describe the shipped code; a reviewer relying on it would not look for SSRF in `ai_models`. Expected — the egress inventory matches the code.
- **Impact:** the security model is the artefact reviewers trust; an inaccurate one hides SEC-001/SEC-007.
- **Fix:** close SEC-001/SEC-007, then add the AI-provider path to `docs/SECURITY.md` sections 1 and 5.
- **Regression test required:** no (documentation); optionally a static test that every `http().post(`/`get(` call site is a Cloudflare endpoint or is wrapped by a validator.
- **Dependencies / risks:** none.
- **Status:** **VERIFIED_FIXED (2026-10-20, PH-02)** — سندِ امنیتی اکنون هر سه مسیرِ خروجیِ محافظت‌شده (خوراک، رسانه، پروایدر AI) و سازوکارِ حذفِ هدرِ اعتبار را توصیف می‌کند.

## SEC-011 — Super-admin Telegram IDs are written to the service journal in cleartext
- **Severity / Priority / Category:** INFO · P3 · DESIGN WEAKNESS
- **Evidence:** `newsbot/app.py:41` — `log.info(f"... super: {config.SUPER_ADMIN_IDS} ...")`; `newsbot/app.py:170` (`preflight`) logs only `len(config.SUPER_ADMIN_IDS)`.
- **Actual vs Expected:** Actual — the full set of privileged IDs is written to stdout/journald on every start; `redact` does not mask bare integers. Expected — the count only.
- **Impact:** identifies the operator's account to anyone who can read the journal.
- **Fix:** log the count.
- **Regression test required:** yes — assert the startup line contains no raw ID.
- **Dependencies / risks:** none.
- **Status:** OPEN

## SEC-012 — Dependencies are version-pinned but not hash-pinned; deploy installs them as root
- **Severity / Priority / Category:** LOW · P3 · IMPROVEMENT OPPORTUNITY
- **Evidence:** `requirements.txt` (32 exact pins, no hashes); `deploy/deploy.sh:78` — `.venv/bin/pip install --quiet -r requirements.txt` as root; `deploy/Dockerfile:9` likewise.
- **Actual vs Expected:** Actual — a re-pushed or hijacked wheel at the same version installs silently on the production host. The pinned versions themselves are recent (httpx 0.28.1, cryptography 50.0.2, urllib3 2.8.0, lxml 6.1.3, python-telegram-bot 22.8) and no obsolete version was identified by version reasoning. Expected — a hash-pinned lock.
- **Impact:** supply-chain blast radius equals the production host.
- **Fix:** `pip-compile --generate-hashes` + `pip install --require-hashes`; install as the service user.
- **Regression test required:** yes — a CI step asserting every requirement line has a `--hash=` entry.
- **Dependencies / risks:** the lock must be regenerated whenever `requirements.txt` changes.
- **Status:** OPEN

## SEC-013 — Deployment authenticates as `root` over SSH
- **Severity / Priority / Category:** LOW · P3 · DESIGN WEAKNESS
- **Evidence:** `.github/workflows/deploy-vps.yml:52,62` — `username: root`; `deploy/deploy.sh:92`.
- **Actual vs Expected:** Actual — compromise of the deploy key implies full host compromise. Positive controls: the host key is verified by fingerprint, all three actions are SHA-pinned, and the *runtime* unit is well hardened (`User=botapp`, `NoNewPrivileges`, `ProtectSystem=strict`).
- **Fix:** a non-root deploy account with passwordless `sudo` limited to `systemctl restart <service>`.
- **Regression test required:** no (infrastructure).
- **Dependencies / risks:** needs SSH/root access to provision the account.
- **Status:** OPEN

## SEC-014 — `s:txtr2` writes arbitrary `settings` keys without validating the key or language
- **Severity / Priority / Category:** INFO · P3 · DESIGN WEAKNESS
- **Evidence:** `newsbot/ui_dispatch.py:262-263` -> `newsbot/ui_i18n.py:34` — `gtext_set(key, lang, value)` writes `settings['text_<key>_<lang>']` for any input; the sibling action `s:txt` validates at `newsbot/ui_inputs.py:483`.
- **Actual vs Expected:** Actual — `s:txtr2:foo:bar` creates a junk settings row. Expected — the same validation as `s:txt`.
- **Impact:** junk rows (super-admin only).
- **Fix:** validate `c in TEXT_KEYS and d in config.LANGS` before writing.
- **Regression test required:** yes — dispatch `s:txtr2:foo:bar` -> `notfound`, no row created.
- **Dependencies / risks:** none.
- **Status:** OPEN

## SEC-015 — Super-admin can delete another admin's personal AI model through `a:myai_d2`, while the view/edit paths refuse the same actor
- **Severity / Priority / Category:** INFO · P3 · DESIGN WEAKNESS
- **Evidence:** `newsbot/ui_dispatch.py:96` allows super (`owner_id != uid and not is_super(uid)`); `newsbot/ui_views_admin.py:283` refuses super; `newsbot/ui_inputs.py:186` refuses super.
- **Actual vs Expected:** Actual — three different authorization rules for the same object. Not an escalation (super can delete any model via `s:model_d2`), but the deletion is irreversible and the behaviour is confusing.
- **Fix:** unify the rule or document the override in `docs/PERMISSIONS.md` section 1.
- **Regression test required:** yes — super dispatching `a:myai_d2:{other_model}` -> `notfound` (or the documented override).
- **Dependencies / risks:** changes super-admin capabilities — needs a product decision.
- **Status:** OPEN

## SEC-016 — `sources.api_url` is echoed to the source owner and to super admins, including embedded credentials
- **Severity / Priority / Category:** LOW · P3 · DESIGN WEAKNESS
- **Evidence:** `newsbot/ui_views_admin.py:161` renders `str(s["api_url"])[:60]`; the discovery code explicitly supports `{key}`-style placeholders and query keys (`newsbot/discover.py:274-278`).
- **Actual vs Expected:** Actual — a customer key embedded in the URL is disclosed to super admins and persists in the chat transcript. Expected — the displayed copy is scrubbed of credentials.
- **Fix:** strip `key`/`apikey`/`api_key`/`token`/`access_token` query parameters from the **display** copy only.
- **Regression test required:** yes — a source with `?key=SECRET` renders without `SECRET`.
- **Dependencies / risks:** the request must still use the real URL.
- **Status:** OPEN

---

# 3. DATABASE & DATA INTEGRITY (`DB-001 … DB-011`)

## DB-001 — `_d1_batch()` still uses the broken separate-request `last_insert_rowid()` fallback that the single-statement path removed
- **Severity / Priority / Category:** HIGH · P1 · CONFIRMED BUG (D1 only)
- **Evidence:** `newsbot/db.py:201-204` (the **fix**) — `lastrowid = meta.get("last_row_id") if is_insert else None`, with the comment that a separate `last_insert_rowid()` request over REST is meaningless because every request is a new session; but `newsbot/db.py:150-157` (**the batch path**) still issues `POST {"sql": "SELECT last_insert_rowid() AS lid"}` for any INSERT whose `meta.last_row_id` is absent.
- **Actual vs Expected:** Actual — the batch path returns a meaningless rowid, so any `_trx` insert on D1 that reads `lastrowid` gets garbage. Expected — the batch path uses `meta.last_row_id` exactly like the single-statement path.
- **Impact:** wrong object ids on the D1 backend; the two paths in the same file disagree about the same invariant, so the original defect survives in the transaction path.
- **Fix:** delete the fallback in `_d1_batch` and read `meta.last_row_id`; if it is genuinely absent, raise rather than fabricate a value.
- **Regression test required:** yes — a fake D1 client whose batch response omits `last_row_id` must raise, not return a bogus id.
- **Dependencies / risks:** D1 is officially unsupported; the fix is cheap and removes a false "already fixed" claim.
- **Status:** OPEN

## DB-002 — On D1 the schema migration is **not** atomic, contradicting the function's own docstring
- **Severity / Priority / Category:** HIGH · P1 · CONFIRMED BUG (D1 only)
- **Evidence:** `newsbot/db.py:314-315` — "on SQLite all steps run inside one transaction; any error => full rollback (no half-schema)"; `newsbot/db.py:322` — `atomic = config.DB_BACKEND != "d1"` => **no transaction on D1**; only `SCHEMA + DB_INDEXES` are batched (`newsbot/db.py:272-273`), while `CREATE TABLE meta` (`:326`), every `ALTER TABLE ... ADD COLUMN` migration (`:333-337`), the trigger drop/create (`:340-341`), the `pub_journal` migration (`:344-345`) and `_ensure_unique_indexes` (`:347`) are each a **separate auto-commit REST request**.
- **Actual vs Expected:** Actual — a failure halfway through the migrations leaves a half-migrated schema; `meta.schema_version` is only written at the very end, so the half-migrated state is not even recorded. Expected — one atomic batch, as the docstring claims.
- **Impact:** on D1 a failed upgrade can leave the schema inconsistent and the next start migrates from an unknown point.
- **Fix:** send the whole migration sequence as one D1 batch, or state honestly that D1 migrations are best-effort and make the startup re-runnable.
- **Regression test required:** yes — a fake D1 that fails on the 3rd migration must leave zero statements applied.
- **Dependencies / risks:** D1 is unsupported; the honest-documentation fix is nearly free.
- **Status:** OPEN

## DB-003 — The referral-credit trigger is dropped and recreated non-atomically on D1
- **Severity / Priority / Category:** MEDIUM · P2 · CONFIRMED BUG (D1 only)
- **Evidence:** `newsbot/db.py:340-341` — `conn.execute("DROP TRIGGER IF EXISTS trg_referral_credit_wallet")` then `conn.execute(REFERRAL_CREDIT_TRIGGER)`; on D1 each `execute` is a separate auto-commit request (`newsbot/db.py:188-207`).
- **Actual vs Expected:** Actual — between the two requests there is a window with **no** trigger, during which a referral credit would update `referrals.credit_amount` **without** charging the wallet or writing a receipt; if the CREATE fails, the trigger is gone permanently and the wallet silently stops being credited. Expected — one atomic replacement.
- **Impact:** silent loss of referral rewards on the D1 backend; a permanent, invisible failure mode.
- **Fix:** batch the DROP+CREATE as one D1 batch; on failure, re-create the trigger before exiting.
- **Regression test required:** yes.
- **Dependencies / risks:** D1 only.
- **Status:** OPEN

## DB-004 — On D1 the ISO canonicalisation is silently skipped **and marked as done**
- **Severity / Priority / Category:** MEDIUM · P2 · CONFIRMED BUG (D1 only)
- **Evidence:** `newsbot/db.py:290-311` — the per-table update uses `SELECT rowid AS _rid` and `UPDATE {tbl} SET {col}=? WHERE rowid=?`; on D1 the `rowid` form is not guaranteed; each failure is swallowed by `except sqlite3.Error: continue` (`:305-308`); then the flag is written unconditionally: `conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('iso_norm_v1',?)", (str(changed),))`.
- **Actual vs Expected:** Actual — where the update fails, the flag is still set to "0 changed", so the normalisation is **never retried** and legacy non-canonical timestamps remain, breaking every string comparison on a timestamp column (the DB-14 rationale: day buckets, expiry checks). Expected — the flag is written only when the work actually completed.
- **Impact:** silently wrong date/period bucketing on D1.
- **Fix:** set the flag only if every column was processed (track a `skipped` counter); use a backend-neutral key instead of `rowid`.
- **Regression test required:** yes — a fake D1 that rejects `rowid` must leave the flag unset.
- **Dependencies / risks:** D1 only; the same `rowid` pattern is used nowhere else.
- **Status:** OPEN

## DB-005 — The schema is defined in two places (`SCHEMA` DDL + `MIGRATIONS` ALTER list)
- **Severity / Priority / Category:** INFO · P3 · IMPROVEMENT OPPORTUNITY
- **Evidence:** `newsbot/db.py:25` — the `users` table DDL has **no** `credit` column (it is added by `MIGRATIONS`, `newsbot/db.py:102`); 30+ further columns are added the same way (`newsbot/db.py:96-110`).
- **Actual vs Expected:** Actual — a fresh database reaches the correct shape only because every `_ensure_schema` run also replays the migration list; the base DDL alone is not a valid schema. Expected — one source of truth for the shape of a new database.
- **Impact:** low at runtime (the migrations are idempotent and always replayed), but a reviewer reading `SCHEMA` alone sees an out-of-date schema.
- **Fix:** keep `SCHEMA` and `MIGRATIONS` in sync, or generate both from one table definition.
- **Regression test required:** no.
- **Dependencies / risks:** none.
- **Status:** OPEN

## DB-006 — `q()`'s busy-retry performs a **blocking** `time.sleep` on the calling thread
- **Severity / Priority / Category:** MEDIUM · P2 · CONFIRMED BUG
- **Evidence:** `newsbot/db.py:415-416` — `time_sleep = 0.02 * (2 ** attempt); import time as _t; _t.sleep(time_sleep)` inside the retry loop of a **synchronous** function; the comment even says "because `q()` also runs on the event loop".
- **Actual vs Expected:** Actual — up to 5 attempts (20/40/80/160 ms => <= 300 ms) of hard block on the event loop whenever a writer holds the lock. Expected — a blocking sleep only on a worker thread.
- **Impact:** the whole bot (Telegram updates, the scheduler tick, other channels) stalls for up to 300 ms; the stall is invisible in the logs.
- **Fix:** move the retry into `arun`-only paths, or replace the sleep with a non-blocking wait when the caller is on the event loop.
- **Regression test required:** yes — assert the loop thread is not blocked during a busy retry.
- **Dependencies / risks:** coupled to PERF-001 (move the remaining `q()` calls off the loop).
- **Status:** OPEN

## DB-007 — No foreign keys anywhere: referential integrity is entirely application-enforced
- **Severity / Priority / Category:** MEDIUM · P2 · DESIGN WEAKNESS
- **Evidence:** `newsbot/db.py:24-52` — no `REFERENCES` clause in any `CREATE TABLE`; `PRAGMA foreign_keys` is never enabled (`newsbot/db.py:245` sets only journal_mode/synchronous/busy_timeout/cache_size/temp_store); `articles.admin_id`, `articles.channel_id`, `sources.channel_id`, `pay_requests.user_id`, `referrals.referrer_id/referred_id`, `credit_log.user_id`, `usage.admin_id` are all unconstrained.
- **Actual vs Expected:** Actual — deleting a channel relies on manual cascading deletes (`newsbot/core_channels.py`), and any missed path silently leaves orphan rows. Expected — declared referential integrity, or an explicit, tested, documented cascade policy.
- **Impact:** orphan rows are possible and undetectable; a deleted user's wallet/ledger history can become unreachable, which is a financial-auditability problem as well.
- **Fix:** declare the relationships and enable `PRAGMA foreign_keys=ON`; or add a periodic orphan-detection query surfaced in `/health`.
- **Regression test required:** yes — an orphan-detection test plus a cascade test on channel deletion.
- **Dependencies / risks:** enabling FKs on a database that already contains orphans requires a cleanup migration first — data-touching, so it needs its own authorised phase.
- **Status:** OPEN

## DB-008 — Several tables grow without bound
- **Severity / Priority / Category:** LOW · P3 · DESIGN WEAKNESS
- **Evidence:** `newsbot/core_articles.py:282-291` — the hourly cleanup deletes from `ai_cache`, `dedup`, `kv_cache`, `usage`, `pay_requests` (90 d) and `posted` (90 d), but **not** from `settings` (which holds the referral idempotency markers `refpaid:pay:<rid>` — one row per approved payment, see FIN-009), nor `ai_usage`, `plan_changes`, `credit_log`, nor `ch_tokens` (which has `expires_at` but is never purged).
- **Actual vs Expected:** Actual — the settings table grows by one row per approved payment, forever. Expected — TTL-driven cleanup for every growing table.
- **Impact:** slow, unbounded DB growth on a long-lived installation; index pressure on `settings`.
- **Fix:** purge the referral markers (they are only needed inside the payment idempotency window) and `ch_tokens` in the existing cleanup.
- **Regression test required:** yes — assert the row counts do not grow across repeated cleanup passes.
- **Dependencies / risks:** purging markers too early re-opens the FIN-009 replay window.
- **Status:** OPEN

## DB-009 — `journal_mode=WAL` is set on every new connection and its failure is silently ignored
- **Severity / Priority / Category:** LOW · P3 · DESIGN WEAKNESS
- **Evidence:** `newsbot/db.py:245-247` — `for pr in ("PRAGMA journal_mode=WAL", ...): try: conn.execute(pr) except sqlite3.OperationalError: pass`.
- **Actual vs Expected:** Actual — `journal_mode` is a persistent database property; setting it requires a write lock, so concurrent connections at startup can hit "database is locked" and the PRAGMA is **silently skipped**; a database left in `delete` journal mode then runs without concurrent readers, and the WAL-safe backup assumption (which the deploy script depends on) is wrong. Expected — the mode is asserted at startup, not merely attempted.
- **Impact:** the database can silently run in a different journal mode than the tooling assumes.
- **Fix:** set the mode once at startup and **verify** it (`PRAGMA journal_mode` -> assert `wal`); log a loud warning on failure instead of `pass`.
- **Regression test required:** yes — open the DB with a competing lock and assert the mode is reported.
- **Dependencies / risks:** a mode change on an existing database requires an exclusive lock.
- **Status:** OPEN

## DB-010 — `synchronous=NORMAL` trades durability for speed with no operator visibility
- **Severity / Priority / Category:** INFO · P3 · DESIGN WEAKNESS
- **Evidence:** `newsbot/db.py:245` — `PRAGMA synchronous=NORMAL` (in WAL mode this can lose the most recent transactions on a power failure, though it does not corrupt the database).
- **Actual vs Expected:** Actual — a host power loss can lose the last few committed transactions, which for this application could include a payment approval. Expected — the trade-off is explicit and documented, or `FULL` is used for the money path.
- **Impact:** low-probability loss of recent financial records.
- **Fix:** document the trade-off; consider `FULL` during the payment window or an explicit knob.
- **Regression test required:** no.
- **Dependencies / risks:** `FULL` costs write throughput.
- **Status:** OPEN

## DB-011 — The single-instance lock file is created next to the database and is never cleaned up
- **Severity / Priority / Category:** INFO · P3 · DESIGN WEAKNESS
- **Evidence:** `newsbot/app.py:180` — `f = open(config.DB_FILE + ".lock", "a+")`; the file is never removed; `.gitignore` excludes `*.lock`.
- **Actual vs Expected:** Actual — a stale `.lock` file persists after a crash; it is harmless because the lock is an advisory `flock`/`msvcrt.locking` on an open handle, not the file's existence. Expected — the file is documented as an artefact.
- **Impact:** cosmetic; a reader may mistake the file's presence for "a bot is running".
- **Fix:** document it, or use the DB's own advisory-lock facility.
- **Regression test required:** no.
- **Dependencies / risks:** none.
- **Status:** OPEN

---

# 4. TELEGRAM UI/UX & USER JOURNEYS (`UI-001 … UI-026`)

**Route map completeness (AST diff of all 216 emitted `callback_data` strings vs. every handled route): zero unmatched emitters — there are NO dead buttons. Authorization is layered and present on every route: `a:*` -> `can_admin` (`newsbot/ui_dispatch.py:83`) + object-level ownership; `s:*` -> `is_super` (`:245`); `u:*` -> own data only. No cross-tenant IDOR was found.**

## UI-001 — Six i18n keys are missing from the dictionary; raw keys are rendered to the super admin
- **Severity / Priority / Category:** MEDIUM · P1 · CONFIRMED BUG
- **Evidence:** `newsbot/ui_i18n.py:273-277` — `tr()` returns the key itself when unknown; the missing keys are `s_users_w`, `s_admins_w` (`newsbot/ui_views_super.py:195`), `s_prev`, `s_next` (`:197-198`), `s_model_add`, `s_models_test` (`:217`).
- **Actual vs Expected:** Actual — the super panel shows `s_users_w` / `s_admins_w` as text, and the pagination and model-test buttons are literally labelled `s_prev`, `s_next`, `s_model_add`, `s_models_test` — unusable, untranslated identifiers. Expected — proper fa/en labels.
- **Impact:** the super panel exposes internal identifiers; two buttons have no icon and no meaning.
- **Fix:** add the six keys to `TXT` (fa+en) and add a test asserting every `tr(lang,"X")` literal exists.
- **Regression test required:** yes.
- **Dependencies / risks:** none.
- **Status:** OPEN

## UI-002 — Unfilled `{placeholder}` tokens are shown verbatim to end users
- **Severity / Priority / Category:** MEDIUM · P1 · CONFIRMED BUG
- **Evidence:** `newsbot/ui_i18n.py:127` — the `review` template needs `{n}` but `newsbot/ui_views_admin.py:80` and `:214` do not supply it; `newsbot/ui_i18n.py:175` (`src_api_url`) shows `<code>{key}</code>` as a literal; `newsbot/ui_i18n.py:257` (`s_texts_title`) shows `<code>{name}</code> <code>{plan}</code> <code>{price}</code>`.
- **Actual vs Expected:** Actual — the channel-panel status line reads `... {n} ...`; the schedule **button label** contains the same literal; the source-API prompt says "put `{key}` in the URL" (misleading — it means the word "key"). Expected — placeholders are resolved or are literal text.
- **Impact:** broken-looking Persian UI on the **main channel panel** and a genuinely misleading instruction in the source-API setup prompt.
- **Fix:** pass `n=` at both call sites; make the `{key}` and `{name}/{plan}/{price}` markers literal.
- **Regression test required:** yes — assert no rendered user string matches `\{[a-z_]+\}`.
- **Dependencies / risks:** none.
- **Status:** OPEN

## UI-003 — `noop` buttons give zero feedback
- **Severity / Priority / Category:** MEDIUM · P2 · DESIGN WEAKNESS
- **Evidence:** `newsbot/ui_dispatch.py:53,452` — `if data == "noop": return`; `newsbot/ui_views_user.py:144,146` — the "under review" and "not available" buttons use `noop`.
- **Actual vs Expected:** Actual — tapping them stops the spinner and does nothing. Expected — a toast explaining the state and the next step.
- **Impact:** a dead end for the customer at the two most sensitive moments (waiting for a payment decision; being told a plan is unavailable).
- **Fix:** replace `noop` with `u:pending:<pid>` / `u:locked:<pid>` routes that answer with a toast.
- **Regression test required:** yes.
- **Dependencies / risks:** none.
- **Status:** OPEN

## UI-004 — The callback is answered only after the work finishes; the button spinner can last minutes
- **Severity / Priority / Category:** MEDIUM · P1 · CONFIRMED BUG (UX)
- **Evidence:** the only eager `answer()` calls are `newsbot/ui_dispatch.py:431,435,440`, `newsbot/ui_views_admin.py:308` (`a:test` only) and `newsbot/ui_widgets.py:563` (the `popup` helper); the generic `await qy.answer()` is at `newsbot/ui_dispatch.py:466`, i.e. **after** the dispatch. Never-answered-early routes: `s:bc_go` (`:265-276`), `s:models_test` (`:362-369`), `a:srcr` (`:116-122`), `a:pub` (`:144-155`), `s:model_test` (`:351-353`), `a:myai_t` (`:102`), `a:artf` (`:138-143`).
- **Actual vs Expected:** Actual — the Telegram client shows a spinner/progress bar for the whole duration (minutes for `s:bc_go`). Expected — an immediate "working..." answer before the long work.
- **Impact:** on mobile the button looks frozen; users tap repeatedly, each tap being a fresh callback that duplicates the work.
- **Fix:** answer early for known-slow routes (a `slow` marker on the route table).
- **Regression test required:** yes — assert `query.answer` precedes the work coroutine for slow routes.
- **Dependencies / risks:** none.
- **Status:** OPEN

## UI-005 — `split_html()` can emit an empty chunk and a chunk longer than Telegram's 4096 limit
- **Severity / Priority / Category:** HIGH · P1 · CONFIRMED BUG
- **Evidence:** `newsbot/text_format.py:264` — `if len(cur) + len(para) + 1 > limit: chunks.append(sanitize_html(cur)); cur = para` — the chunk is appended **before** the length check for the new paragraph, so an over-long single paragraph is emitted intact and the previous chunk may be empty. Reproduced: `"<b>x</b> " + "A"*5000` -> chunk lengths `[0, 5009]`.
- **Consumers with no recovery:** `newsbot/ui_dispatch.py:142` (the "full text" view) and `newsbot/ui_commands.py:60-62` (deeplink payloads, whose `downgrade_html` fallback does not shorten).
- **Actual vs Expected:** Actual — an empty message and an over-limit message => `TelegramBadRequest: message is too long` / `MESSAGE_TEXT_EMPTY`. Expected — every chunk <= 4096 and never empty.
- **Impact:** the "full version" view and `/start r_<key>` deeplinks can fail entirely; the user sees only a generic failure message.
- **Fix:** hard-split over-long paragraphs with the existing `fit_html`/`hard_html` helpers and skip empty chunks.
- **Regression test required:** yes — property test over lengths 0..20 000 and single/multi-paragraph inputs.
- **Dependencies / risks:** none.
- **Status:** OPEN

## UI-006 — A payment receipt can be silently lost before it reaches any administrator
- **Severity / Priority / Category:** HIGH · P0 · CONFIRMED BUG
- **Evidence:** `newsbot/ui_inputs.py:459-462` — `await context.bot.copy_message(sid, msg.chat_id, msg.message_id, caption=header[:1000], parse_mode="HTML", ...)` inside `try/except Exception as e: log.warning(f"pay notify {sid}: {e}")`, followed by `return await view_receipt_ok(update, context)`. `header` = `tr(sl,"s_pay_new", ...)` (`newsbot/ui_i18n.py:255`) containing the customer's note (up to ~400 escaped chars) + plan + price + discount — routinely 700-950 chars and easily over 1000.
- **Actual vs Expected:** Actual — the truncation at 1000 chars cuts inside a tag (`<b>`, `<code>`) => Telegram rejects the caption => the exception is swallowed into a log line => **no administrator ever receives the receipt**, while the customer is told "receipt received". Expected — the receipt reaches an administrator, or the customer is told it failed.
- **Impact:** direct revenue loss **and** a false confirmation to the paying customer.
- **Fix:** truncate the *fields* (cap the note at ~300 chars) and build the caption with `fit_html`; or send the receipt as a photo with a short caption plus a second message; on failure, notify the user and mark the request as un-notified.
- **Regression test required:** yes — a 400-char note must yield valid HTML <= 1024, and the failure path must notify the user.
- **Dependencies / risks:** coupled with OBS-013 (no re-notification for pending payments).
- **Status:** OPEN

## UI-007 — Unbounded inline keyboards (source list, category list) exceed Telegram's limits
- **Severity / Priority / Category:** MEDIUM · P2 · DESIGN WEAKNESS (latent CONFIRMED BUG)
- **Evidence:** `newsbot/ui_views_admin.py:153` (all sources) and `:184` (all categories) are uncapped, while `view_queue` caps at 15 (`:233`), `view_s_users` at 10 (`:193`) and `view_s_pays` at 10 (`:234`); a super admin has `max_sources = None` (`newsbot/core_users.py:72`).
- **Actual vs Expected:** Actual — with > 100 sources the reply markup exceeds Telegram's limit => the whole message is rejected; `render()`'s fallback re-sends the same invalid markup and the exception escapes (`newsbot/ui_widgets.py:586-590`). Expected — paginated lists, as everywhere else.
- **Impact:** the source-management screen breaks for heavy users — the core admin journey.
- **Fix:** paginate `view_sources` / `view_cats` (15 per page), mirroring `view_s_users`.
- **Regression test required:** yes.
- **Dependencies / risks:** none.
- **Status:** OPEN

## UI-008 — Plan names (up to 100 chars) become button labels, and Telegram's 64-character button limit kills the whole screen
- **Severity / Priority / Category:** HIGH · P1 · CONFIRMED BUG
- **Evidence:** `newsbot/ui_inputs.py:108` — the name limit is 100; `newsbot/ui_views_user.py:113` — `B(f"... {plan_txt(p,'name',lang)}", f"u:plan:{p['id']}")`; `newsbot/ui_dispatch.py:308` — the same for the super-admin plan picker. `B()` truncates only `callback_data`, never the label (`newsbot/ui_widgets.py:46`).
- **Actual vs Expected:** Actual — a 100-character Persian plan name yields a 102-character label => Telegram rejects the entire message with `BUTTON_TEXT_INVALID`; `render()`'s fallback (`newsbot/ui_widgets.py:586-590`) re-sends the same invalid markup => the exception escapes => the user sees a generic failure and **no plans screen at all**. Expected — the label is truncated (the codebase already truncates channel titles to 22 and article titles to 35).
- **Impact:** one admin typing a long plan name breaks the purchase screen for **every customer**.
- **Fix:** truncate the label to ~24 characters and/or clamp `name_limit` to ~40; add a guard in `B()`.
- **Regression test required:** yes — a plan with a 100-char name must still render.
- **Dependencies / risks:** none.
- **Status:** OPEN

## UI-009 — The payment-receipt flow is silently aborted by any other button press
- **Severity / Priority / Category:** HIGH · P1 · CONFIRMED BUG (money path)
- **Evidence:** `newsbot/ui_views_user.py:200-201` — the receipt state lives only in `context.user_data["await"]`; `newsbot/opctx.py:48-51` (`clear_interaction`) pops `"await"` for every action not in the `keep` list; `newsbot/ui_dispatch.py:450,458` — `keep` contains only `("disc",)` for `u:plan/req/disc/discx`.
- **Actual vs Expected:** Actual — after "buy", tapping any other button wipes the state **with no message**; the customer then sends the receipt photo, `on_message` finds no `await` and falls through to `go_home` (`newsbot/ui_support.py:79-80`) => the photo is ignored and the bot answers with the home panel. Expected — either the form is preserved, or the customer is told the receipt was not registered.
- **Impact:** silent loss of payment receipts — the same outcome as UI-006 from a different trigger.
- **Fix:** keep `"await"` for the `receipt` kind across navigation and re-assert the payment prompt instead of the home panel when a receipt-kind state was dropped.
- **Regression test required:** yes.
- **Dependencies / risks:** none.
- **Status:** OPEN

## UI-010 — In-progress input forms are discarded silently when the user taps another button
- **Severity / Priority / Category:** MEDIUM · P2 · DESIGN WEAKNESS
- **Evidence:** `newsbot/ui_dispatch.py:450,458` (`clear_interaction` pops `"await"` and `"qs"` for every non-kept action); `newsbot/ui_commands.py:47` (`_prep`) pops it for every command.
- **Actual vs Expected:** Actual — typing a source URL, then tapping a stale button, drops the form without notice; for `a:qe` the lost `qs` value silently becomes `quiet_start = 00:00`. Expected — a warning, or preservation.
- **Impact:** repeated data entry and a silent quiet-hours mis-configuration.
- **Fix:** keep `"await"`/`"qs"` across navigation, or warn before discarding.
- **Regression test required:** yes.
- **Dependencies / risks:** none.
- **Status:** OPEN

## UI-011 — Destructive and high-impact administrator actions are not audited
- **Severity / Priority / Category:** HIGH · P1 · DESIGN WEAKNESS / CONFIRMED BUG
- **Evidence:** only two destructive actions write an audit event — `newsbot/ui_dispatch.py:169` (queue reset) and `newsbot/ui_views_super.py:292` (payment decision). There is **no** `log_event` at `newsbot/ui_dispatch.py:100` (delete personal model), `:124` (delete source), `:172` (**delete channel**), `:219` (delete category), `:234` (delete criterion), `:293` (delete plan), `:303` (delete discount), `:314` (revoke plan), `:319` (**ban/unban user**), `:323-325` (reset counters), `:349` (delete global model), `:309-312` (**assign plan = grant value**), `:249` (global automation off), `:285` (make a plan free).
- **Actual vs Expected:** Actual — an operator cannot answer "who deleted the channel / who banned the user / who granted a plan last Tuesday". Expected — every destructive branch writes `log_event(..., uid)` with the object id (the pattern already exists in the codebase).
- **Impact:** no forensic trail for account-level abuse — the most sensitive super-admin powers.
- **Fix:** add `log_event` to each destructive branch.
- **Regression test required:** yes — assert `log_event` is called for each `*2` execute branch.
- **Dependencies / risks:** none.
- **Status:** OPEN

## UI-012 — One-click, unconfirmed, high-impact super-admin actions
- **Severity / Priority / Category:** MEDIUM · P2 · DESIGN WEAKNESS
- **Evidence:** `newsbot/ui_dispatch.py:249` — `s:auto` flips the **global** automation switch in one click; `:285` — `s:plan_free` silently zeroes a live plan's price; `:309-312` — `s:uassign` grants a paid plan for free. None has a confirmation screen; none is audited (UI-011).
- **Actual vs Expected:** Actual — a single mis-tap (or a compromised super session) halts revenue generation for **every** customer or destroys pricing. Expected — the two-phase confirmation used for delete/ban (`newsbot/ui_dispatch.py:387-389`).
- **Fix:** route the three through `_confirm()` with `...2` variants, exactly like `a:chdel2`.
- **Regression test required:** yes.
- **Dependencies / risks:** none.
- **Status:** OPEN

## UI-014 — The customer home screen has one button, and the referral route is unreachable from the UI
- **Severity / Priority / Category:** MEDIUM · P2 · CONFIRMED BUG (dead route) + DESIGN WEAKNESS
- **Evidence:** `newsbot/ui_views_user.py:37` — `kb = [[B(tr(lang,"plans_btn"), "u:plans")]]`; `newsbot/ui_dispatch.py:66` handles `u:ref`, but the literal `"u:ref"` appears **nowhere** in `newsbot/` (0 hits) — the referral page is reachable only via the `/ref` **command**.
- **Actual vs Expected:** Actual — a new user sees one button; support, invite, help, about and language are reachable only if the user already knows the command names. Expected — a home screen with Support / Invite / Help / Language, and `u:ref` wired to one of them (`ref_btn` already exists at `newsbot/ui_i18n.py:71`).
- **Impact:** non-technical customers cannot discover support or the referral flow — directly relevant to the target audience.
- **Fix:** add the buttons to `view_user_home` and emit `u:ref`.
- **Regression test required:** yes — assert every screen the router can render is reachable from home.
- **Dependencies / risks:** none.
- **Status:** OPEN

## UI-016 — Raw English error codes are shown to Persian users
- **Severity / Priority / Category:** LOW · P2 · DESIGN WEAKNESS
- **Evidence:** `newsbot/cycle.py` `_pub_err(code, lang)` maps only `bot_not_admin` and `duplicate`; `newsbot/ui_dispatch.py:155` — `tr(lang,"pub_failed", e=_pub_err(out, lang))`; `newsbot/publish.py` returns `"not_ready"` / `"no_channel"`.
- **Actual vs Expected:** Actual — the user sees "publish: not_ready" / "publish: no_channel". Expected — a translated message with the next action.
- **Fix:** extend `_pub_err` with the remaining codes.
- **Regression test required:** yes.
- **Dependencies / risks:** none.
- **Status:** OPEN

## UI-017 — The plan price is free text; the seeded paid plan literally says "Contact us"
- **Severity / Priority / Category:** MEDIUM · P2 · DESIGN WEAKNESS
- **Evidence:** `newsbot/core_plans.py` `_BASE_PLANS` — `price="توافقی"`, `price_en="Contact us"`, `price_num`/`price_num_fa` = 0; `newsbot/ui_views_user.py:198-199` — `final_amount = new_price`; `newsbot/ui_i18n.py:18` — "press /man for payment details".
- **Actual vs Expected:** Actual — out of the box the customer sees a non-numeric amount and is told to press `/man`; no currency unit is enforced anywhere. Expected — the purchase screen always shows a unit and the exact amount to transfer.
- **Impact:** the customer cannot know what to pay; the receipt form then captures a screenshot of an unknown figure.
- **Fix:** require a numeric tariff per language (or derive the display from `fmt_amt`) and validate the price string for a unit on save.
- **Regression test required:** yes.
- **Dependencies / risks:** changing the seeded plan changes the public offer — needs an owner decision.
- **Owner decision (2026-10-20, Q-4c):** the free text and the negotiable model are **kept**; a plan with no numeric tariff for the user's language becomes **not online-purchasable** — the plan page states that the price is negotiable and points to support, and the receipt prompt can never appear without a numeric amount. The seeded offer itself is **not** changed.
- **Status:** **VERIFIED_FIXED (2026-10-20, PH-01)** — پلنِ بدونِ تعرفهٔ عددی آنلاین خریداری نمی‌شود: پیامِ «قیمت توافقی» + دکمهٔ خرید حذف می‌شود (Q-4c).

## UI-024 — `/lang` destroys an in-progress form and offers no way out of the language picker
- **Severity / Priority / Category:** LOW · P2 · DESIGN WEAKNESS
- **Evidence:** `newsbot/ui_commands.py:47` — `context.user_data.pop("await", None)`; `newsbot/ui_views_user.py:22` — the picker has only the two language buttons (no Back).
- **Actual vs Expected:** Actual — (a) `/lang` mid-form silently cancels it; (b) a user who only wanted to look is forced to choose a language to get out; (c) `set_language` -> `relocalize_all` rewrites every channel prompt in the new language (`newsbot/ui_views_user.py:27`) — an undisclosed side effect.
- **Fix:** add a Back button, keep the `await` state, and state the relocalisation effect.
- **Regression test required:** no.
- **Dependencies / risks:** none.
- **Status:** OPEN

### UI-013, UI-015, UI-018 … UI-023, UI-025, UI-026 — remaining UI findings

| ID | Title | Sev/Pri | Category | Evidence | Actual vs Expected | Impact | Fix | Test | Status |
|---|---|---|---|---|---|---|---|---|---|
| UI-013 | `docs/PERMISSIONS.md` section 8 misstates the two-phase list (omits `a:chdel2`, `a:qreset2`; claims "all" while `s:uassign`/`s:plan_free`/`s:auto` are one-phase) | LOW · P3 | DOC MISMATCH | `docs/PERMISSIONS.md:105` vs `newsbot/ui_dispatch.py:165,171,309,285,249` | The documented invariant does not match the code | Reviewers get a false picture of the safety model | Update the section to the real list | no | OPEN |
| UI-015 | Six i18n keys are dead (`ref_btn`, `ref_title`, `ref_how`, `ref_line`, `ref_cap_line`, `ref_link`) | LOW · P3 | IMPROVEMENT OPPORTUNITY | `newsbot/ui_i18n.py:71-76`; the referral copy is hard-coded in `newsbot/ui_views_user.py:46-78` | The admin-editable referral text cannot be edited and the copy bypasses i18n | The referral page cannot be localised by an admin | Delete the orphan keys or move `_referral_message` into `TXT` | no | OPEN |
| UI-018 | No bidi isolation anywhere in user-facing Persian text (exactly one LRM character in the whole repo, and it is in a log line) | LOW · P3 | DESIGN WEAKNESS | `newsbot/app.py:145`; mixed strings at `newsbot/ui_i18n.py:50,100,128` | Latin/digit runs inside RTL paragraphs are not isolated -> visual reordering of amounts and units | Cosmetic but pervasive mis-ordering of prices/units | Add an `ltr()` helper applied to price/unit fields | no | OPEN |
| UI-019 | The English plan view shows the **Persian** description and never shows `desc_en` | LOW · P3 | CONFIRMED BUG (i18n) | `newsbot/ui_i18n.py:237` — the `fa` tuple contains `{desc_en}`, the `en` tuple does not (the only placeholder mismatch in the 446-key dictionary) | An English super admin sees the Persian description under the label "Description" | Wrong-language content in the super panel | Add `{desc_en}` to the `en` tuple | yes — assert fa/en placeholder parity for every key | OPEN |
| UI-020 | Typo in the `/help` copy ("مدیریق" instead of "مدیریت") | LOW · P3 | CONFIRMED BUG (copy) | `newsbot/ui_i18n.py:14` | Misspelling in the only in-app help text | Credibility | Correct the string | no | OPEN |
| UI-021 | The super-admin user list "back" button always jumps to page 1 | LOW · P3 | IMPROVEMENT OPPORTUNITY | `newsbot/ui_views_super.py:209` — `B(tr(lang,"back"), "s:users:0")` | The admin loses their place in a 10-row-per-page list | Navigation friction | Pass the page through the callback | no | OPEN |
| UI-022 | Silent no-op routes give the user no feedback | LOW · P3 | DESIGN WEAKNESS | `newsbot/ui_dispatch.py:255-260` and `:245` — bare `return` | A tap produces no toast, no re-render, no error; the screen looks frozen | Confusing failure mode | Replace the bare returns with the `notfound` toast | yes | OPEN |
| UI-023 | Missing empty states (discount list, admin list) | LOW · P3 | IMPROVEMENT OPPORTUNITY | `newsbot/ui_views_super.py:179-183` | "No codes" is indistinguishable from a failed query | Minor confusion | Add `s_disc_none` | no | OPEN |
| UI-025 | Long admin keyboards (9-10 rows) and a 51-character button label | LOW · P3 | DESIGN WEAKNESS | `newsbot/ui_views_super.py:173,244-247`; `newsbot/ui_views_admin.py:214` | Hard to scan on mobile; the schedule-mode button is a sentence | UX | Shorten labels to icon + short word; paginate the editors | no | OPEN |
| UI-026 | Toasts longer than 35 characters become extra chat messages instead of popups | LOW · P3 | DESIGN WEAKNESS | `newsbot/ui_widgets.py:553-566` (`POPUP_MAX_CHARS = 35`) | ~40 admin toasts arrive as auto-deleted bubbles that fill the chat | Chat clutter | Raise the threshold (Telegram allows 200) or shorten the strings | no | OPEN |

---

# 5. CONTENT PIPELINE & AI GENERATION (`PIPE-001 … PIPE-022`)

## PIPE-001 — The per-article AI call budget is silently reset mid-article
- **Severity / Priority / Category:** HIGH · P1 · CONFIRMED BUG
- **Evidence:** `newsbot/ai_models.py:321-329` — `if (time.monotonic() - b["t0"]) > config.AI_ARTICLE_TIME_BUDGET * 2: budget_reset(); b = _budget()`, and `budget_reset()` (`:311-313`) resets **both** the call counter and the clock.
- **Actual vs Expected:** Actual — after 2 x `AI_ARTICLE_TIME_BUDGET` (480 s) the "per-article" cap restarts at 0, so an article can issue 12 provider calls per 480 s window, unbounded in total. Expected — a hard per-article cap.
- **Impact:** the cost control is a **rate**, not a cap; a slow provider (30 s/call) turns one article into 12 chunk calls, then a fresh window, indefinitely.
- **Fix:** reset only when the call is outside an article context (explicit flag or a separate ContextVar); never reset `calls` mid-article.
- **Regression test required:** yes — 13 chunk calls with a mocked clock; the 13th must return `ai_budget` and `b["calls"]` must never reset.
- **Dependencies / risks:** low.
- **Status:** **VERIFIED_FIXED (2026-10-20, PH-08)** — بودجهٔ مقاله «سخت» شد (`budget_reset(hard=True)`) و ریستِ زمانیِ میانهٔ مقاله حذف شد ⇒ سقفِ «N فراخوانی برای هر مقاله» واقعاً سخت است.

## PIPE-002 — Articles longer than ~88 000 characters are structurally unprocessable
- **Severity / Priority / Category:** MEDIUM · P2 · CONFIRMED BUG (config interaction)
- **Evidence:** `newsbot/generate.py:90-92` — `chunks = [full[i:i+config.AI_INPUT_TEXT_CHARS] ...]`; `newsbot/config.py:113,121,123` — `MAX_ARTICLE_CHARS=120000`, `AI_INPUT_TEXT_CHARS=8000`, `AI_MAX_CALLS_PER_ARTICLE=12`.
- **Actual vs Expected:** Actual — chunks = ceil(len/8000) plus one final call; any article over 88 000 chars (27 % of the configured maximum) always exhausts the budget and fails with `ai:ai_budget`, indistinguishable from a quota problem. Expected — a defined outcome for over-long articles.
- **Impact:** long-form sources are permanently rejected.
- **Fix:** derive the effective limit from `AI_MAX_CALLS_PER_ARTICLE-1` and truncate at extraction time, or emit a distinct `ai_too_long` code.
- **Regression test required:** yes — a 100 000-char article must produce a defined outcome.
- **Dependencies / risks:** changing `MAX_ARTICLE_CHARS` alters prompt cost.
- **Status:** **VERIFIED_FIXED (2026-10-20, PH-08)** — متنِ مقاله پیش از تولید به اندازه‌ای که در بودجهٔ فراخوانی جا می‌شود کوتاه می‌شود ⇒ نتیجهٔ مشخص به‌جای شکستِ خاموشِ «ai_budget».

## PIPE-003 — `AI_CACHE_TTL_DAYS` is not enforced on the read path
- **Severity / Priority / Category:** MEDIUM · P2 · CONFIRMED BUG
- **Evidence:** `newsbot/generate.py:135-143` — `SELECT value FROM ai_cache WHERE key=?` with **no** `created_at` predicate; the TTL exists only in the hourly sweeper (`newsbot/core_articles.py:282-283`), which is throttled and fails silently.
- **Actual vs Expected:** Actual — an expired entry is still served whenever the sweeper has not run; the TTL knob is not a real control. Expected — TTL honoured at read time.
- **Impact:** stale summaries published beyond the documented 14-day TTL.
- **Fix:** add `AND created_at >= ?` to both cache reads.
- **Regression test required:** yes — an old row must be a cache miss.
- **Dependencies / risks:** none.
- **Status:** **VERIFIED_FIXED (2026-10-20, PH-08)** — TTL در **زمانِ خواندن** هم اعمال می‌شود (`created_at >= cutoff`) — دیگر به پاک‌سازیِ ساعتی وابسته نیست.

## PIPE-004 — The AI cache key omits the article content
- **Severity / Priority / Category:** MEDIUM · P2 · DESIGN WEAKNESS
- **Evidence:** `newsbot/generate.py:124-129` — the key is `sha1(url_hash | title | settings_fingerprint)`; the article **text** is not part of it, yet it determines `want_full` (`:152`) and the whole output.
- **Actual vs Expected:** Actual — a news item corrected in place keeps serving the old summary for up to 14 days. Expected — the cache is content-addressed (as the chunk cache already is, `newsbot/generate.py:81-83`).
- **Impact:** incorrect content published; `want_full` can flip while the cached `full` stays null.
- **Fix:** add a cheap content fingerprint to the key.
- **Regression test required:** yes — same URL/title, changed text => different key.
- **Dependencies / risks:** invalidates the existing cache (one-off cost).
- **Status:** **VERIFIED_FIXED (2026-10-20, PH-08)** — اثرِ انگشتِ محتوا به کلیدِ کش اضافه شد ⇒ مقاله‌ای که درجا اصلاح شده دیگر خلاصهٔ کهنه سرو نمی‌کند.

## PIPE-006 — The daily AI cap counts articles, not provider calls
- **Severity / Priority / Category:** MEDIUM · P2 · CONFIRMED BUG
- **Evidence:** `newsbot/generate.py:144-147` — `ai_claim(owner_id)` is called **once per `generate()`**, while `newsbot/generate.py:110-117` may issue up to 11 further provider calls for the same article.
- **Actual vs Expected:** Actual — `AI_DAILY_FREE=20` means 20 **articles**, i.e. up to 240 real provider calls per day for a free account. Expected — the cap matches the advertised unit.
- **Impact:** the advertised quota understates real spend by up to 12x.
- **Fix:** claim per provider call (inside `ai_chat`), or claim `1 + chunk_count`.
- **Regression test required:** yes — a long article must consume more than one unit.
- **Dependencies / risks:** changes the meaning of `usage.ai`; needs a semantics migration.
- **Status:** **VERIFIED_FIXED (2026-10-20, PH-08)** — سهمِ روزانهٔ AI به ازای **هر فراخوانیِ واقعیِ مدل** گرفته می‌شود (داخلِ `ai_chat`) ⇒ هزینه واقعی کران‌دار است. برای مقالهٔ معمولی (یک فراخوانی) رفتارِ مشتری تغییری نمی‌کند.

## PIPE-007 — The personal-model test path bypasses the daily AI cap entirely
- **Severity / Priority / Category:** MEDIUM · P2 · CONFIRMED BUG
- **Evidence:** `newsbot/ui_widgets.py:530-537` -> `newsbot/ai_models.py:321` — no `ai_claim` anywhere on the personal-test path; the only throttle is `rate_ok(f"mtest:{uid}", TEST_COOLDOWN_SEC=120)`.
- **Actual vs Expected:** Actual — any channel admin can make ~720 billed provider calls per day without touching `usage.ai`. Expected — the same quota as the channel-test path.
- **Impact:** unbounded third-party spend attributable to one tenant.
- **Fix:** charge the quota in `ai_chat` for owner-scoped calls not already claimed by `generate()`.
- **Regression test required:** yes.
- **Dependencies / risks:** users may find the quota consumed by their own tests.
- **Status:** **VERIFIED_FIXED (2026-10-20, PH-08)** — تستِ مدلِ شخصی هم از همان دروازه‌ی سهمِ روزانه رد می‌شود (`owner_id` دارد) ⇒ دیگر هزینهٔ AI بدونِ سقف ندارد.

## PIPE-011 — There is no per-article extraction deadline
- **Severity / Priority / Category:** MEDIUM · P2 · DESIGN WEAKNESS
- **Evidence:** `newsbot/extract.py:181-193` — the multi-page loop follows up to `MAX_ARTICLE_PAGES` (5) hops, each up to `FETCH_TOTAL_SEC` (60 s) => up to 5 minutes for one article, with no aggregate cap; the cycle has no overall deadline either.
- **Actual vs Expected:** Actual — a single hostile/slow site can hold a cycle and a `CYCLE_SEM` slot (`newsbot/concurrency.py:11`) for minutes. Expected — an overall extract deadline.
- **Impact:** cycle latency, `cycle_ms` inflation, `TICK_SECONDS` drift.
- **Fix:** add `EXTRACT_TOTAL_SEC` checked in the hop loop.
- **Regression test required:** yes.
- **Dependencies / risks:** slow multi-page articles would be truncated.
- **Status:** OPEN

## PIPE-013 — Discovery fan-out has no politeness or robots policy
- **Severity / Priority / Category:** MEDIUM · P2 · DESIGN WEAKNESS
- **Evidence:** `newsbot/discover.py:310-340` — up to 14 feed candidates + 9 sitemap paths + 2 sitemap children + `robots.txt` per source; `newsbot/cycle.py:147` launches **all** sources concurrently, bounded only by `FETCH_SEM=8`; the UA is a browser string (`newsbot/net_http.py:20`); `robots.txt` is fetched only to find sitemaps (`newsbot/discover.py:320-321`) and never honoured as a policy.
- **Actual vs Expected:** Actual — a channel with 20 sources can emit ~500 requests in one burst against 2-20 hosts with no per-host delay. Expected — per-host politeness and a per-cycle request budget.
- **Impact:** 429s/bans from news sites; a self-inflicted thundering herd.
- **Fix:** per-host delay + a global per-cycle request budget + honour `robots.txt` for probe paths.
- **Regression test required:** yes (per-host request spacing).
- **Dependencies / risks:** slower discovery.
- **Status:** OPEN

## PIPE-014 — `clean_url` does not canonicalize query-parameter order => duplicate hashes for one article
- **Severity / Priority / Category:** MEDIUM · P2 · CONFIRMED BUG (reproduced)
- **Evidence:** `newsbot/discover.py:32` — the surviving query pairs keep their original order and are re-emitted with `urlencode`; `newsbot/timeutils.py:149` hashes the result. **Reproduced:** `?b=1&a=2` and `?a=2&b=1` produce two different hashes; `http://` and `https://` produce two more.
- **Actual vs Expected:** Actual — the same article referenced with a different parameter order is a different `articles.hash`; only the title fingerprint catches it, and only if the titles match exactly. Expected — one canonical hash per article.
- **Impact:** duplicate posts and duplicate AI spend for syndicated links.
- **Fix:** sort the surviving query pairs (and consider a `<link rel=canonical>` cross-check).
- **Regression test required:** yes — `?a=1&b=2` and `?b=2&a=1` must hash identically.
- **Dependencies / risks:** re-hashing changes existing article identities (migration concern).
- **Status:** OPEN

## PIPE-016 — The channel lock is held while waiting for the global cycle semaphore
- **Severity / Priority / Category:** MEDIUM · P2 · DESIGN WEAKNESS
- **Evidence:** `newsbot/cycle.py:96-100` — `async with lk: async with CYCLE_SEM:`; `CYCLE_SEM=2` while up to `MAX_CYCLES_PER_TICK=4` cycles start per tick (`newsbot/scheduler.py:122`).
- **Actual vs Expected:** Actual — 2 of 4 channels hold their channel lock while idle-waiting => `flush_ready` and manual publishes report "busy" with no work in progress. Expected — acquire the global semaphore first.
- **Impact:** channels appear blocked without any work in progress; a manual publish is refused.
- **Fix:** acquire `CYCLE_SEM` **before** the channel lock, then re-check the channel lock.
- **Regression test required:** yes.
- **Dependencies / risks:** low.
- **Status:** OPEN

## PIPE-017 — The per-channel daily cap is not enforced at reservation time (TOCTOU)
- **Severity / Priority / Category:** MEDIUM · P2 · CONFIRMED BUG
- **Evidence:** `newsbot/core_articles.py:114-134` (`reserve_publication`) reserves only the **plan** cap atomically; the channel cap is a pre-check (`newsbot/core_users.py:163-174`) called from `newsbot/cycle.py:128,210,319` and `newsbot/ui_dispatch.py:145`; `newsbot/publish.py:271-275` (the manual path) performs **no** cap check at all.
- **Actual vs Expected:** Actual — the channel cap is advisory; the manual publish path ignores it entirely. Expected — a CAS reservation like the plan cap.
- **Impact:** the channel cap can be exceeded (always on the manual path, and by 1 in a race).
- **Fix:** reserve the channel cap in the same transaction as the plan cap (a dedicated counter row).
- **Regression test required:** yes.
- **Dependencies / risks:** `posted` is the source of truth and is pruned after 90 days, so a counter column is preferable.
- **Status:** OPEN

## PIPE-018 — Changing the channel timezone resets the AI daily counter
- **Severity / Priority / Category:** MEDIUM · P2 · CONFIRMED BUG
- **Evidence:** `newsbot/ui_dispatch.py:199-204` — the day-boundary migration inserts `usage(admin_id, day, posts, tests)` and updates only `posts`/`tests`; the **`ai` column is not carried over**, although the surrounding comment states the intent is "changing the day boundary must not zero today's quota".
- **Actual vs Expected:** Actual — the daily AI cap can be reset by toggling the timezone across the local-midnight boundary. Expected — all three counters migrate.
- **Impact:** a repeatable cost-control bypass.
- **Fix:** include `ai` in the INSERT and in `DO UPDATE SET ai=MAX(ai,?)`.
- **Regression test required:** yes — assert `usage.ai` is preserved across a crossing tz change.
- **Dependencies / risks:** none.
- **Status:** OPEN

## PIPE-021 — Unsettled journal rows are never deleted => write amplification every recovery cycle
- **Severity / Priority / Category:** MEDIUM · P2 · CONFIRMED BUG
- **Evidence:** `newsbot/core_articles.py:64-79` (`release_publication`) re-writes a row already in phase `unsettled` and never deletes it; `newsbot/core_articles.py:276` only deletes journal rows whose article no longer exists, and articles live `ARCHIVE_DAYS=365` (`:273`); `recover_publications` runs every 5 minutes and performs a **write transaction per row** (`newsbot/core_articles.py:151-165`).
- **Actual vs Expected:** Actual — every 5 minutes, one `BEGIN IMMEDIATE`+commit plus an `articles` UPDATE per historically ambiguous publication (1 000 rows => ~3.3 write transactions/second, forever). Expected — a terminal state that is cleaned up.
- **Impact:** steady write load proportional to the number of historically ambiguous publications.
- **Fix:** delete the journal row once the article is in a terminal state (`failed/delivery_unknown`) or mark it terminal and exclude it from the recovery scan.
- **Regression test required:** yes — the `pub_journal` row count must not grow across repeated recovery passes.
- **Dependencies / risks:** deleting the row must not re-enable republication — `soft_window_candidate` already refuses `delivery_unknown` articles (`newsbot/core_articles.py:189`).
- **Status:** OPEN

### PIPE-005, PIPE-008 … PIPE-010, PIPE-012, PIPE-015, PIPE-019, PIPE-020, PIPE-022 — remaining pipeline findings

| ID | Title | Sev/Pri | Category | Evidence | Actual vs Expected | Impact | Fix | Test | Status |
|---|---|---|---|---|---|---|---|---|---|
| PIPE-005 | `PROMPT_VERSION` is the only guard for the hard-coded prompt template | LOW · P3 | DESIGN WEAKNESS | `newsbot/config.py:130` vs the ~30-line template at `newsbot/generate.py:19-48` | Editing the prompt without bumping the version leaves every cache entry valid => the new prompt never takes effect | Silent prompt-rollout failure | Hash the rendered system prompt into the cache key | yes | OPEN |
| PIPE-008 | `ai_usage` is written only on success and never enforced | LOW · P3 | DESIGN WEAKNESS | `newsbot/ai_models.py:284` (recorded after validation succeeded); `:113-118` (reporting only) | Calls that consumed tokens but failed validation are invisible; no token budget or cost ceiling | Cost is observable but not controllable | Record usage whenever a `usage` block arrives; add a daily token ceiling | yes | OPEN |
| PIPE-009 | `feedparser`'s `bozo` flag is never inspected => malformed feeds are silently accepted | LOW · P3 | DESIGN WEAKNESS | `newsbot/discover.py:171-174` — only `f.entries` is checked | A truncated feed yields partial/garbage entries (empty titles, wrong dates) | Low-quality items reach the AI stage | Log and treat a bozo feed as failed (or require a minimum entry count) | yes | OPEN |
| PIPE-010 | JSON Feed with a UTF-8 BOM is not detected | LOW · P3 | CONFIRMED BUG (reproduced) | `newsbot/discover.py:165-171` — `bytes.lstrip()` strips ASCII whitespace only, so a BOM leaves `\xef` at index 0 | A BOM-prefixed JSON Feed falls through to the RSS path and is degraded to "no items" -> `source_fail` | JSON-Feed sources break | Strip a leading `\xef\xbb\xbf` (and detect UTF-16 BOMs) before sniffing | yes | OPEN |
| PIPE-012 | The source failure backoff is ignored for failures 1 and 2 | LOW · P3 | CONFIRMED BUG | `newsbot/core_sources.py:58` writes `next_try_after` on every failure; `newsbot/cycle.py:143` honours it only from the 3rd failure | The advertised exponential backoff (30->60->120 min) is enforced only at >= 3 | Needless traffic to a dead source | Honour `next_try_after` whenever it is in the future | yes | OPEN |
| PIPE-015 | `clean_url` preserves URL credentials and non-normalised paths | LOW · P3 | DESIGN WEAKNESS | `newsbot/discover.py:38-42` rebuilds `netloc` **with** `cred`; dot-segments and path case are untouched. Reproduced: `https://user:pass@example.com/x` is stored unchanged | Credentials from a feed end up in `articles.url` / `sources.url` and can be echoed in a deeplink payload (`newsbot/publish.py:328`) | Credential storage/echo; a residual duplicate-hash class | Drop userinfo in `clean_url`; normalise dot-segments | yes | OPEN |
| PIPE-019 | The usage-day migration uses the edited channel's timezone, but the plan cap is keyed on the admin's *first* channel | LOW · P3 | CONFIRMED BUG | `newsbot/core_users.py:81-88` (`admin_offset` = first channel) vs `newsbot/ui_dispatch.py:199` (the edited channel) | For an admin with channels in different zones the counter is written to a day row that is not the one the cap is checked against | Quota drift | Derive the day from `admin_offset(uid)` on both sides | yes | OPEN |
| PIPE-020 | `hostname()` replaces `"www."` anywhere in the netloc | LOW · P3 | CONFIRMED BUG | `newsbot/ai_models.py:23-25` — `netloc.replace("www.", "")` | `"wwww.example.com"` -> `"w.example.com"`; `sub.www.example.com` -> `sub.example.com` | Same-host comparisons in discovery disagree with the real host | Strip only a leading `www.` | yes | OPEN |
| PIPE-022 | The "partial" media journal branch is unreachable | INFO · P3 | IMPROVEMENT OPPORTUNITY | `newsbot/core_articles.py:106-110` vs `newsbot/publish.py:190` — `caption_complete = True` is set **before** the media send | The code looks like it supports "media sent, caption pending", but media and caption are one Telegram message (`newsbot/publish.py:191`); `journal["media_id"]` is never written and the restore path at `newsbot/publish.py:330-332` is dead | No functional bug, but the dead branch hides the real invariant | Delete the `partial` branch and the `media_id` restore path | yes | OPEN |

---

# 6. NETWORKING, HTTP CLIENTS & TRANSPORT (`NET-001 … NET-008`)

## NET-001 — AI provider requests bypass all SSRF validation and follow redirects (cross-reference of SEC-001)
- **Severity / Priority / Category:** HIGH · P1 · CONFIRMED BUG
- **Evidence:** `newsbot/ai_models.py:93-95`; `newsbot/net_http.py:26-27`.
- **Actual vs Expected / Impact / Fix / Test:** see SEC-001 (identical mechanism and remediation). Tracked separately because the fix belongs in the transport layer (`newsbot/net_http.py`) rather than in the security model.
- **Status:** **VERIFIED_FIXED (2026-10-20, PH-02)** — همان SEC-001 — مسیرِ provider اکنون از `fetch_post` (با اعتبارسنجیِ هر جهش) عبور می‌کند.

## NET-002 — `x-api-key` / `apikey` / `Cookie` are not stripped on cross-origin redirects
- **Severity / Priority / Category:** MEDIUM · P2 · CONFIRMED BUG
- **Evidence:** `newsbot/net_http.py:167` — the app's own manual handling strips `{authorization, x-api-key, apikey, cookie}` on a host change; the **shared client** used by the AI path relies on httpx, which (verified in the installed httpx 0.28.1 source, `httpx/_client.py:546-556`) strips **only** `Authorization` on a cross-origin redirect.
- **Actual vs Expected:** Actual — an Anthropic-style provider call sends `x-api-key: <key>` (`newsbot/ai_models.py:124`); if the endpoint 3xxs to another origin, the key is forwarded. The Gemini path puts the key in the query string (`newsbot/ai_models.py:132`). Expected — the full credential set is stripped on a host change.
- **Impact:** provider API-key disclosure to a redirect target.
- **Fix:** use a dedicated provider client with `follow_redirects=False` and a redirect handler that strips the full credential set (re-use the rule at `newsbot/net_http.py:167`).
- **Regression test required:** yes — a cross-origin redirect must not carry `x-api-key`.
- **Dependencies / risks:** same-origin redirects keep the header, so this is safe.
- **Status:** **VERIFIED_FIXED (2026-10-20, PH-02)** — هدرهای `Authorization`/`x-api-key`/`apikey`/`cookie`/`proxy-authorization` روی تغییرِ میزبان در هر redirect حذف می‌شوند.

## NET-003 — The DNS-rebinding window is narrowed but not closed
- **Severity / Priority / Category:** MEDIUM · P2 · DESIGN WEAKNESS
- **Evidence:** `newsbot/net_http.py:104-120` — the docstring claims the rebinding window becomes "microseconds"; the implementation resolves **three** times (`:95,112,113`) and compares, but the actual TCP connection is made later by httpx/httpcore, which performs its **own** (fourth) resolution.
- **Actual vs Expected:** Actual — an attacker-controlled authoritative server (TTL 0, alternating answers) can answer the three validation lookups with a public IP and the connection lookup with `127.0.0.1`. Expected — the validated address is the one connected to (IP pinning with correct Host/SNI).
- **Impact:** the stated guarantee is stronger than the implementation.
- **Fix:** pin the validated IP or use a transport that reuses the validated address.
- **Regression test required:** yes — a resolver that returns different answers per call must be rejected.
- **Dependencies / risks:** IP pinning breaks TLS SNI/virtual hosting unless done carefully.
- **Status:** OPEN — PH-02 پنجرهٔ TOCTOU را **باریک‌تر** کرد (هر جهشِ redirect جداگانه اعتبارسنجی می‌شود) اما **نمی‌بندد**: اتصالِ واقعی همچنان توسط httpx/httpcore با رزولوشنِ خودشان انجام می‌شود. بستنِ کامل نیازمندِ pinningِ IP است (خارج از محدودهٔ این فاز).

## NET-005 — The Telegram client's connection pool is never configured
- **Severity / Priority / Category:** MEDIUM · P2 · MISSING TEST / UNKNOWN BEHAVIOR (RUNTIME-REQUIRED)
- **Evidence:** `newsbot/app.py:200-202` — the `Application` is built with no `HTTPXRequest`; no `connection_pool_size` appears anywhere in the repository; `python-telegram-bot==22.8` (`requirements.txt:26`); only the per-request media timeouts are set (`newsbot/publish.py:168`).
- **Actual vs Expected:** Actual — the effective pool size is PTB's default, which in the 20.x/21.x line is **1** (all Telegram traffic serialised, with a 240 s media upload occupying the connection). Expected — a pool sized for `CYCLE_CONCURRENCY + MAX_CYCLES_PER_TICK + UI traffic`. **The default value itself was NOT verified in this audit — RUNTIME-REQUIRED.**
- **Impact:** if the default is 1, every `send_photo`/`send_video` blocks all other Bot API calls (including `get_chat_member` used by `bot_can_post`, `newsbot/publish.py:230`) for the whole upload, and `pool_timeout=120` starts raising.
- **Fix:** `HTTPXRequest(connection_pool_size=32, read_timeout=60, write_timeout=600, pool_timeout=30)`.
- **Regression test required:** yes — assert the pool limits, and a concurrency test that N uploads overlap.
- **Dependencies / risks:** more open sockets to `api.telegram.org`.
- **Status:** OPEN

### NET-004, NET-006, NET-007, NET-008 — remaining networking findings

| ID | Title | Sev/Pri | Category | Evidence | Actual vs Expected | Impact | Fix | Test | Status |
|---|---|---|---|---|---|---|---|---|---|
| NET-004 | `is_private_host` missed multicast and the invalid-IP-literal forms | LOW · P3 | CONFIRMED BUG (measured) | `newsbot/net_http.py:80-87`; `_parse_ip_alt` rejects heads > 255 although `socket.inet_aton("0x7fffff.1")` is valid. **Measured:** `224.0.0.1` allowed, `ff02::1` allowed, `0x7fffff.1` allowed. Correctly blocked: `::ffff:127.0.0.1`, `fc00::/7`, `fe80::/10`, `100.64/10`, `0.0.0.0/8`, `198.18/15`, `192.0.2/24`, `2001:db8::/32`, `100::/64`, `metadata.google.internal`, `169.254.169.254`, and the decimal/octal/hex/short IPv4 forms | Low in practice (the fetch path also resolves and checks every IP); relevant for the input-time-only check (`newsbot/ui_inputs.py:59`) and the model path (NET-001) | Add `is_multicast`/`is_reserved`/`is_unspecified` checks; relax the `_parse_ip_alt` head limit | yes | OPEN |
| NET-006 | Compression is disabled for all outbound traffic | LOW · P3 | IMPROVEMENT OPPORTUNITY | `newsbot/net_http.py:27` — `Accept-Encoding: identity` on the shared client used for feeds, pages, media **and** AI provider calls | Every response is transferred uncompressed; expected `gzip, deflate` for text | ~3-5x more bytes for HTML/JSON feeds (not measured in this audit) | `Accept-Encoding: gzip, deflate` (httpx decodes transparently) | n/a | OPEN |
| NET-007 | An API key embedded in the URL **path** is not covered by `redact()` | LOW · P3 | DESIGN WEAKNESS | `newsbot/discover.py:274-278` substitutes the key into the path (`{key}` placeholders); `newsbot/logutils.py:39` masks secrets only in the query string and in `key=value` forms; `newsbot/core_sources.py:61` stores `redact(err)[:200]` in `sources.last_error` | A path-embedded provider key is not masked anywhere and can reach the DB and the logs | Residual secret leakage into the DB and the logs | Mask the substituted key before it can reach any message, or forbid path substitution | yes — extend `tests/test_log_secret_leak.py` | OPEN |
| NET-008 | `public_url_strict` costs three DNS lookups per hop, in a thread | LOW · P3 | IMPROVEMENT OPPORTUNITY | `newsbot/net_http.py:109-115` — 3 x `socket.getaddrinfo` per hop, executed via `asyncio.to_thread` (`newsbot/net_http.py:154,164`), sharing the default executor with the CPU-bound trafilatura work (`newsbot/extract.py:179,188,197`) | 3x DNS amplification per request, on a shared executor | Resolver load and executor contention; not measured in this audit | Cache the resolution per (host, short TTL) and reuse it for the connection (see NET-003) | n/a | OPEN |

---

# 7. PERFORMANCE & LATENCY (`PERF-001 … PERF-007`)

## PERF-001 — `q()` is executed directly on the event loop in the AI/publish hot path
- **Severity / Priority / Category:** MEDIUM · P2 · CONFIRMED BUG
- **Evidence:** `newsbot/generate.py:103,117,136,175`; `newsbot/ai_models.py:337,352`; `newsbot/net_http.py:205,212,214` — synchronous SQLite calls inside `async def`. The module docstring `newsbot/db.py:6-7` states the opposite: "async paths never run a query on the event loop". A further set exists in the UI layer (`newsbot/ui_dispatch.py:202,203,249,285,319,323,340,348,358,359`, `newsbot/ui_views_super.py:39,41,44,46,135,136,167,227` — including `PRAGMA quick_check(1)`, a full integrity scan, on the loop).
- **Actual vs Expected:** Actual — a synchronous SELECT/INSERT plus a possible 20-160 ms blocking sleep (`newsbot/db.py:415-416`) runs on the event loop. Expected — all DB work via `arun`.
- **Impact:** the whole bot (Telegram updates, the scheduler tick, other channels) stalls for the duration of the query and longer under lock contention (`busy_timeout=15000`).
- **Fix:** wrap these call sites in `arun(...)`.
- **Regression test required:** yes — assert no `q()` executes on the loop thread (mirror `tests/test_phase8_concurrency.py::test_arun_does_not_block_event_loop`).
- **Dependencies / risks:** `arun` uses a 4-worker pool (PERF-003) — the offload must not become the new bottleneck.
- **Status:** OPEN

## PERF-002 — `ch_lock` / `model_lock` perform O(N) blocking DB queries per acquisition once > 200 entries exist
- **Severity / Priority / Category:** MEDIUM · P2 · CONFIRMED BUG
- **Evidence:** `newsbot/concurrency.py:17-23` — the prune list comprehension calls `get_channel(k)` for **every** key (not only for deleted channels); `model_lock` does the same with `get_model` (`:27-29`). `get_channel` is a blocking `q()` (`newsbot/core_channels.py:61`) and `ch_lock()` is called from `async def` contexts (`newsbot/cycle.py:96,308`; `newsbot/publish.py:274`).
- **Actual vs Expected:** Actual — with > 200 channels, **every** lock acquisition issues hundreds of blocking queries on the event loop, per cycle, per publish. Expected — an amortised prune.
- **Impact:** quadratic-ish growth in the scheduler's busiest path; blocks the event loop (PERF-001).
- **Fix:** prune at most N entries at most once per minute, and move the lookup off the loop.
- **Regression test required:** yes — 300 channels => the number of queries per `ch_lock()` is bounded.
- **Dependencies / risks:** none.
- **Status:** OPEN

## PERF-004 — `docs/PERFORMANCE.md` is a DB-only microbenchmark; the pipeline has no baseline
- **Severity / Priority / Category:** MEDIUM · P2 · DOCUMENTATION MISMATCH
- **Evidence:** `docs/PERFORMANCE.md:8-13` — four single-threaded SQLite blocks; `tools/benchmark.py:75-78` measures exactly those four. Nothing touches discovery, extraction, HTTP, the AI path, publishing, concurrency or the scheduler. The published numbers are arithmetically consistent with the harness (`nref = ops//5 = 400`; 2000/0.2952 = 6774.9) but were **not re-measured** in this audit.
- **Actual vs Expected:** Actual — no load-test harness, single sample per block, no warm-up, no variance, and `bench.json` is not committed. Expected — a reproducible baseline that covers the audited domain.
- **Impact:** a regression in the pipeline (PERF-001/PERF-002) would not be detected by it.
- **Fix:** extend `tools/benchmark.py` with a concurrent cycle simulation over a temp DB and a mocked-HTTP pipeline; >= 3 repetitions with min/median/max; commit the raw JSON; state the machine.
- **Regression test required:** n/a (harness work).
- **Dependencies / risks:** the harness must stay offline to remain reproducible.
- **Status:** OPEN

## PERF-007 — No bound on the number of concurrent publishes across channels
- **Severity / Priority / Category:** MEDIUM · P2 · DESIGN WEAKNESS
- **Evidence:** `newsbot/scheduler.py:94-103` — `flush_ready` is invoked for **every** channel on **every** tick, before the due/quiet gates; `newsbot/cycle.py:307-334` publishes up to `max_n=2` articles per channel and takes **no global semaphore** (only the per-channel lock).
- **Actual vs Expected:** Actual — with N channels holding a backlog, one tick can start up to 2N concurrent Telegram uploads on one Bot API connection pool. Expected — a global publish semaphore and a per-tick budget.
- **Impact:** bursty Bot API load, connection-pool exhaustion, `pool_timeout` errors (see NET-005).
- **Fix:** add a global publish semaphore and a per-tick publish budget.
- **Regression test required:** yes.
- **Dependencies / risks:** throttling changes throughput.
- **Status:** OPEN

### PERF-003, PERF-005, PERF-006 — remaining performance findings

| ID | Title | Sev/Pri | Category | Evidence | Actual vs Expected | Impact | Fix | Test | Status |
|---|---|---|---|---|---|---|---|---|---|
| PERF-003 | The DB thread pool has 4 workers and is shared with long-running maintenance | LOW · P3 | DESIGN WEAKNESS | `newsbot/db.py:20` (`_DB_POOL_SIZE = 4`); `archive_backup` (`newsbot/core_articles.py:205-223`) and `VACUUM` (`:227-242`) run from `cleanup()` (`newsbot/scheduler.py:80`) | A 2 GB backup + VACUUM can starve the pool every cycle depends on | Cycle latency spikes during the hourly cleanup | Give maintenance its own executor, or bound the work | yes | OPEN |
| PERF-005 | Startup: a one-time full scan of 18 tables dominates `init_core` | LOW · P3 | DESIGN WEAKNESS | `newsbot/db.py:285-311` — 18 `(table, column)` pairs rewritten inside the schema transaction, guarded by `meta.iso_norm_v1` | The first boot after a DB-14 build rewrites every timestamp in one transaction (minutes on a large DB) | One-off long startup after upgrade | Batch the canonicalisation outside the schema transaction and report progress | yes | OPEN |
| PERF-006 | `subprocess.run("git rev-parse")` blocks the event loop | LOW · P3 | CONFIRMED BUG | `newsbot/ui_views_super.py:87-93` (up to 3 s), called from `async def view_health` (`:36,65`) | A synchronous process spawn on the event loop | A 3 s stall of the whole bot when a super admin opens `/health` | `await asyncio.to_thread(_code_version)` or cache at startup | no | OPEN |

---

# 8. PUBLISHING, SCHEDULING & EXACTLY-ONCE (`PUB-001 … PUB-005`)

**The exactly-once publishing mechanism is the strongest part of the system** and is documented here as a positive control. The journal state machine is `prepared -> in_flight -> partial -> ack -> settle`; the terminal transition (`posted` insert + `articles` update + journal delete) is a single `_trx` (`newsbot/core_articles.py:136-146`); recovery is idempotent and distinguishes `ack` (settle) from everything else (release + `delivery_unknown`) (`newsbot/core_articles.py:149-165`). The ambiguous-delivery case is resolved conservatively: a `delivery_unknown` article is never recycled (`newsbot/core_articles.py:189`) and never re-queued (`newsbot/cycle.py:193-196`) — **lost posts, never duplicated posts**. This is covered by `tests/test_exactly_once_limits.py` (13 tests), `tests/test_recovery_concurrency.py` (8) and `tests/test_phase8_concurrency.py`.

## PUB-002 — A DB failure between the send and the `posted` insert loses the post
- **Severity / Priority / Category:** MEDIUM · P2 · DESIGN WEAKNESS
- **Evidence:** `newsbot/publish.py:360-373` — `journal.update(phase="ack", link=link)` then `await arun(set_pub_journal, ...)` then `await arun(settle_publication, ...)`; the `finally` at `newsbot/publish.py:389-393` skips the release because `published` is `True`.
- **Actual vs Expected:** Actual — the message is delivered but never recorded => on restart the article is marked `failed/delivery_unknown` and never re-sent (correct) — but the post is invisible to the operator and the daily quota was consumed. Expected — retry the settle with backoff and record a distinct `settle_failed` reason.
- **Impact:** silent post loss + quota consumption, with no hint that the message may be live.
- **Fix:** retry the settle; record `settle_failed`.
- **Regression test required:** yes — inject a DB error between the send and the settle.
- **Dependencies / risks:** low.
- **Status:** OPEN

### PUB-001, PUB-003, PUB-004, PUB-005 — remaining publishing findings

| ID | Title | Sev/Pri | Category | Evidence | Actual vs Expected | Impact | Fix | Test | Status |
|---|---|---|---|---|---|---|---|---|---|
| PUB-001 | The exactly-once mechanism is sound, but there is no Telegram-side idempotency key (documented trade-off) | INFO · P3 | DESIGN WEAKNESS | `newsbot/core_articles.py:136-146`; `:91-96` | The state machine is correct; the unavoidable gap is that "sent but not recorded" is indistinguishable from "not sent", resolved conservatively as `delivery_unknown` | **Lost posts, never duplicated posts** — the correct trade-off, explicitly acknowledged in the code and covered by tests | None required; optionally record `delivery_unknown` for operator review | already present | OPEN |
| PUB-003 | The channel lock is held across the whole Telegram network call | LOW · P3 | DESIGN WEAKNESS | `newsbot/publish.py:271-275` — `async with ch_lock(...)` spans the media download (`newsbot/publish.py:333`, up to `MEDIA_TOTAL_SEC=600 s`) and the upload (`newsbot/publish.py:169`, `write_timeout=600`) | Correct for exactly-once, but it serialises a potentially 10-minute operation against every other operation on that channel (manual publish, queue reset, channel delete — `newsbot/core_channels.py:79-82`) | A slow media host blocks the channel for up to 10 minutes; `publication_pending` then blocks channel deletion/transfer | Download the media **before** taking the channel lock; reduce `MEDIA_TOTAL_SEC` | partially covered | OPEN |
| PUB-004 | `posted` rows are pruned after 90 days while the article survives 365 days | INFO · P3 | DESIGN WEAKNESS | `newsbot/core_articles.py:274` | After 90 days the `posted` duplicate guard no longer fires, while `articles` still holds `UNIQUE(channel_id, hash)` for 365 days (`:273`) and the cycle's `_have` set (`newsbot/cycle.py:165`) still suppresses re-processing | The practical guarantee survives, but the two records can disagree; a very old article re-inserted with the same hash could be published twice | Align the retention of `posted` with `articles`, or keep a hash-only record | n/a | OPEN |
| PUB-005 | The `posted` uniqueness is per `chat_id` while the article hash is per internal channel id | LOW · P3 | DESIGN WEAKNESS | `newsbot/core_articles.py:182` (`posted_before(chat_id, h)`) vs `newsbot/db.py:31-32` (the `posted` table), where `posted.channel_id` stores the **chat_id** (`newsbot/core_articles.py:142`) | The 1:1 mapping is implicit and undocumented; if a channel's `chat_id` ever changes the guard no longer matches | Low today — the code paths that change `chat_id` delete the old `posted` rows (`newsbot/core_channels.py:98,138`) | Document the invariant or key `posted` on the internal channel id | n/a | OPEN |

---

# 9. CI/CD, DEPLOYMENT, SYSTEMD & OPERATIONAL DOCS (`OPS-001 … OPS-031`)

## OPS-001 — The deployment identity belongs to a different project
- **Severity / Priority / Category:** HIGH · P0 · CONFIRMED CONFIGURATION DEFECT
- **Evidence:** `deploy/deploy.sh:17-19` — `APP_DIR="${APP_DIR:-/opt/bot-ai-automatic}"`, `BACKUP_DIR="${BACKUP_DIR:-/opt/backups}"`, `SERVICE="${SERVICE:-bot-ai-automatic}"`; `docs/DEPLOY_RUNBOOK.md:171` -> `/etc/systemd/system/bot-ai-automatic.service`; `deploy/systemd/10-restart-always.conf:7` -> the `ExecStopPost` logger tag is also `bot-ai-automatic`; `git grep -n "newsbot.service"` -> **0 matches**; `git grep -n "bot-official-telegram"` -> **0 matches**; `git grep -c "bot-ai-automatic"` -> **51 occurrences across 5 tracked files** (`deploy/deploy.sh`, `deploy/systemd/10-restart-always.conf`, `docs/DEPLOY_RUNBOOK.md`, `DEPLOY-VPS-UPDATE.md`, `README.md`).
- **Actual vs Expected:** Actual — every path/unit identifier in the deploy chain and all four operator documents points at `/opt/bot-ai-automatic` + `bot-ai-automatic.service` (residue from an unrelated repository), while the project identity is `Bot-Official-Telegram-` / `/opt/bot-official-telegram` + `newsbot.service`. Expected — the deployment identity matches the project.
- **Impact:** every operator command in `README.md:449-458`, `DEPLOY-VPS-UPDATE.md` (21 occurrences) and `docs/DEPLOY_RUNBOOK.md` (24 occurrences) is wrong. `systemctl restart` fails -> `deploy.sh:90-93` calls `die` instead of rolling back (OPS-026) -> the release aborts **after** sync+deps with new code on disk and old code running.
- **Verification:** **REPRODUCED (2026-10-20, PH-00)** — the repository-side evidence was re-verified mechanically: the three `deploy.sh` defaults above, the 51/0/0 occurrence counts, and the five affected files. **The production side could NOT be verified** (no VPS access — see §13), so whether the live host actually runs `bot-ai-automatic` remains **UNVERIFIED**.
- **Fix:** single-source `APP_DIR`/`SERVICE`/`UNIT` constants (e.g. `deploy/systemd/newsbot.service` + `deploy/env.example`), rename `bot-ai-automatic` -> `newsbot` / `/opt/bot-official-telegram` everywhere, and ship the base unit file.
- **Regression test required:** yes — extend `tests/test_deploy_script.py` to assert the script defaults equal the values used in the runbook and README.
- **Dependencies / risks:** the real host's unit name must be read first (PH-00); **the rename must not be applied before that read**, because a wrong rename would make a currently-working service unstartable. Support both via an explicit `SERVICE=` override during the transition.
- **Owner decision (2026-10-20, Q-6c):** **read the host first, then decide.** If the rename is later approved, the old unit name (`bot-ai-automatic`) is kept as an **alias for one release** so the "restart the previous unit" rollback path stays intact.
- **Status:** OPEN — **the fix is blocked on the PH-00 production read** (no VPS access in this environment; see §13).

## OPS-002 — The health-gate budget (10 s) contradicts `Type=notify` / `TimeoutStartSec=180`
- **Severity / Priority / Category:** HIGH · P0 · CONFIRMED BUG
- **Evidence:** `deploy/deploy.sh:21` — `HEALTH_TRIES="${HEALTH_TRIES:-10}"`; `deploy/deploy.sh:96-107` — `for i in $(seq 1 "$HEALTH_TRIES"); do if systemctl is-active --quiet "$SERVICE"; then break; fi ... sleep 1`; `deploy/systemd/20-watchdog.conf:2,5` -> `Type=notify` + `TimeoutStartSec=180`; `newsbot/app.py:73` — `watchdog.notify_ready()` is the **last** statement of `post_init` (after `get_me()` `:50`, two `set_my_commands` `:55-56`, `notify_supers` `:71`).
- **Actual vs Expected:** Actual — with `Type=notify`, `is-active` stays false until `READY=1` (>= 3 Telegram round-trips) but the gate allows only 10 x 1 s; **without** the drop-in (`Type=simple`) `is-active` is true within milliseconds, so the loop breaks on iteration 1 and the `NRestarts`/journal checks run against a service that has not loaded its code. Expected — wait for `READY=1` with a budget derived from `TimeoutStartSec`.
- **Impact:** (a) on a slow/degraded Telegram API a **successful** deploy is declared failed and rolled back (false negative); (b) without the drop-in a crash-looping bot failing 30 s after start is reported healthy (false positive). Both corrupt trust in the deploy signal.
- **Fix:** wait for `READY=1` with a budget derived from `TimeoutStartSec` (e.g. 120 s), then assert `NRestarts` and a **post-restart-only** journal window; treat `Type=notify` as mandatory and install the drop-in as part of the deploy.
- **Regression test required:** yes — extend the `systemctl`/`journalctl` stubs to simulate delayed activation and immediate exit.
- **Dependencies / risks:** a longer deploy window (the job timeout is 25 min).
- **Status:** **VERIFIED_FIXED (2026-10-20, PH-04)** — بودجهٔ سلامت ۱۰ ثانیه → ۱۸۰ ثانیه (هم‌ارز TimeoutStartSec) و برای `Type=notify` تا `SubState=running` (دریافت READY=1) منتظر می‌ماند.

## OPS-003 — The health gate greps a *rolling* 120 s journal window for "traceback"
- **Severity / Priority / Category:** HIGH · P1 · CONFIRMED BUG
- **Evidence:** `deploy/deploy.sh:115-118` — `journalctl -u "$SERVICE" --since "@$(($(date +%s) - 120))" --no-pager | grep -Eiq 'traceback|fatal error'` -> `return1`; `newsbot/logutils.py:52-54` — `rec.exc_text = redact("".join(traceback.format_exception(*rec.exc_info)))`; the app logs handled exceptions via `log.exception` in **11** places (`newsbot/app.py:109`, `newsbot/publish.py:357,384`, `newsbot/ui_dispatch.py:153,377`, `newsbot/ui_widgets.py:427`, `newsbot/ui_views_super.py:273,287`, `newsbot/core_articles.py:165,267`, `newsbot/ai_models.py:357`).
- **Actual vs Expected:** Actual — `redact()` only strips secrets, so the literal `Traceback (most recent call last):` survives into the journal; one non-fatal publication/payment/model-probe error in the 2 minutes before a deploy fails the health check -> `rollback_and_die` (`deploy.sh:124-133`) reverts a perfectly good release. The window is relative to *now*, so it also covers the previous incarnation. Expected — anchor the window to the restart epoch and match crash markers, never the free-text word "traceback".
- **Fix:** capture `date +%s` immediately before `systemctl restart` and use `--since "@$EPOCH"` plus `-p err` and the unit's `Result=`/`ExecMainStatus`.
- **Regression test required:** yes — stub `journalctl` emitting a traceback *before* the restart epoch.
- **Dependencies / risks:** coupled to OPS-002.
- **Status:** **VERIFIED_FIXED (2026-10-20, PH-04)** — پنجرهٔ ژورنال به لحظهٔ همین restart لنگر شد (`RESTART_EPOCH`) و فقط نشانه‌های مرگبار (Main process exited / failed with result / core dumped / watchdog timeout) بررسی می‌شوند — «traceback» معیار نیست.

## OPS-004 — The deploy script that runs on the VPS is taken from the default branch, not from `TARGET_SHA`
- **Severity / Priority / Category:** MEDIUM · P1 · CONFIRMED BUG
- **Evidence:** `deploy-vps.yml:46` — `uses: actions/checkout@11d5960a...` with **no `ref:`**; `deploy-vps.yml:56-57` — `source: deploy/deploy.sh`, `target: /tmp/newsbot-deploy.sh`.
- **Actual vs Expected:** Actual — a checkout with no `ref` materialises the **default branch head**, not `workflow_run.head_sha`; the uploaded script may be a *newer* commit than the deployed one, and the checkout step is otherwise unused (the real sync is `git reset --hard` on the VPS). Expected — the tool and the artefact come from the same commit.
- **Impact:** deployment mechanism and deployed artefact can come from different commits; fixes to `deploy.sh` (OPS-002, OPS-003, OPS-006) do not take effect until a later deploy; "which tooling deployed SHA X" traceability is broken.
- **Fix:** `with: ref: ${{ env.TARGET_SHA }}` on the checkout step, or fetch the script via `git show $TARGET_SHA:deploy/deploy.sh`.
- **Regression test required:** no (workflow-only) — add a repo lint asserting every `actions/checkout` in a deploy workflow pins `ref` to the target SHA.
- **Dependencies / risks:** none.
- **Status:** **VERIFIED_FIXED (2026-10-20, PH-04)** — `actions/checkout` با `ref: ${{ env.TARGET_SHA }}` سنجاق شد ⇒ اسکریپتِ استقرار و کدِ مستقرشده از یک کامیت می‌آیند.

## OPS-005 — `workflow_dispatch` bypasses the green-CI gate entirely
- **Severity / Priority / Category:** MEDIUM · P1 · DESIGN WEAKNESS
- **Evidence:** `deploy-vps.yml:22-25` — `if: github.event_name == 'workflow_dispatch' || (github.event.workflow_run.conclusion == 'success' && github.event.workflow_run.event == 'push')`; `docs/DEPLOY_RUNBOOK.md:288` recommends `workflow_dispatch` for the first production deploy.
- **Actual vs Expected:** Actual — any SHA reachable from the selected ref can be deployed to production without ever passing CI. Expected — the manual path names a SHA with a successful CI run, or is an explicitly-named break-glass workflow.
- **Impact:** the runbook's central invariant ("red = never deployed", `README.md:431`) is trivially bypassable by anyone with write access, with no record that the manual deploy was CI-verified.
- **Fix:** require the manual path to name a SHA with a successful CI run (query check runs); keep a separate, explicitly-named break-glass workflow.
- **Regression test required:** no (workflow logic) — add a policy check.
- **Dependencies / risks:** the first production deploy needs a deliberate exception.
- **Status:** **VERIFIED_FIXED (2026-10-20, PH-04)** — مسیرِ `workflow_dispatch` حالا SHA اجباری می‌گیرد و با `gh api` یک اجرای موفقِ CI برای همان SHA را **fail-closed** مطالببه می‌کند.

## OPS-006 — Automatic rollback reverts the code but **not** the installed dependencies
- **Severity / Priority / Category:** MEDIUM · P1 · CONFIRMED BUG
- **Evidence:** `deploy/deploy.sh:148` — `install_deps || rollback_and_die "dependency install failed"`; `deploy/deploy.sh:124-133` — `rollback_and_die() { ... git reset -q --hard "$prev" || true; if [ "$RESTARTED" -eq 1 ]; then systemctl restart "$SERVICE" || true; fi; exit 1; }`; `install_deps` is `deploy.sh:75-80`; the manual procedure gets this right (`DEPLOY-VPS-UPDATE.md:118` re-runs `pip install -r requirements.txt` after the reset).
- **Actual vs Expected:** Actual — `install_deps` mutates the shared `.venv` before the gate; on a later failure the rollback restores `HEAD` to `$prev` but leaves the **new** pip-resolved dependencies installed against the **old** source tree. Expected — old code runs against old dependencies.
- **Impact:** after a failed deploy the running service can execute old code against new libraries — neither release is actually in place.
- **Fix:** in `rollback_and_die`, after `git reset --hard "$prev"` re-run `.venv/bin/pip install -r requirements.txt` (now the *old* requirements) before restarting; preferably snapshot `pip freeze` before `install_deps`.
- **Regression test required:** yes — the `pip` stub records invocations; assert a failing gate produces a second `pip install` after the reset.
- **Dependencies / risks:** none.
- **Status:** **VERIFIED_FIXED (2026-10-20, PH-04)** — rollback علاوه بر کد، دوباره `pip install -r requirements.txt` را اجرا می‌کند ⇒ کدِ قدیمی روی وابستگی‌هایِ قدیمی اجرا می‌شود.

## OPS-007 — The gate runs as `root` while the service runs unprivileged; ownership is never reconciled
- **Severity / Priority / Category:** MEDIUM · P1 · DESIGN WEAKNESS
- **Evidence:** `deploy-vps.yml:52,63` — `username: root`; `docs/DEPLOY_RUNBOOK.md:182-190` — `User=botapp`, `WorkingDirectory=/opt/bot-ai-automatic`, `ProtectSystem=strict`, `ReadWritePaths=/opt/bot-ai-automatic`; `newsbot/app.py:134-137` — the preflight checks `os.access(folder, os.W_OK)`.
- **Actual vs Expected:** Actual — every mutating step (`git reset --hard`, `pip install`, `compileall`) runs as root, so the gate's `os.access(..., W_OK)` checks are **always true**; nothing re-`chown`s `$APP_DIR` to `botapp` after the root-run sync/install. Expected — the gate certifies that the service account can operate the deployment.
- **Impact:** permission problems surface as a crash-loop *after* the restart, which — combined with OPS-002 — may be classified healthy.
- **Fix:** run the gate as the service account (`sudo -u botapp .venv/bin/python run.py --check`), or add `chown -R botapp:botapp $APP_DIR` + `chmod` after `install_deps`.
- **Regression test required:** partial — assert the gate runs under the service UID.
- **Dependencies / risks:** none.
- **Status:** OPEN

## OPS-008 — `.env` is never provisioned or permission-hardened; `EnvironmentFile=-…` tolerates its absence
- **Severity / Priority / Category:** MEDIUM · P2 · DESIGN WEAKNESS
- **Evidence:** `deploy/deploy.sh:33` — `[ -f "$APP_DIR/.env" ] || die ".env missing in $APP_DIR"`; `docs/DEPLOY_RUNBOOK.md:184` — `EnvironmentFile=-/opt/bot-ai-automatic/.env`; `deploy-vps.yml:33-43` preflight checks only the three SSH secrets.
- **Actual vs Expected:** Actual — the pipeline neither creates nor uploads `.env` (it must pre-exist, hand-made); the leading `-` makes systemd *ignore a missing file* -> the unit starts with an empty environment and the bot dies in `preflight` (`newsbot/app.py:118-119`) inside a `Restart=always` loop; no document states the required ownership/mode (contrast `newsbot/db.py:320`, which forces the DB file to `0600`). Expected — a required, owner-only `EnvironmentFile`.
- **Impact:** `BOT_TOKEN`/`CF_*_TOKEN` live in a file of undefined permissions (a world-readable `.env` on a shared host leaks tokens); a missing `.env` yields an infinite restart loop instead of a loud failure.
- **Fix:** drop the `-` prefix so systemd refuses to start with a clear error; document and enforce `chown botapp:botapp` + `chmod 600 .env`; add permission assertions to the preflight.
- **Regression test required:** no (config/docs) — or a static assertion that `deploy.sh` verifies the `.env` mode.
- **Dependencies / risks:** none.
- **Status:** **FIXED_UNVERIFIED (2026-10-20, PH-04)** — پیش‌خیم: اگر `EnvironmentFile` یونیت اختیاری باشد (`EnvironmentFile=-`) استقرار هشدار می‌دهد و مسیرِ حلقهٔ ری‌استارت را توضیح می‌دهد. **خودِ یونیت هنوز اصلاح نشده** — فایلِ یونیت در مخزن نیست و ساختنش به تأییدِ هویتِ سرویس روی میزبان نیاز دارد (PH-00).

## OPS-009 — Backups are unverified, host-local only, and never restore-tested
- **Severity / Priority / Category:** MEDIUM · P2 · MISSING TEST / UNKNOWN BEHAVIOR
- **Evidence:** `deploy/deploy.sh:47-60` — the backup is written and `chmod 600`'d but never verified (no `PRAGMA integrity_check`, no reopen, no row-count/fingerprint); the 7 retained copies live in `/opt/backups` on the same host/disk as the live DB; no off-host copy exists in the repository.
- **Actual vs Expected:** Actual — "we have 7 backups" is unverified; a truncated backup surfaces only during recovery; total VPS loss = total data loss. Expected — each backup is verified on creation and copied off-host.
- **Fix:** verify each backup immediately (reopen + `PRAGMA quick_check` + row-count sanity), fail the deploy if verification fails, and add an off-host copy step with a documented RPO/RTO.
- **Regression test required:** yes — `tests/test_deploy_script.py:202` only asserts `st_size > 0`; add a corruption-injection case.
- **Dependencies / risks:** storage/egress cost of off-host copies.
- **Status:** **VERIFIED_FIXED (2026-10-20, PH-04)** — هر بکاپ پیش از ادامه باز و با `PRAGMA quick_check` و مقایسهٔ تعداد اشیا با مبدأ تأیید می‌شود.

## OPS-011 — The operator-facing recovery procedure restores a WAL-mode database with a raw `cp` and without clearing `-wal`/`-shm`
- **Severity / Priority / Category:** HIGH · P1 · DOCUMENTATION MISMATCH / CONFIRMED BUG (in the documented procedure)
- **Evidence:** `DEPLOY-VPS-UPDATE.md:120-123` — `systemctl stop` / `cp /opt/backups/newsbot-<TIMESTAMP>.db newsbot.db` / `systemctl start`; `newsbot/db.py:245` — `PRAGMA journal_mode=WAL`; `docs/DEPLOY_RUNBOOK.md:74-75` — "raw `cp` of a live database is forbidden".
- **Actual vs Expected:** Actual — the documented restore overwrites the main DB while stale `newsbot.db-wal`/`-shm` sidecars remain; on the next open SQLite replays the stale WAL over the restored file => silent-mix corruption; it also contradicts the runbook's own prohibition. Expected — the documented procedure removes the sidecars and uses the `sqlite3.backup` restore from `docs/DEPLOY_RUNBOOK.md:80-89`, followed by `PRAGMA integrity_check`.
- **Impact:** the single most likely disaster-recovery command in the operator handbook can corrupt the data it is meant to recover.
- **Fix:** publish **one** restore procedure referenced by all three documents.
- **Regression test required:** yes — restore over a WAL database with sidecars present and assert the content equals the backup.
- **Dependencies / risks:** none.
- **Status:** OPEN

## OPS-026 — A failed `systemctl restart` leaves the new code on disk with the old code running, and skips the rollback
- **Severity / Priority / Category:** MEDIUM · P2 · CONFIRMED BUG
- **Evidence:** `deploy/deploy.sh:90-93` — `start_service() { RESTARTED=1; systemctl restart "$SERVICE" || die "systemctl restart failed"; }`; `deploy.sh:148-151` — `install_deps || rollback_and_die ...` / `gate || rollback_and_die ...` / `start_service` / `health || rollback_and_die ...`.
- **Actual vs Expected:** Actual — `start_service` calls `die` (=> `exit 1`) instead of returning a failure code, so the `rollback_and_die` path is bypassed; `RESTARTED=1` is set *before* the restart attempt so even the flag is wrong. Expected — a failed restart triggers the rollback.
- **Impact:** the most common failure for this project (OPS-001: the wrong unit name => the restart fails) leaves the host in a mixed state requiring manual diagnosis.
- **Fix:** make `start_service` return 1 and route it through `rollback_and_die`; set `RESTARTED=1` only after a successful restart.
- **Regression test required:** yes — the `systemctl` stub can fail `restart` and assert HEAD is restored to `$prev`.
- **Dependencies / risks:** none.
- **Status:** **VERIFIED_FIXED (2026-10-20, PH-04)** — شکستِ `systemctl restart` از مسیرِ `rollback_and_die` می‌گذرد (به‌جای exitِ خام) ⇒ کد و وابستگی‌ها برمی‌گردند و سرویس روی نسخهٔ قبلی بالا می‌آید.

## OPS-028 — `deploy/deploy.sh` is committed non-executable, so the documented invocation fails
- **Severity / Priority / Category:** MEDIUM · P2 · CONFIRMED BUG
- **Evidence:** `git ls-files -s deploy/deploy.sh` -> `100644 0d592f8d... deploy/deploy.sh`; `docs/DEPLOY_RUNBOOK.md:231` — `./deploy/deploy.sh deploy <MAIN_SHA>`.
- **Actual vs Expected:** Actual — the file mode is `100644` (no execute bit), so the documented command fails with `Permission denied`; only the workflow (`chmod +x ...; bash ...`, `deploy-vps.yml:70-71`) and the test (`bash ...`, `tests/test_deploy_script.py:89`) work. Expected — the documented command works.
- **Fix:** `git update-index --chmod=+x deploy/deploy.sh` (and the other shell entry points), or change the docs to `bash deploy/deploy.sh ...`.
- **Regression test required:** yes — a static test asserting `deploy/deploy.sh` is executable in the git index (`git ls-files -s`).
- **Status:** **VERIFIED_FIXED (2026-10-20, PH-00)** — the git index mode was changed from `100644` to `100755` and the regression test `tests/test_repo_integrity.py::test_deploy_script_is_executable_in_git_index` now enforces it.

### OPS-002b, OPS-010 … OPS-014, OPS-016 … OPS-025, OPS-027, OPS-029 … OPS-031 — remaining ops findings

| ID | Title | Sev/Pri | Category | Evidence | Actual vs Expected | Impact | Fix | Test | Status |
|---|---|---|---|---|---|---|---|---|---|
| OPS-010 | The archive-backup folder the runbook mandates is never captured by the deploy backup | MEDIUM · P2 | CONFIRMED CONFIGURATION DEFECT | `docs/DEPLOY_RUNBOOK.md:124-125` says the folder must be included in VPS backups; `newsbot/core_articles.py:200-210` writes snapshots to `<db_dir>/backups/`; `deploy/deploy.sh:40` backs up only `newsbot.db` | The pre-delete archive snapshots (the only copy of articles older than `ARCHIVE_DAYS`) are in no backup | A documented data-protection requirement is silently unmet; archived content is unrecoverable after host loss | Back up the whole DB directory or fold archive files into the same backup set; update the runbook | yes — seed `$APP_DIR/backups/` in the sandbox test | **VERIFIED_FIXED (PH-04)** |
| OPS-012 | No recovery runbook for a corrupted database or for "DB schema newer than code" | MEDIUM · P2 | MISSING TEST / UNKNOWN BEHAVIOR | `newsbot/db.py:329-331` raises `SystemExit("database schema_version ... is newer than this build ...")`; `DEPLOY-VPS-UPDATE.md:204-208` has no entry for it | The code refuses to start by design and `/health` detects corruption, but neither condition has a documented operator procedure | After a botched rollback (exactly what `deploy.sh:127` produces) the service enters a `Restart=always` loop visible only in the journal | Add a "Recovery runbook" section: schema-newer-than-build, corrupted DB (`.recover`/`integrity_check`), WAL-sidecar cleanup; cross-link from the deploy failure message | yes — a DB with `schema_version = SCHEMA_VERSION+1` must fail `--check` with an actionable message (already true at `newsbot/db.py:499-500`) and the docs must mention it | OPEN |
| OPS-013 | Migration/restore tools have no `--dry-run`; one flag both authorises destruction and suppresses a safety check | MEDIUM · P2 | DESIGN WEAKNESS | `tools/migrate_sqlite_to_d1.py:32` (`FORCE`), `:128-130` (overwrite guard), `:204-206` (version guard also disabled by `--force`); `tools/restore_d1_to_sqlite.py:47-49` | The operator cannot preview a migration; `--force` conflates "authorise overwrite" with "ignore version mismatch" | Risky destructive tooling with no dry-run, on a backend declared unsupported in `docs/D1_DECISION.md:9-13` | Add `--dry-run`; split `--force` into `--force-overwrite` and `--force-version`; require interactive confirmation | yes — `tests/test_deployment.py:137` only `ast.parse`s the tools | **VERIFIED_FIXED (PH-04)** |
| OPS-014 | `READY=1` depends on Telegram API reachability: a Telegram outage becomes a systemd restart loop | MEDIUM · P2 | DESIGN WEAKNESS | `newsbot/app.py:49-73` — `get_me()` `:50`, two `set_my_commands` `:55-56`, `notify_supers` `:71`, then `watchdog.notify_ready()` `:73`; `deploy/systemd/20-watchdog.conf:2-5` (`Type=notify`, `TimeoutStartSec=180`); `deploy/systemd/10-restart-always.conf:5-6` (`Restart=always`, `RestartSec=5`) | With `Type=notify` the unit stays *activating* until `READY=1`; any earlier exception means it is never sent -> after 180 s systemd kills it -> `Restart=always` restarts forever, with `StartLimitIntervalSec=0` disabling the safety net | A transient Telegram outage becomes an endless restart storm; the deploy health gate then rolls back the release, blaming the code | Send `READY=1` as early as possible (right after `Application.builder().build()`), before networked calls | yes — mock `get_me` to raise and assert `notify_ready` was still called | OPEN |
| OPS-015 | Watchdog pings are emitted from the same event loop they are meant to supervise | LOW · P3 | DESIGN WEAKNESS | `newsbot/watchdog.py:41-57` — `watchdog_interval()` = `WATCHDOG_USEC / 3_000_000` and `watchdog_loop` sends `WATCHDOG=1` from a task on the same loop; `deploy/systemd/20-watchdog.conf:4` -> `WatchdogSec=300` (ping every 100 s) | The interval is correct per systemd guidance, but any blocking call > 100 s (blocking HTTP, a long SQLite write, a CPU-bound article loop) starves the ping | False-positive restarts during legitimately long operations; the recovery (`Restart=always`) is itself disruptive (in-flight publications are recovered by `recover_publications`, `newsbot/app.py:37`) | Emit the ping from a dedicated OS thread independent of the event loop, and/or raise `WatchdogSec` to 600 s with a matching interval | yes — block the loop for > 1 interval and assert the ping still arrives | OPEN |
| OPS-016 | CI has no coverage gate, no real linter and no type checker | MEDIUM · P2 | DESIGN WEAKNESS | `ci.yml:31-36` — only `pyflakes`, `py_compile`, `pytest -q`; `requirements-dev.txt` has no `ruff`/`mypy`/`pytest-cov`/`bandit`/`pip-audit`; `docs/PERMISSIONS.md:89` records `pytest-cov` as "local only" | The only static analysis is pyflakes; no coverage measurement/threshold, no type checking, no dependency-vulnerability gate | The dominant historical defect class (13 undefined-name bugs hidden inside `except`) is guarded, but every other class is unguarded and coverage is not measurable in CI | Add `ruff` + `mypy`, `pytest --cov=newsbot --cov-fail-under=<baseline>`, `pip-audit -r requirements.txt`, and a SHA-pinned CodeQL action | n/a (CI change) — establish the coverage baseline first | OPEN |
| OPS-017 | CI matrix covers only Python 3.13 while the docs promise 3.12+ | LOW · P2 | DOC MISMATCH | `ci.yml:24-26` (`python-version: "3.13"`); `README.md:8,132` ("Python 3.12+"); `deploy/Dockerfile:3` and `requirements.txt:1` are 3.13 | The requirement line and the badge advertise 3.12+, but nothing exercises 3.12 | A 3.12-only incompatibility ships undetected for anyone following the README | Add a `matrix: ["3.12","3.13"]` or change the README to 3.13 | no | OPEN |
| OPS-018 | The Docker image is built in CI but never published or deployed | LOW · P3 | IMPROVEMENT OPPORTUNITY | `ci.yml:46-52` — `docker build -t newsbot-ci:${{ github.sha }} .` and nothing else | The image is tagged with the SHA and discarded; production is exclusively git + systemd | The Dockerfile is effectively unexercised in the real deploy chain; no artefact provenance for what actually runs | Publish to GHCR with digest pinning, or drop the CI job and state that Docker is unsupported | no | OPEN |
| OPS-019 | Dependencies are version-pinned but not hash-pinned; CI upgrades `pip` unpinned | MEDIUM · P2 | DESIGN WEAKNESS | `requirements.txt:1-2`; `ci.yml:28-30` (`pip install --upgrade pip`); `deploy.sh:78` | `==` pins fix versions but not *artifacts*; without `--require-hashes` a re-pushed wheel at the same version installs silently, and the VPS venv is not reproducible because `pip install -r requirements.txt` upgrades unlisted transitive dependencies | Supply-chain blast radius equals the production host | `pip-compile --generate-hashes` + `pip install --require-hashes`; pin `pip` itself | yes — a CI step asserting every requirement line has a `--hash=` entry | OPEN |
| OPS-020 | Docker `HEALTHCHECK` runs `run.py --check`, which never exercises the real path | LOW · P3 | DESIGN WEAKNESS | `deploy/Dockerfile:29-30`; `newsbot/app.py:126-139` (no Telegram call in check mode) | The health signal proves only "config parses and the DB file is readable" — not that the bot can authenticate, poll or write; `run.py --check` also exits non-zero without `BOT_TOKEN`, so a container without `--env-file` is permanently unhealthy | An orchestrator would keep a container that is up but functionally dead | Add a liveness command that asserts the `heartbeat` row age (mirroring `newsbot/ui_views_super.py:50`) | no (Docker-level) | OPEN |
| OPS-021 | The systemd hardening profile omits resource limits and several confinement directives | LOW · P2 | IMPROVEMENT OPPORTUNITY | `docs/DEPLOY_RUNBOOK.md:180-199` — present: `NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=strict`, `ProtectHome`, `ReadWritePaths`, `TimeoutStopSec=60`, `TimeoutStartSec=180`, journal output. Absent: `LimitNOFILE`, `MemoryMax`, `MemoryHigh`, `OOMPolicy`, `TasksMax`, `RestrictAddressFamilies`, `StateDirectory` | A runaway process is not bounded by cgroups; `OOMPolicy` defaults to `stop` + `Restart=always`, so an OOM kill becomes an automatic restart loop indistinguishable from a normal exit | A runaway AI-retry leak can take the host down | Add `MemoryMax=1G`, `MemoryHigh=768M`, `OOMPolicy=stop`, `LimitNOFILE=8192`, `TasksMax=256`, `RestrictAddressFamilies=...`, `StateDirectory=`; document that `DB_FILE` must live under `ReadWritePaths` | no (config) | OPEN |
| OPS-022 | `Restart=always` + `StartLimitIntervalSec=0` gives an unbounded restart loop with no alert | INFO · P3 | DESIGN WEAKNESS | `deploy/systemd/10-restart-always.conf:2,5-6`; `docs/DEPLOY_RUNBOOK.md:249-253` | The intent (never stay down) is documented and was introduced to fix a real incident (`DEPLOY_RUNBOOK.md:224-225`: the service was abandoned after 31 120 restarts); the residual risk is that a permanently-failing build restarts every 5 s forever, signalled only by a GitHub Actions email | Log churn, CPU/IO noise, possible Telegram rate-limit impact, no page | Keep the design but add a circuit breaker (`StartLimitIntervalSec=300` + `StartLimitBurst=20` + an `OnFailure=` alert unit) and make the health gate treat sustained `NRestarts > 0` as a hard failure | no | OPEN |
| OPS-023 | No CODEOWNERS, CHANGELOG, LICENSE, PR template or Dependabot configuration | MEDIUM · P2 | DESIGN WEAKNESS | `git ls-files` shows no `CODEOWNERS`/`CHANGELOG`/`CONTRIBUTING`/`LICENSE`/templates; `.github/` contains only the two workflows | No ownership metadata for deploy-critical paths, no release changelog, no dependency-update automation | The two workflows that can mutate production are unprotected by any in-repo convention; dependency CVEs are only re-checked when a human remembers `pip-audit` | Add `.github/CODEOWNERS` (`.github/workflows/`, `deploy/`, `tools/`, `newsbot/db.py`, `newsbot/config.py`), `.github/dependabot.yml` (pip + github-actions), `CHANGELOG.md`, and a PR template | no | OPEN |
| OPS-024 | Documentation is not consistently updated in the same commit as the code it describes | LOW · P3 | DOC MISMATCH | `71bc3c2` changed `newsbot/config.py` (referral default 20 %) and only two commits later `ee735bd` updated `.env.example` and `da8fbc5` updated `README.md`; `fbe5f94` (phase 15) and `39a48de` (phase 14) *did* update docs in the same commit | The convention holds for phases 14-15 but not for phase 13 or the recent referral-default change | Short windows where `README.md`/`.env.example`/config disagree; the repo already carries two such residuals | Add a CI check failing when `newsbot/config.py` changes without a matching `.env.example`/`README.md` change; extend `tests/test_phase12_config_docs.py::test_env_example_is_in_sync_with_config` to cover numeric defaults, not only key names | yes — extend the existing test | OPEN |
| OPS-025 | Re-running a deploy for the SHA already on disk reports success without backup or health check | LOW · P2 | DESIGN WEAKNESS | `deploy/deploy.sh:141-144` — `if [ "$prev" = "$target" ]; then log "already at $target"; exit 0; fi` (before `backup_db` at `:146`) | The early exit happens **before** the backup and before any health check, and exits 0 | A green deploy run is not evidence the service is healthy; a retry after a partial failure silently succeeds without repair | On the "already at target" path still run `backup_db` and the health gate, and exit non-zero if health fails | yes — deploy the same SHA twice in the sandbox and assert the second run performs a backup and health check | **VERIFIED_FIXED (PH-04)** |
| OPS-027 | `workflow_dispatch` can only ever deploy SHAs reachable from `origin/main` | LOW · P2 | DESIGN WEAKNESS | `deploy.sh:67-69` — `git fetch -q origin main` / `git cat-file -e "${sha}^{commit}"` / `git reset -q --hard "$sha"` | The manual path lets the operator pick any ref, but `sync_to` fetches only `origin main`; a SHA not an ancestor of `origin/main` is not in the VPS object store, so the deploy aborts with "code sync to `<sha>` failed" | The manual escape hatch is narrower than the workflow interface implies; a hot-fix from a release branch fails opaquely | `git fetch -q origin "$sha"` (or fetch all heads) and/or add a `ref` input to `workflow_dispatch` | no (the sandbox origin is a bare repo on `main`; adding a branch is straightforward) | OPEN |
| OPS-029 | Two mutually inconsistent procedures for producing the SSH host fingerprint | MEDIUM · P2 | DOC MISMATCH | `docs/DEPLOY_RUNBOOK.md:31` says to take it from `ssh-keyscan -t ed25519 <host>`; `docs/SECURITY.md:40` says to take it from `ssh-keygen -lf` of the host public key | `ssh-keyscan` fetches the key **from the network** — trivially MITM-able and the wrong tool for establishing trust on a first connection; `ssh-keygen -lf` over the already out-of-band-installed key is correct | The runbook steers operators to the weaker method, defeating fingerprint pinning | Make the `ssh-keygen -lf` method the single documented procedure; keep `ssh-keyscan` only as a *verification* step | no | OPEN |
| OPS-030 | Deploy success is never reconciled against the SHA actually running | LOW · P3 | IMPROVEMENT OPPORTUNITY | `deploy.sh:152` writes `$APP_DIR/.deployed_sha` (never read back by the app); `newsbot/app.py:72-73` records no SHA; the only SHA of record is the ad-hoc `git rev-parse --short HEAD` in `newsbot/ui_views_super.py` | `.deployed_sha` is an operator-facing claim, not an assertion by the running process | Drift between "what was deployed" and "what is running" is only detectable manually | Have the app record its resolved commit at `post_init` and have `deploy.sh` compare it to `$TARGET_SHA` after the health check, failing on mismatch | yes — assert `post_init` writes the resolved SHA and `deploy.sh` compares it | **FIXED_UNVERIFIED (PH-04)** |
| OPS-031 | CI runs the test collector twice (nested `pytest` inside `final_verification.py`) | INFO · P3 | IMPROVEMENT OPPORTUNITY | `ci.yml:35-38` runs `pytest -q` and then `python tools/final_verification.py`; `tools/final_verification.py:29-33` re-runs `pytest -q --collect-only` in a subprocess | Doubles collection cost and writes a second `.pytest_cache`; the check ("more than zero tests are collected") is strictly weaker than the `pytest -q` step immediately before it | Minor CI waste in a 15-minute job | Replace the nested collection with a cheap static count, or drop the check now that `pytest -q` gates the job | no | OPEN |

### Documented-vs-actual mismatches (documentation register)

| doc file:line | claimed | actual | severity |
|---|---|---|---|
| `README.md:449`, `:457-458` | install path `/opt/bot-ai-automatic`, unit `bot-ai-automatic` | the known-intended target is `/opt/bot-official-telegram` + `newsbot.service`; neither string appears anywhere in the repository | HIGH (OPS-001) |
| `DEPLOY-VPS-UPDATE.md:6` | `/opt/bot-ai-automatic` + service `bot-ai-automatic` | same | HIGH (OPS-001) |
| `docs/DEPLOY_RUNBOOK.md:3-4` | "this document describes the *reality of the code*" | the paths belong to another project | HIGH (OPS-001) |
| `docs/DEPLOY_RUNBOOK.md:171-203` | "Fresh install — full unit" `/etc/systemd/system/bot-ai-automatic.service` | no unit file ships in the repository (only two drop-ins) | HIGH (OPS-001) |
| `DEPLOY-VPS-UPDATE.md:53-58` | "create these **two** secrets" | `deploy-vps.yml:37,42` also hard-requires `VPS_HOST_FINGERPRINT`; a missing one aborts every deploy at preflight | HIGH (OPS-008) |
| `DEPLOY-VPS-UPDATE.md:18`, `:73`, `:186` | raw `cp newsbot.db /opt/backups/...` | `docs/DEPLOY_RUNBOOK.md:74-75` and `deploy.sh:38-52` forbid raw `cp` of a live WAL database | HIGH (OPS-011) |
| `DEPLOY-VPS-UPDATE.md:122` | restore via `cp /opt/backups/....db newsbot.db` | the DB is WAL-mode; the procedure never removes stale `-wal`/`-shm` | HIGH (OPS-011) |
| `docs/DEPLOY_RUNBOOK.md:31` | fingerprint via `ssh-keyscan` | `docs/SECURITY.md:40` says `ssh-keygen -lf` | MEDIUM (OPS-029) |
| `docs/DEPLOY_RUNBOOK.md:117` | "`SCHEMA_VERSION=4`" | `newsbot/__init__.py:9` -> `SCHEMA_VERSION = 7` (the runbook's own section 10 says v7) | MEDIUM (DOC) |
| `docs/DEPLOY_RUNBOOK.md:124-125` | the archive folder "must also be included in VPS backups" | `deploy.sh:39-62` backs up only `newsbot.db` | MEDIUM (OPS-010) |
| `docs/DEPLOY_RUNBOOK.md:231` | `./deploy/deploy.sh deploy <MAIN_SHA>` | the file is committed mode `100644` | MEDIUM (OPS-028) |
| `DEPLOY-VPS-UPDATE.md:145` | "must pass **161** tests" | 455 collected | MEDIUM (TEST-001/002) |
| `docs/PERMISSIONS.md:91-92,97` | "295 green tests", "53 % coverage" | 455 collected; the 295/53 % figures are a phase-6 snapshot | MEDIUM (TEST-001) |
| `docs/SECURITY.md:20,58-62` | "`pip-audit` … No known vulnerabilities found" | 31 packages match, but there is **no** dependency-audit step in CI | MEDIUM (OPS-016) |
| `DEPLOY-VPS-UPDATE.md:31-35` | deploy does "backup -> reset -> pip -> `--check` -> restart + `is-active`" | actual: WAL-safe backup -> `reset --hard <SHA>` -> pip -> `compileall` + `--check` -> restart -> health (`is-active` + `NRestarts` + journal grep) -> `.deployed_sha` markers -> **auto-rollback** | MEDIUM (DOC) |
| `DEPLOY-VPS-UPDATE.md:88-89` | manual health = `sleep 3` + `is-active` | `deploy.sh:96-121` is materially stronger; the manual path (and `README.md:456-458`) is the weaker one | MEDIUM (DOC) |
| `README.md:429-437` | the automatic path does backup -> pull -> `--check` -> restart | it never mentions the systemd drop-ins, and `deploy.sh` never installs them | MEDIUM (OPS-001) |
| `DEPLOY-VPS-UPDATE.md:204-208` | troubleshooting table | no entry for the `SystemExit` raised when the DB schema is newer than the build | MEDIUM (OPS-012) |
| `README.md:363` | `test_security.py` "49 tests" | 20 functions -> **73** collected | LOW (TEST-001) |
| `README.md:8`, `:132` | "Python **3.12+**" | CI/Docker/requirements are pinned to **3.13** | LOW (OPS-017) |
| `README.md:9` | badge "python-telegram-bot **21.6+**" | `requirements.txt:26` -> `python-telegram-bot==22.8` | LOW (DOC) |
| `docs/PERFORMANCE.md:3` | "Schema: **v6**" | `SCHEMA_VERSION = 7` | LOW (PERF-004) |
| `docs/PERMISSIONS.md:3`, `docs/SECURITY.md:3` | branch `phase-6-authz` / `phase-3-secrets-logs-injection`, "ref ROADMAP.md" | neither branch exists and **ROADMAP.md is not in the repository** (also `docs/SECURITY.md:78`, `newsbot/ratelimit.py:20`, `tests/test_authz_matrix.py:4`) | LOW (DOC) |
| `.env.example:30-31` vs `:42-43` | `MAX_MEDIA_MB`, `MAX_PAGE_MB` declared twice | cosmetic duplication | LOW (DOC) |
| `.env.example:18` vs `:24` | `CF_ACCOUNT_ID` declared twice | cosmetic duplication | LOW (DOC) |
| `README.md:12`, `:353` | "454 tests passing" | 455 collected — **accurate** | INFO |
| `README.md:355`, `DEPLOY-VPS-UPDATE.md:146` | `final_verification.py` "17 checks" | exactly 17 `check(...)` calls — **accurate** | INFO |
| `README.md:507` | "pytest + pyflakes + `run.py --check` must be green before PR" | CI runs all three (plus `py_compile` and `final_verification.py`) — **accurate** | INFO |
| `README.md:515` | "this repository currently has no license file" | no LICENSE file — **accurate** | INFO |

**Environment-variable reconciliation (the cleanest area of the repository):** all **62** documented defaults in `.env.example` match `newsbot/config.py`; **zero** undocumented-but-used keys (except two intentionally-legacy aliases: `TELEGRAM_BOT_TOKEN` at `newsbot/config.py:67` and `CF_KV_ID` at `:137`); **zero** documented-but-unused keys (`REFERRAL_MAX_PER_USER` is read but deliberately inert — see FIN-023). This is enforced by `tests/test_phase12_config_docs.py:252-258`, which CI runs.

---

# 10. TESTING & QA (`TEST-001 … TEST-014`)

**Executed in this audit:** `python -m pytest -q -p no:cacheprovider` on Windows 11 / Python 3.13.14, out-of-tree venv with the pinned dependencies (nothing installed) -> **455 passed, 0 failed, 0 skipped, 0 errors** in 112.5 s. Re-run in reverse alphabetical file order -> **455 passed** (no order sensitivity). The repository was left clean (`git status --porcelain` empty; no `newsbot.db` created in the repo root; the temp DB lives under `%TEMP%`). `tests/test_deploy_script.py` ran (Git Bash present) — on a runner without Git Bash those 4 tests skip, so CI coverage differs.

## TEST-005 — The wallet ledger invariant `SUM(credit_log.delta) == users.credit` is never asserted
- **Severity / Priority / Category:** HIGH · P1 · MISSING TEST / UNKNOWN BEHAVIOR
- **Evidence:** `newsbot/db.py:47` defines `credit_log(id, user_id, delta, balance_after, reason, ref, ts)`; it is written at `newsbot/core_plans.py:231` and `newsbot/db.py:89-92`. **No test** compares the aggregate to `users.credit`; `tests/test_phase13_tech_debt.py:138-140` asserts only **one** receipt's `balance_after`.
- **Actual vs Expected:** Actual — the two write paths are *different code* (a SQL trigger vs. a Python function), and the trigger's `balance_after` is computed by a **sub-SELECT** while the Python path computes it **in Python** (`newsbot/core_plans.py:225-229`). A divergence (rounding, the `MAX(0,…)` clamp, a missed path) would silently desynchronise the ledger from the wallet. Expected — an invariant test over N users x M movements.
- **Impact:** exactly the class of bug that erodes trust in the credit balance; FIN-001/FIN-002/FIN-006 are all invisible to CI.
- **Fix:** add a property-style test: perform N randomised wallet movements (referral trigger + plan change + zero-delta + cap-partial) for M users, then assert `SELECT COALESCE(SUM(delta),0) FROM credit_log WHERE user_id=? == users.credit` for every user and that `balance_after` is monotone.
- **Regression test required:** yes.
- **Status:** **VERIFIED_FIXED (2026-10-20, PH-01)** — تستِ تسویهٔ دفترِ کیف پول اضافه شد (جمعِ رسیدها == موجودی، و ``balance_after`` == مقدارِ ذخیره‌شده).

## TEST-006 — The D1 business payment path `_pay_approve_d1` has zero test coverage
- **Severity / Priority / Category:** HIGH · P1 · MISSING TEST / UNKNOWN BEHAVIOR
- **Evidence:** `newsbot/core_payments.py:113-134`; `DB_BACKEND == "d1"` appears in only 3 test files and always against a fake client (`tests/test_trx_atomicity.py`, `tests/test_phase1_fixes.py`, `tests/test_phase10_db_quality.py`); `_pay_approve_d1` is never entered.
- **Actual vs Expected:** Actual — the D1 path is *materially different* from the SQLite path the tests exercise (the SQLite path is one `BEGIN IMMEDIATE` transaction; the D1 path is a sequence of independent auto-commit statements with manual compensation). The compensation logic (`disc_refund` on failure, `pay_set("pending")`), the `applied_at` marker and the `PayDiscountExhausted` reset are all untested. Expected — parity tests for the non-atomic CAS sequence.
- **Impact:** FIN-011's double-debit and double-apply windows are invisible to CI.
- **Fix:** drive `_pay_approve_d1` with the existing fake-D1 client; assert exactly-one-apply under two concurrent approvers, discount `used` restored on failure, and `applied_at` preventing re-apply after a simulated crash.
- **Regression test required:** yes.
- **Status:** OPEN

### TEST-001 … TEST-004, TEST-007 … TEST-014 — remaining testing findings

| ID | Title | Sev/Pri | Category | Evidence | Actual vs Expected | Impact | Fix | Test | Status |
|---|---|---|---|---|---|---|---|---|---|
| TEST-001 | README test-count claims are inaccurate | LOW · P3 | DOCUMENTATION MISMATCH | `README.md:353` ("454 tests"), `:363` (`test_security.py` "49 tests") | Observed 455 collected; `test_security.py` -> 73 | The operator-facing quality contract under-reports the total by 1 and the security file by 24 (46 %) | Regenerate the counts from `pytest --collect-only -q`; add a CI doc-lint | yes (a doc-lint) | OPEN |
| TEST-002 | `PHASE1-10-FINAL-REPORT.md` test counts are stale (161 vs 455) | LOW · P3 | DOCUMENTATION MISMATCH | `PHASE1-10-FINAL-REPORT.md:13,23` | The report self-documents an older HEAD (`eb4753b`) | A historical report reads as current truth | Add a "superseded by" banner or archive it | no | OPEN |
| TEST-003 | No pytest configuration exists anywhere in the repository | MEDIUM · P2 | DESIGN WEAKNESS | no `pytest.ini`/`pyproject.toml`/`setup.cfg`/`tox.ini`; `requirements-dev.txt` pins only `pytest` (no `pytest-timeout`, no `pytest-cov`) | pytest runs with 100 % defaults | No per-test timeout (a hung test burns the 15-minute job budget); no coverage measurement is possible at all — the root cause of several gaps above; no marker strictness | Add `pytest.ini` with `testpaths`, `addopts = -ra --strict-markers --durations=10`, `pytest-timeout` and `pytest-cov` | yes (a CI step) | OPEN |
| TEST-004 | ~22 of 376 tests assert source text / repo state instead of behaviour | MEDIUM · P2 | DESIGN WEAKNESS | `tests/test_payment_atomicity.py:213-232`; `tests/test_phase1_fixes.py:77-86`; `tests/test_phase12_config_docs.py:207,214,223,239,252,261`; `tests/test_phase13_tech_debt.py:168,283`; `tests/test_phase14_payment_ui.py:149,158`; `tests/test_cancel_and_ref.py:215,225`; `tests/test_injection.py:47,58`; `tests/test_data_retention.py:62`; `tests/test_static_guard.py:71,96` | ~6 % of the suite asserts on the *text* of the implementation | These tests pass when the code is behaviourally broken but the string is still present, and fail on harmless refactors; they give a false sense of coverage on exactly the money path | Convert each to a behavioural test; keep only the genuinely static guards (`pyflakes`, `.gitignore`) | yes (a meta-test) | OPEN |
| TEST-007 | Source failure cooldown / retry / backoff is completely untested | MEDIUM · P2 | MISSING TEST / UNKNOWN BEHAVIOR | `newsbot/core_sources.py:55-61` (`source_fail`, exponential `next_try_after`); `newsbot/cycle.py:143` (the `paused` branch) — `fail_count`/`next_try_after`/`SOURCE_PAUSE_MIN` appear in **no** test file | Monotonic backoff, the 12 h cap, auto-disable after `SOURCE_FAIL_LIMIT`, and the `source_ok` reset are all unverified | A dead source is re-fetched every cycle (hammering a failing endpoint), or a healthy source is permanently disabled after a transient blip — both silent | Add tests for the backoff sequence, `fail_count`, `source_ok` reset, the `paused` branch, and the auto-disable threshold | yes | OPEN |
| TEST-008 | The daily **test-run** quota is never driven to its cap | MEDIUM · P2 | MISSING TEST / UNKNOWN BEHAVIOR | `newsbot/core_users.py:150-157`; `newsbot/ui_views_admin.py:305`; `newsbot/cycle.py:128-129` — `daily_tests` appears in tests only as a plan field | The AI quota and post quota are both proven; the test quota (a paid entitlement that costs the provider money per run) is not | A regression that removed the `tests` cap would go unnoticed | Add a cap-boundary test mirroring `tests/test_phase11_limits_boundaries.py:68`, plus a `usage_reset(uid,"tests")` test | yes | OPEN |
| TEST-009 | Dead/mis-routed callback buttons cannot be detected by the suite | MEDIUM · P2 | MISSING TEST / UNKNOWN BEHAVIOR | `newsbot/ui_dispatch.py:383` — unknown callback data silently redirects to the home screen; `tests/test_ui_routing.py:185` exercises only a hard-coded whitelist of 8 super + 4 admin actions | A typo'd or orphaned button produces a confusing home redirect with no error and no failing test | The classic "dead button" class is invisible to CI | Build a registry of emitted `callback_data` and assert each resolves in `dispatch()`; add `log.warning("unrouted callback: %s", data)` at the fall-through | yes | OPEN |
| TEST-010 | i18n fa/en parity is only structurally checked, not semantically | MEDIUM · P2 | MISSING TEST / UNKNOWN BEHAVIOR | `tests/test_phase14_payment_ui.py:104-114` (2-tuples with non-empty strings); `tests/test_phase12_config_docs.py:230-234` (12 named keys) | No test asserts that the `en` value contains Latin characters, that fa/en carry the same `{placeholder}` set, the `tr()` fallback, or the DB-overridden texts | A Persian string pasted into the `en` slot (or a `{n}` placeholder present only in `fa`) renders broken in production while the suite stays green | Extend the bilingual test to assert equal placeholder sets for every `TXT`/`DIAG`/`STAGES` entry and apply the Latin-script heuristic to `en` | yes | OPEN |
| TEST-011 | Global state is mutated without restore (blocks `pytest-xdist`) | LOW · P2 | DESIGN WEAKNESS | `tests/test_user_features.py:16-23` (zeroes `MANDATORY_CHANNEL_ID`/`USERNAME` and never restores them); `tests/test_phase12_config_docs.py:82-85` (`importlib.reload(newsbot.config)` mid-session) | Global mutations are not restored | Benign today (the suite passes in both orders), but it destroys any environment-provided `MANDATORY_CHANNEL_ID` and is a hard blocker for `pytest -n auto` | Use `monkeypatch.setattr`; load `config` into a throwaway module object instead of reloading the shared one | no (mechanical) | OPEN |
| TEST-012 | Wall-clock / sleep / DST-dependent tests are a flakiness risk | LOW · P3 | DESIGN WEAKNESS | `tests/test_cancel_and_ref.py:201` (`time.sleep(1.05)`), `:88,91`; `tests/test_phase8_concurrency.py:61-69` (asserts `ticks >= 10` under a 10 ms ticker), `:278-285`; `tests/test_phase15_watchdog.py:90`; `tests/test_time_zone.py:55,82`; `tests/test_core.py:131`; `tests/test_phase10_db_quality.py:189`; `tests/test_exactly_once_limits.py:192-203` | Real wall-clock instead of an injected clock | On a loaded CI runner or a run spanning local midnight these fail intermittently and are misattributed to product bugs; `test_phase8_concurrency.py:69` is the most exposed | Inject a clock fixture; relax the tick-count assertion to "the loop was not blocked" | no | OPEN |
| TEST-013 | `conftest.py` shares one DB for the entire session and leaks the temp directory | MEDIUM · P2 | DESIGN WEAKNESS | `tests/conftest.py:10-11` — `tempfile.mkdtemp(prefix="newsbot-tests-")`, no teardown; all 455 tests share one database | Every test sees every other test's rows: cross-test coupling is invisible, and a test can pass only because of another test's leftovers (e.g. `users.id == 1`, fixed discount codes such as `"SEC7A"`, `"CRASH10"`) | The temp directory (containing the DB and, before encryption, plaintext API keys) leaks on every CI run | Use a `tmp_path`-backed DB per test module (or a transaction rollback); register an `atexit`/fixture finaliser to `shutil.rmtree` | no | OPEN |
| TEST-014 | README asserts "no empty `except` hides an error"; the code has 59 | MEDIUM · P2 | DOCUMENTATION MISMATCH | `README.md:376`; `grep 'except\s+[^:]*:\s*(pass|continue)\s*$' newsbot/*.py` -> **59** occurrences across 21 modules (e.g. `newsbot/ui_dispatch.py:432,436,467,484`; `newsbot/ui_widgets.py:308,565`; `newsbot/notify.py:85`; `newsbot/db.py:321,349,354,413,421`; `newsbot/core_articles.py:289,291`; `newsbot/cycle.py:111`) | The claim is false — and the silent `except` is exactly the mechanism that hid the undefined-name bugs the project was built to eliminate (`PHASE1-10-FINAL-REPORT.md:31,47`) | The repo's headline quality guarantee is inaccurate; a reviewer who trusts it will not look | Soften the claim to "no `except` hides a *business-critical* failure" and add a CI lint that whitelists the 59 benign sites; or convert the meaningful ones (see OBS-005/OBS-013) to `log.exception` | yes (a lint that fails on new un-annotated `except: pass`) | OPEN |

---

# 11. OBSERVABILITY & OPERATIONS (`OBS-001 … OBS-016`)

## OBS-002 — `/health` is a process-stats card, not a readiness probe
- **Severity / Priority / Category:** HIGH · P1 · DESIGN WEAKNESS
- **Evidence:** `newsbot/ui_views_super.py:36-81` (`view_health`), `:83-85` (`cmd_health`); `newsbot/ui_views_super.py:44-45` — `try: pending = q("SELECT COUNT(*) c FROM pub_journal", one=True)["c"] except Exception: pending = 0`.
- **Actual vs Expected:** Actual — reported: heartbeat age, tick interval, cycle concurrency, channel count, ready-queue count, pending journal count, AI model health, DB size, RSS, `PRAGMA quick_check`, recovery interval, git SHA. **Missing:** (a) Telegram Bot API reachability; (b) the last successful **publish** timestamp; (c) per-channel queue depth; (d) stuck/failed publications (`articles.status='failed'`, `send_fails`); (e) in-flight publication tasks / stuck locks; (f) pending payment approvals; (g) source health (`next_try_after`, paused sources); (h) AI token/cost; (i) on the D1 backend `config.DB_FILE` does not exist so the size always reports 0.0 MB; (j) no "degraded" state — a broken DB renders `pending = 0` and an `error TypeError` string. Expected — readiness (can it publish now?) + liveness + last-known-good.
- **Impact:** during a Telegram outage or a source-wide failure `/health` still shows "OK" (the heartbeat is fresh because the scheduler ticks fine even when every cycle fails). The `pending = 0` fallback is actively misleading — a DB read error is indistinguishable from "no stuck publications".
- **Fix:** split into `/health` (readiness) and `/metrics` (stats); make every probe tri-state (`ok`/`degraded`/`unknown`); skip the file-size probe on D1.
- **Regression test required:** yes.
- **Status:** OPEN

## OBS-004 — The scheduler/automation loop logs errors without a traceback
- **Severity / Priority / Category:** HIGH · P1 · DESIGN WEAKNESS
- **Evidence:** `newsbot/scheduler.py:69,86,88,90,103,128` and `newsbot/app.py:46` — `except Exception as e: log_event("ERROR", f"cycle {ch['title']}: {e}", uid)`; `log_event` (`newsbot/logutils.py:87-90`) renders without `exc_info`. Contrast `newsbot/publish.py:357` and `newsbot/ui_views_super.py:273`, which use `log.exception`.
- **Actual vs Expected:** Actual — only `str(e)` (often a bare `NameError`/`AttributeError` message with no location). Expected — a full traceback.
- **Impact:** reproducing a scheduler failure offline is guesswork. This is precisely the failure class the project's own post-mortem describes ("the names were hidden inside `except` and the error was silent", `PHASE1-10-FINAL-REPORT.md:31`). The scheduler is the single most important failure surface and it is the one that throws the traceback away.
- **Fix:** add `exc_info=True` support to `log_event` and pass it at all 7 scheduler/app sites.
- **Regression test required:** yes (assert the traceback text reaches the log record).
- **Status:** OPEN

## OBS-005 — A referral credit failure after a successful payment is logged and then lost forever
- **Severity / Priority / Category:** HIGH · P1 · DESIGN WEAKNESS
- **Evidence:** `newsbot/ui_views_super.py:275-287` — the `try/except Exception: log.exception("referral credit failed rid=%s", rid)` around `credit_referral_on_payment(...)`; the function is idempotent **by `ref_key`** (`newsbot/core_referrals.py:84-139`, `pay:<rid>` stored in `settings`), but nothing ever retries it: there is no outbox, no retry queue and no pending flag.
- **Actual vs Expected:** Actual — the referrer is permanently short-changed after a **successful** customer payment; the log line is the only trace. Expected — the credit is guaranteed or queued for retry.
- **Impact:** money loss for the referrer, discovered only by complaint. The idempotency key exists precisely to make a retry safe, so the retry loop was designed for and never built.
- **Fix:** write `settings['refcredit:pending:<rid>'] = 1` on failure and let `scheduler._recover_state()` retry it (safe thanks to `ref_key` idempotency); surface the pending count in `/health`.
- **Regression test required:** yes — simulate a transient failure; assert the referral is credited after recovery and only once.
- **Status:** OPEN

## OBS-007 — The financial audit trail is incomplete and self-destructing
- **Severity / Priority / Category:** HIGH · P1 · DESIGN WEAKNESS
- **Evidence:** `newsbot/core_articles.py:286` — `DELETE FROM pay_requests WHERE status!='pending' AND COALESCE(decided_at, created_at) < ?-90d`; `newsbot/db.py:45` — `pay_requests` has **no `decided_by`** column; `newsbot/db.py:89-92` — the referral receipt's `ref` is the *referred user id*; `newsbot/core_plans.py:231-232` — the plan-change receipt's `ref` is the *plan id*; neither is the `pay_requests.id`.
- **Actual vs Expected:** Actual — (a) *who* approved a payment cannot be answered; (b) `final_price` is free text with no currency column (the system is dual-currency: `price_num_fa` Toman / `price_num` USD); (c) no wallet movement can be joined to the payment that caused it; (d) approved/rejected payments are deleted after 90 days while `credit_log`/`plan_changes` are kept, producing dangling references; (e) the receipt image pointer becomes unreachable if the chat is purged; (f) the rejection reason is never recorded. Expected — an operator can answer "who paid what, when, and what was credited" from the app.
- **Impact:** the three tables together cannot be reconciled into a per-transaction money story. Disputes ("I paid and got nothing") are unanswerable; the 90-day purge destroys the primary document while keeping the derived ledger.
- **Fix:** add `pay_requests.decided_by`, `pay_requests.currency`, `pay_requests.paid_amount_num`; make `credit_log.ref` a stable external id (`pay:<rid>`); align the retention of `pay_requests` with the ledger; add a "money ledger" view to the super-admin panel.
- **Regression test required:** yes (with TEST-005).
- **Status:** **FIXED_UNVERIFIED (2026-10-20, PH-01)** — بخشِ «اتصالِ حرکتِ کیف پول به پرداخت» رفع شد (``credit_log.ref``/``plan_changes.ref`` = ``pay:<id>`` و ``pay_requests.decided_by``/``cur``)؛ **هم‌ترازیِ نگه‌داریِ داده (۹۰ روز) هنوز باقی است** و به PH-09 موکول شد.

## OBS-011 — `notify_super` failures are logged and dropped; there is no delivery guarantee
- **Severity / Priority / Category:** HIGH · P1 · DESIGN WEAKNESS
- **Evidence:** `newsbot/notify.py:83-86` — `except Exception as e: log.warning(f"notify super: {e}")`; `newsbot/notify.py:40-54` retries with delays `(1.0, 3.0, 8.0)`, then `log.error(...)` and `return False` — **the return value is discarded** by the caller (`newsbot/notify.py:62`). `newsbot/notify.py:15-16` — `SUPER_QUEUE_MAX = 100`, and on `QueueFull` the message is **dropped** with a warning.
- **Actual vs Expected:** Actual — the *only* alerting channel of the whole system can fail silently, and it fails hardest exactly when the system is already broken (a Telegram API outage disables both the bot's function and its alerting path). Expected — durable, escalating delivery.
- **Impact:** a classic monitoring dead-end; the operator can be told nothing at the moment they most need to know.
- **Fix:** persist undelivered super-notifications to a `notifications` table with `attempts`/`next_attempt_at`/`delivered_at`; retry from the scheduler; expose `undelivered_count` in `/health`; fall back to the journal at ERROR level if Telegram itself is down.
- **Regression test required:** yes — queue overflow must land in a dead-letter table, not vanish.
- **Status:** OPEN

## OBS-013 — If the receipt notification to super-admins fails, the payment request is orphaned
- **Severity / Priority / Category:** HIGH · P1 · DESIGN WEAKNESS
- **Evidence:** `newsbot/ui_inputs.py:448-461` — `pay_create(...)` commits the row **before** the notification loop; the loop catches per-super-admin failures and only logs (`log.warning(f"pay notify {sid}: {e}")`); there is no retry and no "unnotified payment" marker.
- **Actual vs Expected:** Actual — a `pending` `pay_requests` row exists that **no super-admin ever saw**. Expected — durable notification, or a sweeper that re-alerts pending payments.
- **Impact:** direct revenue loss: the customer uploaded a receipt and paid, the plan is never granted because nobody approves it, and the only evidence is a log line. The super-admin payments list (`view_s_pays`) does surface pending rows, but only if an operator happens to open that screen — there is no push. Combined with OBS-011 (the same path is lossy), this is the highest-value money-observability gap. It compounds UI-006 (caption truncation) and UI-009 (form loss).
- **Fix:** add `notified_at`/`notify_attempts` on `pay_requests`; re-notify un-notified pending rows from `scheduler_tick`; alert if any is older than N minutes; expose the count in `/health`.
- **Regression test required:** yes.
- **Status:** OPEN

### OBS-001, OBS-003, OBS-006 … OBS-010, OBS-012, OBS-014 … OBS-016 — remaining observability findings

| ID | Title | Sev/Pri | Category | Evidence | Actual vs Expected | Impact | Fix | Test | Status |
|---|---|---|---|---|---|---|---|---|---|
| OBS-001 | No metric export, no external alerting, and operational alerts can be silently dropped | HIGH · P1 | DESIGN WEAKNESS | `newsbot/notify.py:15-16,79-82` (`SUPER_QUEUE_MAX = 100`, drop on overflow with a warning); `grep 'prometheus\|statsd\|sentry\|datadog\|opentelemetry\|grafana\|webhook'` -> **no matches**; the only "metrics" are an in-memory dict snapshotted to `settings` (`newsbot/logutils.py:69-85`) and rendered as a Telegram text block | There is no way to answer "is the bot actually publishing?" without opening the bot in Telegram. No SLO, no error-rate, no latency, no cost metric, and the alert channel itself is lossy (an in-memory queue with no persistence and no dead-letter) | Operational blindness | Export the counters in Prometheus text format on a local `/metrics` endpoint (or a `metrics` table); persist the super-notification queue with a dead-letter state | yes | OPEN |
| OBS-003 | Logging is free text; plain `log.*` calls carry no correlation id | MEDIUM · P2 | DESIGN WEAKNESS | `newsbot/logutils.py:11` — `logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s")`; `:87-90` — `log_event` attaches the context; 96 direct `log.*` calls in `newsbot/` emit the message with no `op`/`channel`/`stage` prefix | No `extra=`/structured-JSON formatter; the correlation context is attached only by `log_event` | Operators cannot grep or aggregate; "why did this cycle fail?" requires reading Persian prose; no machine-parseable `stage=`/`error_class=` field exists | Add a `Formatter` that renders the ContextVar fields on every line and route the 96 direct calls through `log_event` | yes | OPEN |
| OBS-006 | No dead-man's switch for *business* liveness; systemd covers process liveness only | MEDIUM · P2 | DESIGN WEAKNESS | `newsbot/watchdog.py:51-57` pings systemd; `newsbot/scheduler.py:80` writes the `heartbeat`; `newsbot/ui_views_super.py:49` — `healthy = 0 <= hb_age < config.TICK_SECONDS * 4` | The worst operational outcome — the bot is up, ticking, and publishing nothing — is indistinguishable from normal operation. `automation_enabled` can also be toggled off with no alert | A silent total outage | Add a `last_successful_publish` setting written by `publish_article`; expose it in `/health`; notify super admins when it is stale while automation is enabled | yes | OPEN |
| OBS-008 | No durable log sink; stdout-only with no rotation and no JSON output | MEDIUM · P2 | DESIGN WEAKNESS | `newsbot/logutils.py:11`; no `FileHandler`, `RotatingFileHandler` or syslog handler anywhere; `httpx` is pinned to WARNING (`:12`) but `httpcore`, `telegram` and `apscheduler` are not; the unit files set no `StandardOutput`/`SyslogIdentifier`/`LogRateLimitIntervalSec` | The `logging` contract is delegated entirely to the supervisor, which the repo does not own; the journal can throttle or rotate away the very lines needed to explain an incident; the log file is not in the backup (`deploy/deploy.sh` backs up the DB only) | Incident forensics is at the mercy of the host's journal settings | Add a `RotatingFileHandler` (the global redaction factory applies automatically); set `StandardOutput=journal` + `SyslogIdentifier=newsbot`; or emit JSON to stdout | no (but test that the file handler is redacted) | OPEN |
| OBS-009 | The health view converts probe failures into healthy-looking zeros | MEDIUM · P2 | DESIGN WEAKNESS | `newsbot/ui_views_super.py:44-45`, `:40` | A DB error is indistinguishable from "0 pending journals"; on D1 the DB size always reports 0.0 MB | The most important readiness number reports "0" precisely when the DB is unreadable — a false green in the costliest failure mode | Wrap every probe in a helper returning `(value, error)`; render "?" on failure; skip the size probe on D1 | yes | OPEN |
| OBS-010 | Error classes are computed but discarded before reaching the log | MEDIUM · P2 | DESIGN WEAKNESS | `newsbot/ai_models.py:173-180` (`err_code` -> `ai_busy`/`ai_auth`/`ai_timeout`), `:300` (`bump("model_fail", sub=last)`); `newsbot/cycle.py:146` collapses every source error to `str(e)[:80]` | The transient-vs-permanent distinction exists only inside the AI module and is lost in the log/UI layer; no alert rule could ever be written on it | An operator cannot tell "the provider is rate-limiting us" from "the API key is invalid" without reading prose | Thread a normalised `error_class` (`auth|rate_limit|timeout|network|content|unknown`) through `log_event` and into a persisted `reason` | yes | OPEN |
| OBS-012 | The user is never told their payment was approved if the notification fails | MEDIUM · P2 | DESIGN WEAKNESS | `newsbot/ui_widgets.py:618-625` — `except Exception as e: log.warning(f"notify user {uid}: {e}")`, called from `newsbot/ui_views_super.py:288` | The customer's "payment approved" message can be lost while the plan is granted; the `SCHED_NOTIF_FAILURES` re-raise path (`newsbot/notify.py:113-114`) exists only for *scheduled* warnings | The likely outcome is a support ticket ("I paid, nothing happened") that the operator must resolve by hand, with no record that the notification failed | Route payment confirmations through the same durable outbox as OBS-011 and record delivery state | yes | OPEN |
| OBS-014 | No operator view of stuck locks, in-flight publications, or the last successful publish | MEDIUM · P2 | DESIGN WEAKNESS | `newsbot/ui_views_super.py:36-81` (`view_health`) and `newsbot/scheduler.py:130-207` (`report_text`) are the only two operator surfaces; the lock/in-flight state lives only in process memory (`newsbot/concurrency.py`, `newsbot/opctx.py:_operations`) and is never persisted | "Queue depth, stuck jobs, stale locks, failed publications, last successful cycle" are not visible in one place; stale locks — the state most likely to explain a stuck channel — are invisible because they are never written to the DB | Diagnosing "why did my channel stop publishing?" requires the DB plus guessing | Persist per-channel cycle/publish state; add a super-admin queue screen; surface `pay_requests.status='processing'` age in `/health` | no | OPEN |
| OBS-015 | The redaction factory swallows its own failure and would emit an unredacted record | MEDIUM · P2 | CONFIRMED SECURITY ISSUE (low probability, high severity)
| **Evidence:** `newsbot/logutils.py:47-59` — `rec.msg = redact(rec.getMessage()); rec.args = ()` inside a `try: ... except Exception: pass` | Actual — on a redaction failure the assignment never happens and the **original, unredacted** `rec.msg` is emitted with `rec.args` still attached. Expected — fail closed (suppress the record or emit a safe placeholder) | A single unhandled exception object in a log call could put a bot token or an API key into the journal. The redaction layer is the single control `tests/test_log_secret_leak.py` pins, and its failure mode is inverted | In the `except` branch replace `rec.msg` with a safe placeholder and clear `rec.args`; guard `redact()` itself | yes (monkeypatch `redact` to raise and assert the raw secret does not reach the handler) | OPEN |
| OBS-016 | Third-party loggers are not pinned; the Gemini key is sent as a query parameter | LOW · P3 | DESIGN WEAKNESS | `newsbot/logutils.py:12` pins only `httpx`; `newsbot/ai_models.py:132` — `params={"key": key}` | `httpcore`, `telegram`, `telegram.ext` and `apscheduler` stay at the root INFO level; the Gemini key travels in the URL, so any future INFO-level URL log (in httpx, a proxy, or a future PTB version) would leak it | Low residual risk today, but the guard is one dependency upgrade away from a real key leak, and no test would catch it | Send the Gemini key as `x-goog-api-key`; pin the other loggers; add a test asserting no `params={"key": ...}` pattern exists | yes | OPEN |

### Swallowed exceptions that hide business-meaningful failures (25 of 59)

| # | file:line | Class | Why it matters |
|---|---|---|---|
| 1 | `newsbot/ui_views_super.py:287` | traced | **Referral bonus permanently lost** after a successful payment (OBS-005). |
| 2 | `newsbot/ui_inputs.py:461` | warned | **Payment receipt never shown to any super-admin** -> the customer paid and the plan is never granted (OBS-013). |
| 3 | `newsbot/ui_widgets.py:621-622` | warned | **"Payment approved" message lost**; no retry, no record (OBS-012). |
| 4 | `newsbot/notify.py:85-86` | warned | The **only alerting channel** fails silently; the return value is discarded (OBS-011). |
| 5 | `newsbot/notify.py:80-81` | warned | Operational alerts are **dropped** under load (OBS-001). |
| 6 | `newsbot/notify.py:111-112` | warned | Same as #3, at the core-notify layer. |
| 7-13 | `newsbot/scheduler.py:69,86,88,90,103,128` and `newsbot/app.py:46` | warned (no traceback) | **The automation loop's** failures, the publication-recovery failure, dead models never recovering, expiry warnings never sent, the queue drain failing, retention cleanup failing, and the top-level tick failure — all without a traceback (OBS-004). |
| 14 | `newsbot/app.py:108-111` | silently | A PTB `error_handler` failure loses the unhandled-error notification entirely. |
| 15 | `newsbot/cycle.py:110-111` | silently | The user's live progress message fails invisibly. |
| 16 | `newsbot/core_articles.py:98-104` | warned | Media-delivery error classification; the bare `except Exception:` at `:104` discards the reason. |
| 17 | `newsbot/core_payments.py:123-127` | re-raised | The D1 compensation path — entirely untested (TEST-006). |
| 18 | `newsbot/publish.py:343-359` | traced | Publication failure is recorded in `articles.reason` but **not retried** and **not alerted**. |
| 19-20 | `newsbot/ratelimit.py:30-31,39-40` | debug-only | Rate-limit persistence/loading failure => a restart **resets the abuse limits**, at DEBUG level where nobody sees it. |
| 21 | `newsbot/ratelimit.py:67` | silently | A stale limit key persists in the DB. |
| 22 | `newsbot/net_http.py:189,194` | warned | Cloudflare KV mirror down => "more" links break for users (partial failure). |
| 23 | `newsbot/discover.py:345` | re-raised | Fails loudly — but `source_fail` itself is untested (TEST-007). |
| 24 | `newsbot/ai_models.py:111` | warned | AI token/cost accounting silently incomplete. |
| 25 | `newsbot/ai_models.py:118` | silently | `tokens_today()` returns **zeros** on error => the report shows "0 AI calls" during a DB outage. |
| — | `newsbot/ui_commands.py:26` | silently | **Security-relevant**: a DB error makes the ban check *fail open*. |
| — | `newsbot/db.py:321` | silently | **Security-relevant**: the `chmod 600` on the DB file may fail silently. |
| — | `newsbot/logutils.py:57` | silently | **Security-relevant**: fail-open redaction (OBS-015). |

---

# 11b. یافته‌های تازهٔ PH-01



## OPS-032 — jobِ استقرار روی محیطِ «production» اجرا می‌شود ولی قاعدهٔ محیط هنوز در تنظیمات ساخته نشده
- **Severity / Priority / Category:** LOW · P2 · CONFIRMED CONFIGURATION DEFECT
- **Evidence:** `.github/workflows/deploy-vps.yml:environment: production`, `tests/test_deploy_gate.py`
- **Fix applied:** PR #21: کلیدِ `environment: production` اضافه و با تست نگهبانی شد. تا وقتی قاعدهٔ «Required reviewers» در تنظیمات ساخته نشود، GitHub محیط را بدونِ محافظت می‌سازد ⇒ استقرار هنوز خودکار است.
- **Status:** OPEN


---

# 11b. یافته‌های تازهٔ PH-01

## FIN-025 — موجودیِ منتقل‌شده به سطلِ «legacy» تا اجرای اسکریپتِ تخصیصِ دستی قابل خرج نیست
- **Severity / Priority / Category:** MEDIUM · P2 · DESIGN WEAKNESS
- **Evidence:** `newsbot/db.py:_migrate_legacy_credit`, `newsbot/core_wallet.py:CUR_LEGACY`
- **Fix applied:** PH-01: موجودیِ قدیمی در سطلِ «legacy» منجمد شد (نه تبدیل، نه خرج). تخصیصِ دستیِ آن یک اسکریپتِ جداگانه و صریحاً مجاز است که هنوز نوشته نشده.
- **Status:** OPEN

## DB-012 — ستونِ `users.credit` بازنشسته شد ولی حذف نشده (مهاجرت افزایشی)
- **Severity / Priority / Category:** INFO · P3 · IMPROVEMENT OPPORTUNITY
- **Evidence:** `newsbot/db.py:SCHEMA (users.credit)`, `newsbot/db.py:_migrate_legacy_credit`
- **Fix applied:** PH-01: دیگر خوانده/نوشته نمی‌شود ولی حذف نشده (اصلاحِ افزایشی) — حذفِ ستون یک فازِ جداگانه با بکاپ لازم دارد.
- **Status:** OPEN


## DOC-001 — Six references point to a `ROADMAP.md` that is not tracked in the repository
- **Severity / Priority / Category:** LOW · P2 · DOCUMENTATION MISMATCH (repository integrity)
- **Evidence:** `docs/PERMISSIONS.md:3` — «مرجع: ROADMAP.md بخش ۶.۶ (فاز ۶ — مرز دسترسی)»;
  `docs/PERMISSIONS.md:98` — «…در ROADMAP (بخش گزارش فاز ۶) ثبت می‌شود»;
  `docs/SECURITY.md:3` — «مرجع: ROADMAP.md بخش ۶.۳ (فاز ۳)»;
  `docs/SECURITY.md:78` — «…در ROADMAP (جدول تصمیم‌ها) ثبت شده است»;
  `newsbot/ratelimit.py:20` — «(مستند در SECURITY/ROADMAP)»;
  `tests/test_authz_matrix.py:4` — «پوشش هدف‌های فاز ۶ طبق ROADMAP».
  `git ls-files | Select-String ROADMAP` -> **no match**: the document was never committed.
- **Actual vs Expected:** Actual — the document exists on the operator's machine but not in the repository, so
  every one of the six references is unreachable for anyone who clones the project, and the two cited sections
  (§۶.۳ and §۶.۶, which use **Persian digits**) cannot be checked. Expected — a document that the repository
  cites is tracked by the repository.
- **Verification:** **REPRODUCED (2026-10-20, PH-00)** — the local copy was inspected: 228 780 bytes, contains
  the exact cited headings `### ۶.۳ فاز ۳` and `### ۶.۶ فاز ۶`, and a secret scan over nine credential patterns
  (`BOT_TOKEN=`, `CF_API_TOKEN=`, `CF_D1_TOKEN=`, `SUPER_ADMIN_IDS=`, `SECRETS_KEY=`, `sk-…`, `ghp_…`, a
  `NNNNNNNNN:AA…` bot token, `PRIVATE KEY`) returned **0 matches**.
- **Impact:** a broken cross-reference in the two most security-sensitive documents and in application code;
  it undermines the traceability the project explicitly claims for its permission and security decisions.
- **Fix:** add the project's own `ROADMAP.md` to the repository root (original text unchanged, plus a
  five-line status note under the title marking it as the historical phase plan and pointing to
  `docs/audit/ROADMAP.md` as the live one), and add a regression test that asserts the document is tracked,
  that the cited sections exist, and that no backticked repository path in the tree points at a file that
  does not exist.
- **Regression test required:** yes —
  `tests/test_repo_integrity.py::test_roadmap_document_is_tracked_and_contains_the_cited_sections` and
  `::test_referenced_repo_paths_exist` (the latter carries an explicit allow-list of paths the audit
  *proposes* to add, so they must be removed from that list once created).
- **Dependencies / risks:** the added document describes a superseded phase plan (it is the 2026-10-14
  revision covering phases 1–13, all of which were subsequently merged as PRs #1–#16). It is added for
  traceability of the phase-3 / phase-6 decisions, not as a live plan; the live plan is
  `docs/audit/ROADMAP.md`. This is stated in the file's own header by the owner before the next edit.
- **Status:** **VERIFIED_FIXED (2026-10-20, PH-00)** — `ROADMAP.md` was added to the repository root (original
  text unchanged, plus a five-line status note under the title marking it as the historical phase plan) and
  the two regression tests above now enforce it (a negative control — removing the file — was executed and
  made the tests fail as expected).

---

# 13. PH-00 VERIFICATION PASS (2026-10-20)

## 13.1 Production verification — **BLOCKED (no access)**

The read-only production checklist in `ROADMAP.md` (PH-00) could **not** be executed: this environment has
**no route to the VPS**. Verified absent:

| Access vector | Result |
|---|---|
| `~/.ssh` directory | **does not exist** (no keys, no `config`, no `known_hosts`) |
| `ssh` client configuration / host entries | none |
| `gh` CLI | **not installed** (so GitHub secrets and environment protection cannot be listed either) |
| VPS/SSH/host/deploy environment variables | none |
| Any deploy host recorded in the repository | none — the host is only the GitHub secret `VPS_HOST` (`deploy-vps.yml:37`), whose **value is not readable** |

**Consequence:** every production-facing claim in this register remains **UNVERIFIED** — the running commit
SHA, the real systemd unit name and `WorkingDirectory`, the deployed Python version and dependency set, the
`.env` owner/mode, the database path/journal mode/integrity, the backup inventory, and the service journal.
This is a **limitation of the environment, not of the audit method**; the checklist itself is ready to run
from any host that has the key.

**What was verified instead, from the repository alone (mechanically, not by inspection by eye):**

| Check | Method | Result |
|---|---|---|
| `deploy.sh` service identity | `Select-String` on `deploy/deploy.sh:17-19` | `APP_DIR=/opt/bot-ai-automatic`, `SERVICE=bot-ai-automatic` |
| Occurrence counts | `git grep -c "bot-ai-automatic"` | **51** occurrences across **5** tracked files; `newsbot.service` = **0**; `bot-official-telegram` = **0** |
| The systemd drop-in also carries the old identity | read `deploy/systemd/10-restart-always.conf` | `ExecStopPost=… logger -t bot-ai-automatic …` (line 7) — **this occurrence was not in the original register and is now added to OPS-001** |
| `deploy/deploy.sh` index mode | `git ls-files -s deploy/deploy.sh` | `100644` — **not executable** (OPS-028) |
| `ROADMAP.md` tracked? | `git ls-files \| Select-String ROADMAP` | **no match** — 6 dangling references (DOC-001) |
| Secret scan of the local `ROADMAP.md` | 9 credential patterns | **0 matches** |

## 13.2 FIN-001 — **REPRODUCED** (was STATIC)

Executed against an isolated temporary SQLite database using the real business functions
(`record_referral` → `credit_referral_on_payment` → `upgrade_quote`); no repository or production file was
touched. Observed output:

```
plan U (en tariff) : price_num=5.0      price_num_fa=0.0
plan T (fa tariff) : price_num=0.0      price_num_fa=600000.0
[1] payer buys the fa plan for 600000.0 Toman
    credit_referral_on_payment -> (1001, 120000.0)
[2] referrer wallet  users.credit = 120000.0
    credit_log: [{'user_id': 1001, 'delta': 120000.0, 'balance_after': 120000.0, 'reason': 'referral', 'ref': '1002'}]
[3] referrer (lang=en) quotes the USD plan:
      new_cost=5.0  balance=120000.0  used_balance=5.0  payable=0.0  payable_num=True  blocked=False
[4] => FIN-001 REPRODUCED: a USD plan is obtained for FREE from a Toman-denominated reward (24000x over-credit)
[5] MIRROR: reward = 20% of 5.0 USD = 1.0 in a Toman wallet -> used_balance=1.0, payable=599999.0 (worthless)
```

This confirms the CRITICAL classification and raises the finding from `STATIC` to `REPRODUCED`.

## 13.3 Test suite — re-executed in the clean audit workspace

`python -m pytest -q -p no:cacheprovider` → **455 passed, 0 failed, 0 skipped, 0 errors** in 88.95 s
(same 3 × `PTBDeprecationWarning` as before). The repository was left clean.

## 13.4 Audit-document validation

A validator checked the register against itself: **516 evidence references** (`file:line`) were resolved
against the working tree — **0 pointing past EOF, 0 severity mismatches** between the register headings and
`status.json`, and the finding-ID sets of `FINDINGS.md` and `status.json` are **identical (180/180)**.

Two real defects were found in the audit documents themselves and corrected:

1. **Stale severity counts in the `FINDINGS.md` header** (`HIGH 25 / MEDIUM 68 / LOW 57 / INFO 29` instead of
   the true `26 / 78 / 64 / 11`) and an **"all OPEN"** claim that ignored the 4 deferred findings.
   The `MASTER_STATUS.md` counts were already correct; the two documents are now generated from one source
   and are re-checked by the validator.
2. **OPS-001 was missing one evidence site** — the `deploy/systemd/10-restart-always.conf:7` logger tag.

## 13.5 Repository-infrastructure fixes applied (with regression tests)

| # | Fix | Regression test |
|---|---|---|
| 1 | `deploy/deploy.sh` committed as **executable** (`100644` -> `100755` in the git index) — OPS-028 | `tests/test_repo_integrity.py::test_deploy_script_is_executable_in_git_index` |
| 2 | The project's own **`ROADMAP.md` added to the repository root verbatim**, resolving the 6 dangling references — DOC-001 | `::test_roadmap_document_is_tracked_and_contains_the_cited_sections` + `::test_referenced_repo_paths_exist` |

**Negative control:** removing `ROADMAP.md` from the tree was executed and made 2 of the 3 new tests fail as
expected — the tests are proven to detect the regression they claim to guard.

**Deliberately NOT changed:** the `bot-ai-automatic` identity itself (OPS-001). Renaming it without reading
the live host risks making a working service unstartable; it is blocked on the PH-00 production read.
No application logic, schema, dependency, workflow or deployment behaviour was modified.

---

# 14. Audit conclusions

**What is genuinely strong** (do not regress): the exactly-once publishing state machine and its recovery
(`newsbot/core_articles.py`, `tests/test_exactly_once_limits.py`, `tests/test_recovery_concurrency.py`); the
atomic, idempotent SQLite payment approval (`newsbot/core_payments.py:98-111`, `tests/test_payment_atomicity.py`);
the atomic referral wallet credit implemented as a **database trigger** (`newsbot/db.py:82-94`); the layered
SSRF defence for the source and media paths (`newsbot/net_http.py:89-181`, 73 tests in `tests/test_security.py`);
the prompt-injection fence proven end-to-end (`tests/test_security.py:85`, `tests/test_injection.py:75`);
the global pre-handler secret-redaction factory (`newsbot/logutils.py:45-63`); the WAL-safe backup, the
pre-restart gate and the **real automated rollback** in `deploy/deploy.sh` (rehearsed by `tests/test_deploy_script.py`);
the five-gate CI pipeline; the fail-fast, fully-documented configuration validation (`newsbot/config.py` +
`newsbot/app.py:114-170`); the 100 % accurate `.env.example`/README/config default reconciliation; and
**zero dead buttons and zero IDOR** in the 216-route callback map.

**What must change first:** the money path (FIN-001…FIN-006, FIN-012, FIN-013), the unguarded AI egress
(SEC-001/NET-001/NET-002), the deployment identity and health gate (OPS-001…OPS-004, OPS-026), the
receipt-notification loss chain (UI-006, UI-009, OBS-013), and the loss-of-observability chain
(OBS-002, OBS-004, OBS-005, OBS-007, OBS-011).

**Production state is UNVERIFIED.** Before any release reaches the VPS, the deploy identity, the running
commit SHA, the systemd unit, the `.env` permissions, the backup contents and the health-gate behaviour
must be read on the host (phase `PH-00` — **blocked in this environment**, see §13.1).

**End of register — 184 findings: 158 OPEN, 5 DEFERRED, 1 FIXED_UNVERIFIED, 20 VERIFIED_FIXED.**
