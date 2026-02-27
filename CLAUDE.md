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
```

## Dependencies

Install with `pip install -r requirements.txt`. Key deps: `python-telegram-bot` (async), `sqlalchemy`.

## Architecture

Three-file structure with clear separation:

- **tg_stuff.py** — Telegram bot handlers and conversation state machine. Entry point. Uses `python-telegram-bot` async framework. Conversation flow: `/menu` → category selection keyboard → user enters amount (+ optional comment on next line) → payment saved.
- **db.py** — SQLAlchemy ORM models (`Category`, `Payment`) and database initialization. Two tables: `category` (8 predefined categories with Russian descriptions) and `payment` (expense records with integer amounts). Running `db.py` directly recreates the schema and seeds categories.
- **db_adaptor.py** — Database query layer. Functions for adding payments and fetching aggregated statistics.

State machine uses a `Stage` class stored in `context.user_data` with states: `menu`/`None` → `category_chosen` → back to menu.

## Conventions

- Python 3.9+ type hints (`list[str]`, `str | None`)
- UI text and category comments are in Russian
- Access restricted to Telegram user IDs from `ALLOWED_USER_IDS` env var (comma-separated) via `whitelist_user_deco` decorator
- Database path hardcoded as `kakebo.db` in working directory
- Payment amounts are integers (no decimals)