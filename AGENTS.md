# Project Notes for Coding Agents

## Purpose

This is a small Telegram bot for tracking moving boxes. Users create boxes, attach item lists and Telegram photo `file_id`s, search by item text, update box status, and print QR codes that deep-link back to the box card in the bot.

Keep changes small and practical. This bot is intended to run on a low-memory VPS, so avoid heavy dependencies or background services unless the user explicitly asks for them.

## Runtime

- Python 3.11+
- aiogram 3.x
- SQLite file database at `data/boxes.db`
- Polling, not webhooks
- Default Telegram `parse_mode` is HTML in `src/main.py`
- Config comes from `.env` via `src/config.py`

Required `.env` values:

```env
BOT_TOKEN=...
WHITELIST_USER_IDS=123456789
```

Do not print `.env` contents or bot tokens in logs, command output summaries, commits, or documentation.

## Repository Map

- `src/main.py` - bot setup, logging, Telegram session, polling, global HTML parse mode.
- `src/config.py` - environment loading, whitelist parsing, paths.
- `src/database.py` - SQLite schema, migrations, and async wrapper around `sqlite3`.
- `src/keyboards.py` - reply and inline keyboards.
- `src/qr.py` - QR generation and Telegram deep links.
- `src/middlewares.py` - whitelist access control and user upsert.
- `src/handlers/boxes.py` - box creation, card rendering, QR sending, item edits, delete callbacks.
- `src/handlers/search.py` - search and list flows.
- `src/handlers/photos.py` - adding photos to existing boxes.
- `src/handlers/statuses.py` - status selection callbacks.
- `TZ.md` - original product brief; useful for intent, but the code is the source of truth.
- `README.md` - setup, deployment, and manual test notes.

## Important Implementation Details

- Telegram messages use HTML formatting by default. Escape user-controlled text with `html.escape()`.
- Box cards are formatted in `box_text()` in `src/handlers/boxes.py`.
- QR images are sent from two places:
  - after creating a box in `finish_create_box()`;
  - from the `box:qr:*` callback in `send_qr_callback()`.
- The create-flow QR caption currently wraps the box code in `<code>...</code>` so Telegram clients can copy it with one tap.
- QR deep links use `https://t.me/{bot_username}?start=box_{box_code}` and are handled by `/start` arguments.
- Legacy `/box_CODE` commands are still handled in `src/handlers/boxes.py`.
- Store Telegram photos as `file_id` only. Do not download or persist photo binaries unless asked.
- Voice recognition is intentionally a stub. If implementing it, use a cloud API, not local Whisper, because the target VPS is memory constrained.

## Database Notes

Schema is created in `src/database.py`:

- `boxes`: `id`, `code`, `room`, `status`, `created_at`
- `items`: `id`, `box_id`, `name`
- `photos`: `id`, `box_id`, `file_id`
- `users`: `user_id`, `name`
- `room_counters`: `prefix`, `last_number`

Box codes are generated from the room prefix plus a per-prefix counter, for example `DIR-01`.

The database layer intentionally uses `sqlite3` in a single-worker executor. Keep that pattern unless there is a clear reason to change it.

## Commands

Use the existing virtualenv when present:

```bash
venv/bin/python -m compileall src
venv/bin/python test_sqlite.py
```

Run the bot locally:

```bash
venv/bin/python -m src.main
```

If `venv/` is absent, follow `README.md` and install `requirements.txt` in a virtualenv.

## Deployment After Push

After pushing changes to GitHub for this project, deploy them on the VPS as the final step:

```bash
cd /root/projects/box-bot
git pull origin main
docker compose build
docker compose up -d
docker compose logs --tail 20
```

Confirm that `docker compose ps` shows the `box-bot` service as running. If it exits or restarts repeatedly, show `docker compose logs --tail 50` and wait for user instructions instead of trying to fix it automatically.

## Testing Guidance

For code-only changes, at minimum run:

```bash
venv/bin/python -m compileall src
```

For database behavior, run:

```bash
venv/bin/python test_sqlite.py
```

For Telegram formatting or Bot API behavior, send a real test message only when `.env` is configured and network access is allowed. Use a whitelisted chat id from `WHITELIST_USER_IDS`, but do not reveal it in the final response.

## Style

- Match the existing straightforward aiogram handler style.
- Keep Russian user-facing text consistent with the current bot copy.
- Prefer HTML formatting in Telegram messages unless a specific message already uses a different parse mode.
- Escape user data, but do not escape trusted fixed Russian text for HTML unless it contains HTML-sensitive characters.
- Avoid large refactors during narrow fixes.
- Do not commit generated runtime files from `data/`, `logs/`, caches, or local test databases.
