# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Kakebo is a personal finance tracking Telegram bot (Japanese household accounting method). Users log expenses by category through a Telegram chat interface.

## Running

```bash
# Start the bot (requires env vars)
TELEGRAM_TOKEN=<token> ALLOWED_USER_IDS=123,456 python tg_stuff.py

# Open the SQLite database directly
make db

# Debug the Yandex mail fetch standalone (dumps last N emails)
python mail_worker.py 10
```

The optional mail worker starts automatically when `YANDEX_EMAIL` /
`YANDEX_APP_PASSWORD` are set (IMAP host defaults to `imap.yandex.com`, override
via `YANDEX_IMAP_HOST`). Without them the bot runs normally, worker disabled.
Poll cadence via `MAIL_POLL_INTERVAL` (secs, default 60).

## Dependencies

Install with `pip install -r requirements.txt`. Key deps: `python-telegram-bot` (async), `sqlalchemy`, `python-dotenv`. Env vars can be set via a `.env` file in the project root.

## Architecture

Five-file structure with clear separation:

- **tg_stuff.py** — Telegram bot handlers and conversation state machine. Entry point. Uses `python-telegram-bot` async framework. Commands: `/menu` (start expense flow), `/help` (category descriptions), `/total` (spending stats), `/undo` (delete last payment). Also hosts the mail worker: a background asyncio poll loop (started in `post_init`) plus the inline-keyboard `CallbackQueryHandler` for categorising emailed payments.
- **db.py** — SQLAlchemy ORM models and database initialization. Tables: `category` (8 predefined categories with Russian descriptions), `payment` (expense records with integer amounts), `merchant_category` (remembered merchant→category for auto-logging), `processed_email` (dedup log). Running `db.py` directly **drops** and recreates the schema and seeds categories.
- **db_adaptor.py** — Database query layer. Functions for adding/undoing/recategorising payments, fetching stats, merchant-memory lookups, and email dedup.
- **mail_worker.py** — Yandex IMAP client (`YandexMailClient`, stdlib `imaplib`/`email`) returning `MailMessage` objects. Run directly for a debug dump.
- **parsers.py** — Per-bank regex parsers (`parse_mail`, sender→parser `PARSERS` registry) turning a bank email into a `ParsedPayment` (amount rounded to int THB, merchant, kind, ref). Currently supports K PLUS/Kasikornbank (plain text) and Krungsri (HTML).

State machine uses a `Stage` class stored in `context.user_data` with states: `menu`/`None` → `category_chosen` → back to menu.

## Conventions

- Python 3.9+ type hints (`list[str]`, `str | None`)
- UI text and category comments are in Russian
- Access restricted to Telegram user IDs from `ALLOWED_USER_IDS` env var (comma-separated) via `whitelist_user_deco` decorator
- Database path resolves to `kakebo.db` relative to `db.py` (not CWD)
- Payment amounts are integers (no decimals)