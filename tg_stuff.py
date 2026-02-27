import functools
import os
import sys
import traceback

from dotenv import load_dotenv
load_dotenv()

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from db_adaptor import get_category_names, get_category_help, add_payment, get_total_stats, delete_last_payment

ALLOWED_USER_IDS = [int(x) for x in os.environ["ALLOWED_USER_IDS"].split(",")]
TOKEN = os.environ["TELEGRAM_TOKEN"]


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


def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("menu", menu_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("total", total_cmd))
    app.add_handler(CommandHandler("undo", undo_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.run_polling()


if __name__ == "__main__":
    main()
