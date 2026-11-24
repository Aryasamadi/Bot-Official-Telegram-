# Phase 8 — Deeplink "back to channel" (design)

Date: 2026-11-25 · Status: awaiting owner approval · Roadmap row 8 · Findings: AUD-008 (external audit, §«فاز ۸»)

## Intent and why

Every time a channel post is shortened, the "more" link opens the bot with `/start=r_<key>`. The bot
shows the full article, but the reader ends up in a dead end: there is no way back to the post they
came from, so they either lose the channel or scroll for it manually. Requirement 3.7 asks for a
localized **"back to channel"** control on that page. AUD-008 records that today the payload keeps
only text/URL/media and no source reference at all (`newsbot/publish.py:402`), and `send_deeplink`
(`newsbot/ui_commands.py:52-62`) sends the body with no keyboard.

## Goals

1. A deeplink page created **after** this phase carries an inline URL button that opens the exact
   source post in the channel.
2. The URL is derived from data, not frozen: a renamed channel self-heals on its next publication,
   a removed channel still degrades to the private `t.me/c/...` form, and a payload from an older
   build simply has no button.
3. Old deeplinks (no source reference) behave exactly as today — no button, no note, no error.
4. Private channels get the closest valid UX plus one honest line that only members can open it;
   a channel whose row no longer exists gets wording that promises nothing false.
5. Exactly **one** control per deeplink, even when a long body is split into several messages.
6. No new API call, no schema change, no new callback, no change to the "more" path itself.

## Non-goals

- Detecting a deleted post before/after render (needs an extra Bot API call; out of scope).
- Forwarding/copying the channel post into the bot chat.
- Changing the deeplink payload's existing keys, the text builder, or `save_deeplink` semantics.
- Replacing the `/start` referral (`ref_`) or deeplink (`r_`) argument precedence.
- Refreshing usernames for channels that never publish again (a documented, rare staleness).

## Anti-goals

- A button that silently points at the wrong post (wrong id, wrong channel).
- A dead button on every private channel with no explanation.
- Regressing old deeplinks into an error or an empty keyboard.
- A button repeated on every chunk of a split message.

## Current state (facts the design rests on)

| Where | What exists today |
|---|---|
| `newsbot/publish.py:398-403` | payload = short/full/title/url/show_source/media/ts; key generated before send |
| `newsbot/publish.py:437-443` | after a successful send: `link = msg_link(ch, msg)` → journal, then `save_deeplink(...)` |
| `newsbot/core_channels.py:161` | `msg_link(ch, msg)`: `t.me/<username>/<id>` or `t.me/c/<chat_id minus -100>/<id>` |
| `newsbot/core_channels.py:74` | `channel_by_chat(chat_id)` resolves the current channel row |
| `newsbot/core_channels.py:157-159` | `update_channel_meta(cid, username=...)` exists for a refresh |
| `newsbot/ui_commands.py:52-62` | `send_deeplink` builds the body and calls `rich_send.send_content(...)` with no markup |
| `newsbot/ui_widgets.py:59` | `U(label, url)` builds an `InlineKeyboardButton(url=...)` |
| `newsbot/ui_widgets.py:638` | the notification path already sends `send_content(..., reply_markup=...)` — the rich path is assumed to carry keyboards |
| `newsbot/rich_send.py:240-253` | `send_rich_raw` forwards `reply_markup` into the `sendRichMessage` payload |
| `newsbot/rich_send.py:262-287` | `send_legacy` attaches the markup to **every** chunk |
| `newsbot/ui_i18n.py:347` | deeplink strings live beside `dl_notfound` / `dl_source` |

The source post's ids are only known **after** `send_post` returns; the payload row is written only
on success (`publish.py:440-443`), which is exactly the right moment to enrich it.

## Platform limits (honest, documented, not hidden)

1. **Public channel** — `https://t.me/<username>/<message_id>` opens the post for anyone.
2. **Private channel** — `https://t.me/c/<internal_id>/<message_id>` opens the post **only for
   members** (and for the channel's admins) in an official client. A non-member gets Telegram's own
   "private channel" refusal, which we cannot restyle. Hence the one-line note.
3. **Deleted post** — Telegram answers "post not found"; the bot cannot know in advance without an
   extra API call, and a stale cached check would be worse.
4. **Renamed channel** — the authoritative handle arrives with each successful publication
   (`msg.chat.username`), so the next publication repairs the stored handle; a channel that never
   publishes again keeps its old handle in the payload (documented).
5. The source reference is data only; nothing about the reader is stored or logged.

## Approaches (owner picked A on 2026-11-25)

**A — source reference in the payload + URL button (chosen).** Add
`src: {"chat_id", "message_id", "username"}` to the JSON at the existing post-success
`save_deeplink` call; render resolves the current username through `channel_by_chat` and builds the
URL with one pure helper shared with `msg_link`. Old rows simply lack `src`. No schema change, no
new API, the URL stays current if the username changes and posts continue, and a deleted channel
still degrades to the `t.me/c/...` form because `chat_id` is stored independently.

**B — store the final `journal["link"]` string.** One line simpler, rejected: the URL freezes at
publish time, so a username change leaves every old deeplink pointing at a stale address, and we
would ship a value we can derive better later.

**C — guidance text instead of a button.** Always "works", rejected: the requirement explicitly
prefers the native URL control when it is possible, and a text instruction is strictly worse UX
for the public case that covers most posts.

## Design

### 1. Payload (ADD-only)

At the success path, before `save_deeplink`:

```python
dl_payload["src"] = {
    "chat_id": int(ch["chat_id"]),
    "message_id": int(msg.message_id),
    "username": (getattr(getattr(msg, "chat", None), "username", "") or ch["username"] or ""),
}
```

- Written only after a confirmed send, beside the existing journal/ack logic — never for a failed
  post, so no orphan reference.
- `username` is a **snapshot taken from Telegram's own answer** (falling back to the stored row), so
  a channel row deleted later still yields a working public link.
- Existing keys untouched; `net_http` needs no change (it already stores arbitrary JSON).
- Rows written by older builds have no `src` and must keep their current behaviour.

### 2. One URL rule, one helper

In `newsbot/core_channels.py`, next to `msg_link`:

```python
def post_url(chat_id, message_id, username=""):
    """URL of a channel post: public form when a username exists, else the private c/ form."""
```

- `msg_link(ch, msg)` becomes `return post_url(ch["chat_id"], msg.message_id, ch["username"])`, so the
  journal link and the button can never disagree.
- Returns `""` for a missing/non-positive `message_id` or a non-`-100…` chat id, so a corrupt
  payload renders no button instead of a wrong link.

### 3. Handle self-healing on publication (my call, D8-6)

After a successful send, if Telegram reported a non-empty `msg.chat.username` that differs from the
stored `channels.username`, call `update_channel_meta(ch["id"], username=...)` inside a `try/except`
that can never fail the publication. Rationale: without it, a rename makes every new deeplink point
at the old handle (a dead link, or worse a reassigned handle); with it, the next post repairs it.
The payload snapshot (above) is the fallback for a channel row that no longer exists.

### 4. Rendering (`send_deeplink`)

```
src  = data.get("src") if isinstance(data, dict) else None
chat_id, message_id = src.get("chat_id"), src.get("message_id") if src else (None, None)
ch   = channel_by_chat(chat_id) if chat_id else None
name = (ch["username"] if ch else "") or (src.get("username") if src else "")
url  = post_url(chat_id, message_id, name)
kb   = InlineKeyboardMarkup([[U(tr(lang, "dl_back"), url)]]) if url else None
```

Note wording, chosen by what we actually know:

| Situation | Note |
|---|---|
| public URL (`name` non-empty) | none |
| channel row exists, no username (private) | `dl_private_note` — "only members of the private channel" |
| no row, no username (unknown/deleted row) | `dl_unknown_note` — "if you are a member, this opens the post" |

- The keyboard goes to the **text** `send_content` call; the media message (if any) stays
  caption-only, so the button always sits on the readable body.
- No `src` / no message id / unparsable value ⇒ `kb = None` and the body is byte-identical to today.
- No logging of `src`, of the URL, or of the reader's id.

### 5. Exactly one button on split bodies (my call, D8-7)

The classic sender repeats `reply_markup` on every chunk (`rich_send.py:272-275`), which would put the
button on each of up to five parts of a 20 000-character article. Add a keyword-only
`markup_last_only=False` to `send_legacy` and `send_content` and pass it through; `send_deeplink`
sets it to `True`, so the control appears once, on the last part (right under the note). Default
behaviour of every other caller is unchanged. The rich path sends a single native message, so it is
unaffected.

### 6. i18n (TXT, fa/en 2-tuples)

| Key | fa | en |
|---|---|---|
| `dl_back` | `↩️ بازگشت به کانال` | `↩️ Back to channel` |
| `dl_private_note` | `این دکمه فقط برای اعضای کانالِ خصوصی باز می‌شود.` | `This button opens only for members of the private channel.` |
| `dl_unknown_note` | `اگر عضو کانال هستید، این دکمه همان پست را باز می‌کند.` | `If you are a member of the channel, this opens that post.` |

Parity is enforced by the existing fa/en tests.

### 7. Edge cases (defined behaviour)

| Case | Behaviour |
|---|---|
| Old deeplink, no `src` | no button, no note — identical to today |
| New, public channel | `t.me/<username>/<id>` button, no note |
| New, private channel | `t.me/c/<internal>/<id>` button + member-only note |
| Channel row deleted, snapshot present | public URL from the snapshot (still works) |
| Channel row deleted, no snapshot | `t.me/c/...` button + "if you are a member" note |
| Username changed, channel publishes again | stored handle refreshed → correct URL |
| Username changed, no further publication | stale handle until the next post (documented) |
| Post deleted | button stays (we cannot know); Telegram shows its own "not found" |
| `message_id` missing/0/non-integer | no button, body unchanged |
| `src` is not a dict / payload not a dict | treated as no `src`; no crash |
| Body longer than 4096 (legacy split) | one button, on the last part only |

## Decisions taken by the builder (owner may override)

- **D8-1** Payload stores ids **and** the observed username; the URL is always derived at render
  time.
- **D8-2** `msg_link` delegates to `post_url`; the journal link and the button share one rule.
- **D8-3** Notes are keyed on what is known (public / private / unknown), never on a guess.
- **D8-4** Button label is localized through `TXT`, not `DEFAULT_TEXTS`, matching Phase 7's rule.
- **D8-5** `SCHEMA_VERSION` stays 13 — the phase adds no column and no table.
- **D8-6** A successful publication refreshes a changed channel handle (fail-safe, never fatal).
- **D8-7** One control per deeplink via `markup_last_only=True` (default off everywhere else).
- **D8-8** The rich path relies on `reply_markup` exactly as the existing notification path does
  (`newsbot/ui_widgets.py:638`); the concrete client behaviour is verified in the Phase 10 run.

## Test plan (new file `tests/test_phase8_deeplink_back.py`)

Pure and fast, no network:

1. `post_url` public → `t.me/<user>/<id>`; private `-100…` → `t.me/c/<internal>/<id>`; missing message
   id → `""`; non-`-100` chat id → `""`; `msg_link` delegates identically.
2. Old payload (no `src`) → no keyboard, body byte-identical (regression pin).
3. New public payload → one URL button, label localized fa/en, no note.
4. New private payload → `t.me/c/...` + member note exactly once.
5. Row missing with snapshot → public URL, no note; row missing without snapshot → c-link + unknown
   note.
6. Corrupt `src` (string message id, chat id `None`, non-dict payload) → no button, no crash.
7. Publish success writes `src` with the ids of the message actually sent; a failed send writes
   nothing (reuse the `PubBot` + `_ready` harness from `tests/test_exactly_once_limits.py`).
8. A publication reporting a new `chat.username` updates the channel row (fail-safe test with a fake
   `msg.chat`).
9. Split legacy body → `reply_markup` only on the last chunk (unit test on `send_legacy` with a fake
   bot and three chunks).
10. Reference/URL appears neither in logs nor in the rendered text.

## Acceptance mapping

| Audit acceptance item | Covered by |
|---|---|
| Old deeplink without error | test 2 |
| New deeplink button points at the same post | tests 1, 3, 7 |
| Private / public channel | tests 1, 3, 4, 5 |
| Deleted post = defined behaviour | documented limit; no false promise (tests 4–5) |
| "Button + test + limitation note + PR" | this design, tests, README/state note, PR chain |

Live confirmation that Telegram opens the button in a real client is **not claimed** here; it joins
the single Phase 10 run (D-7) together with Phases 4–7.

## Migration / rollback

- No schema change, no data migration. The new key lives inside existing JSON rows.
- Rollback = revert the code; older builds ignore `src` and `markup_last_only`, so already-written
  deeplinks keep working (without the button).
- No secrets or new personal data are stored.

## Risks

- **Low.** Worst case is a button Telegram declines to open (private, non-member, deleted post) —
  documented and unavoidable without extra API calls.
- The `t.me/c/...` form requires the `-100` internal id; `post_url` returns `""` otherwise, so
  nothing is silently wrong.
- Refresh-on-publish adds one small DB write per rename only (not per post).

## What is left to the builder

- Exact placement inside `publish.py`'s success block and the shape of the fake used by tests 7–8.
- Whether the note goes above or below the source link (design choice: last line, after the body and
  the source link).
- Whether to show the private note when a public channel is later made private (cannot be detected
  without an API call; no).
