import asyncio
import functools
import os
import sys
import traceback
import uuid
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
load_dotenv()

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from db_adaptor import (
    get_category_names,
    get_category_help,
    add_payment,
    get_total_stats,
    delete_last_payment,
    delete_payment,
    update_payment_category,
    get_merchant_category,
    set_merchant_category,
    is_email_processed,
    mark_email_processed,
    get_last_processed_email_date,
)
from mail_worker import YandexMailClient, MailMessage, mail_configured
from parsers import parse_mail, sender_address, PARSERS

ALLOWED_USER_IDS = [int(x) for x in os.environ["ALLOWED_USER_IDS"].split(",")]
TOKEN = os.environ["TELEGRAM_TOKEN"]

# Mail worker config
NOTIFY_CHAT_ID = ALLOWED_USER_IDS[0]
MAIL_POLL_INTERVAL = int(os.environ.get("MAIL_POLL_INTERVAL", "60"))
MAIL_START_DELAY = int(os.environ.get("MAIL_START_DELAY", "5"))
# Each poll rescans mail since (last processed email date - RESCAN_HOURS), to
# catch delayed/out-of-order deliveries. The first run (empty DB) scans the
# entire inbox.
MAIL_RESCAN_HOURS = int(os.environ.get("MAIL_RESCAN_HOURS", "1"))

# Friendly bank labels keyed by sender address
BANK_NAMES = {
    "kplus@kasikornbank.com": "K PLUS",
    "admin@krungsri.com": "Krungsri",
}


class Stage:
    menu = "menu"
    category_chosen = "category_chosen"

    @classmethod
    def get(cls, user_data: dict[str, str]):
        if "stage" not in user_data:
            return None
        return user_data["stage"]

    @classmethod
    def set(cls, user_data: dict[str, str], stage: str | None):
        if stage is None:
            user_data.pop("stage", None)
            return
        user_data["stage"] = stage


def whitelist_user_deco(handler):
    @functools.wraps(handler)
    async def real_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if context._user_id not in ALLOWED_USER_IDS:
            return await update.message.reply_text("unauthorised")
        await handler(update, context)

    return real_handler


def split_by_3(l: list[str]) -> list[list[str]]:
    return [
        l[i * 3:(i + 1) * 3]
        for i in range((len(l) - 1) // 3 + 1)
    ]


def category_keyboard():
    categories = get_category_names()
    keyboard = split_by_3(categories)
    reply_markup = ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    return reply_markup


# /menu command
@whitelist_user_deco
async def menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    Stage.set(context.user_data, Stage.menu)

    await update.message.reply_text(
        "Choose category:",
        reply_markup=category_keyboard(),
    )


# /help command
@whitelist_user_deco
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    Stage.set(context.user_data, None)

    await update.message.reply_text(
        get_category_help(),
        reply_markup=category_keyboard(),
    )

# /total command
@whitelist_user_deco
async def total_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    Stage.set(context.user_data, None)
    await update.message.reply_text(
        get_total_stats(),
        reply_markup=category_keyboard(),
    )


# /undo command
@whitelist_user_deco
async def undo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    Stage.set(context.user_data, None)
    result = delete_last_payment()
    text = "No payments to undo."
    if result is not None:
        text = result
    await update.message.reply_text(text, reply_markup=category_keyboard())


# all text comes here
@whitelist_user_deco
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if Stage.get(context.user_data) in (Stage.menu, None):
        return await category_chosen(update, context)
    if Stage.get(context.user_data) == Stage.category_chosen:
        return await amount_entered(update, context)
    print("unknown step:", Stage.get(context.user_data), file=sys.stderr)
    return


# Handle category choice
async def category_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    category = update.message.text
    if category not in get_category_names():
        return await update.message.reply_text(
            f"Unknown category: {category}",
        )

    Stage.set(context.user_data, Stage.category_chosen)
    context.user_data["category"] = category
    await update.message.reply_text(
        f"Enter amount (and comment) for {category}:",
        reply_markup=ReplyKeyboardRemove(),
    )


# Handle amount
async def amount_entered(update: Update, context: ContextTypes.DEFAULT_TYPE):
    category = context.user_data["category"]
    Stage.set(context.user_data, Stage.menu)

    try:
        data = update.message.text.strip().split("\n")
        amount, comment = data[0], "\n".join(data[1:]) or None
        amount = int(amount)
        add_payment(category_name=category, amount=amount, comment=comment)
    except Exception:
        await update.message.reply_text(
            f"Error occurred:\n{traceback.format_exc()}",
            reply_markup=ReplyKeyboardRemove(),
        )
    else:
        await update.message.reply_text(
            f"Category: {category}\nAmount: {amount}\nComment: {comment}",
            reply_markup=category_keyboard(),
        )


# ---------------------------------------------------------------------------
# Mail worker: poll Yandex, parse bank emails, log payments via Telegram.
# ---------------------------------------------------------------------------

# In-memory store of payments awaiting a button press. Keyed by a short token
# used in callback_data (which is capped at 64 bytes, so we can't embed the
# Thai merchant names / refs directly). Lost on restart -> stale buttons report
# "expired", which we handle gracefully.
PENDING: dict[str, dict] = {}


def _inline_categories(token: str) -> InlineKeyboardMarkup:
    """Category buttons, 3 per row; callback data references categories by index."""
    names = get_category_names()
    buttons = [
        InlineKeyboardButton(name, callback_data=f"cat:{token}:{i}")
        for i, name in enumerate(names)
    ]
    rows = split_by_3(buttons)
    return InlineKeyboardMarkup(rows)


def _to_utc_naive(dt: datetime | None) -> datetime | None:
    """Normalise a (possibly tz-aware) datetime to naive UTC for storage/compare."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _fetch_since_blocking(since: datetime | None) -> list[MailMessage]:
    """Blocking IMAP fetch — must be run in a worker thread. since=None -> all."""
    with YandexMailClient() as client:
        return client.fetch_since(since)


def _fetch_window_start() -> datetime | None:
    """Anchor the incremental fetch: last processed email date - RESCAN_HOURS.
    Returns None on the first run (empty DB) to fetch the entire inbox."""
    last = get_last_processed_email_date()
    if last is None:
        return None
    return last - timedelta(hours=MAIL_RESCAN_HOURS)


async def poll_mail_once(app: Application) -> None:
    """Fetch inbox since the rescan window, handle new bank emails (oldest first)."""
    since = _fetch_window_start()
    mails = await asyncio.to_thread(_fetch_since_blocking, since)
    for mail in mails:  # ascending IMAP order == chronological
        if sender_address(mail) not in PARSERS:
            continue  # only bank senders touch the dedup log
        message_id = mail.message_id or f"{mail.sender}|{mail.date}|{mail.subject}"
        if is_email_processed(message_id):
            continue
        parsed = parse_mail(mail)
        mark_email_processed(message_id, email_date=_to_utc_naive(mail.date))
        if parsed is None:
            continue
        await announce_payment(app, mail, parsed)


async def announce_payment(app: Application, mail: MailMessage, parsed) -> None:
    """Auto-log a remembered merchant, else prompt the user for a category."""
    bank = BANK_NAMES.get(sender_address(mail), sender_address(mail))
    token = uuid.uuid4().hex[:8]
    entry = {
        "merchant": parsed.merchant,
        "amount": parsed.amount,
        "currency": parsed.currency,
        "bank": bank,
    }
    PENDING[token] = entry

    remembered = get_merchant_category(parsed.merchant)
    if remembered is not None:
        payment_id = add_payment(remembered, parsed.amount, parsed.merchant)
        entry["payment_id"] = payment_id
        entry["category"] = remembered
        text = (
            f"✅ Auto-logged\n"
            f"{bank}: {parsed.merchant}\n"
            f"{parsed.amount} {parsed.currency} → {remembered}"
        )
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✏️ Change", callback_data=f"chg:{token}"),
            InlineKeyboardButton("🗑 Undo", callback_data=f"undo:{token}"),
        ]])
    else:
        text = (
            f"💳 New payment\n"
            f"{bank}: {parsed.merchant}\n"
            f"{parsed.amount} {parsed.currency}\n"
            f"Choose category:"
        )
        keyboard = _inline_categories(token)

    await app.bot.send_message(NOTIFY_CHAT_ID, text, reply_markup=keyboard)


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if context._user_id not in ALLOWED_USER_IDS:
        return

    action, _, rest = query.data.partition(":")
    token = rest.split(":")[0]
    entry = PENDING.get(token)
    if entry is None:
        await query.edit_message_text("This action expired (bot restarted).")
        return

    if action == "cat":
        category = get_category_names()[int(rest.split(":")[1])]
        if "payment_id" in entry:
            # Re-categorising an already-logged payment (came from "Change").
            update_payment_category(entry["payment_id"], category)
            set_merchant_category(entry["merchant"], category)
            entry["category"] = category
            await query.edit_message_text(
                f"✏️ {entry['bank']}: {entry['merchant']}\n"
                f"{entry['amount']} {entry['currency']} → {category}\n"
                f"(remembered for next time)"
            )
        else:
            # First categorisation of a new payment: log it, then offer to remember.
            entry["payment_id"] = add_payment(category, entry["amount"], entry["merchant"])
            entry["category"] = category
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("⭐ Always", callback_data=f"rem:{token}:1"),
                InlineKeyboardButton("Just once", callback_data=f"rem:{token}:0"),
            ]])
            await query.edit_message_text(
                f"Logged: {entry['bank']}: {entry['merchant']}\n"
                f"{entry['amount']} {entry['currency']} → {category}\n\n"
                f"Remember {entry['merchant']} as {category}?",
                reply_markup=keyboard,
            )

    elif action == "rem":
        remember = rest.split(":")[1] == "1"
        if remember:
            set_merchant_category(entry["merchant"], entry["category"])
            note = f"⭐ Will auto-log to {entry['category']} next time."
        else:
            note = "OK, just this once."
        await query.edit_message_text(
            f"{entry['bank']}: {entry['merchant']}\n"
            f"{entry['amount']} {entry['currency']} → {entry['category']}\n{note}"
        )
        PENDING.pop(token, None)

    elif action == "chg":
        await query.edit_message_text(
            f"{entry['bank']}: {entry['merchant']}\n"
            f"{entry['amount']} {entry['currency']}\nChoose new category:",
            reply_markup=_inline_categories(token),
        )

    elif action == "undo":
        if "payment_id" in entry:
            delete_payment(entry["payment_id"])
        await query.edit_message_text(
            f"🗑 Undone: {entry['bank']}: {entry['merchant']} "
            f"{entry['amount']} {entry['currency']}"
        )
        PENDING.pop(token, None)


async def mail_loop(app: Application) -> None:
    await asyncio.sleep(MAIL_START_DELAY)
    while True:
        try:
            await poll_mail_once(app)
        except Exception:
            print("mail_loop error:\n" + traceback.format_exc(), file=sys.stderr)
        await asyncio.sleep(MAIL_POLL_INTERVAL)


async def post_init(app: Application) -> None:
    if mail_configured():
        app.create_task(mail_loop(app))
        print("Mail worker started.", file=sys.stderr)
    else:
        print("YANDEX_EMAIL/YANDEX_APP_PASSWORD not set; mail worker disabled.", file=sys.stderr)


def main():
    app = ApplicationBuilder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(CommandHandler("menu", menu_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("total", total_cmd))
    app.add_handler(CommandHandler("undo", undo_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.run_polling()


if __name__ == "__main__":
    main()
