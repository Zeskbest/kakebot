import sqlalchemy as sa
from db import engine, Category, Payment, MerchantCategory, ProcessedEmail


def get_category_names() -> list[str]:
    with engine.begin() as conn:
        res = conn.execute(sa.select(Category.name).order_by(Category.id)).fetchall()
    return [c for c, in res]


def get_category_help() -> str:
    with engine.begin() as conn:
        categories = conn.execute(sa.select(Category.name, Category.comment).order_by(Category.id)).fetchall()
    return "Categories:\n" + "\n".join(
        f"* {name} - {comment}" for name, comment in categories
    )


def add_payment(category_name: str, amount: int, comment: str | None) -> int:
    with engine.begin() as conn:
        result = conn.execute(
            sa.insert(Payment).values(category_name=category_name, sum=amount, comment=comment)
        )
    return result.inserted_primary_key[0]


def delete_payment(payment_id: int) -> bool:
    with engine.begin() as conn:
        res = conn.execute(sa.delete(Payment).where(Payment.id == payment_id))
    return res.rowcount > 0


def update_payment_category(payment_id: int, category_name: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.update(Payment).where(Payment.id == payment_id).values(category_name=category_name)
        )


def delete_last_payment() -> str | None:
    with engine.begin() as conn:
        last = conn.execute(
            sa.select(Payment).order_by(Payment.id.desc()).limit(1)
        ).first()
        if last is None:
            return None
        conn.execute(sa.delete(Payment).where(Payment.id == last.id))
    return f"Deleted: {last.category_name}, {last.sum}, {last.comment or ''}"


def get_merchant_category(merchant: str) -> str | None:
    """Return the remembered category for a merchant, or None if not remembered."""
    with engine.begin() as conn:
        return conn.execute(
            sa.select(MerchantCategory.category_name).where(MerchantCategory.merchant == merchant)
        ).scalar_one_or_none()


def set_merchant_category(merchant: str, category_name: str) -> None:
    """Remember (upsert) the category to auto-apply for this merchant from now on."""
    with engine.begin() as conn:
        conn.execute(sa.delete(MerchantCategory).where(MerchantCategory.merchant == merchant))
        conn.execute(
            sa.insert(MerchantCategory).values(merchant=merchant, category_name=category_name)
        )


def forget_merchant(merchant: str) -> None:
    with engine.begin() as conn:
        conn.execute(sa.delete(MerchantCategory).where(MerchantCategory.merchant == merchant))


def is_email_processed(message_id: str) -> bool:
    with engine.begin() as conn:
        return conn.execute(
            sa.select(ProcessedEmail.message_id).where(ProcessedEmail.message_id == message_id)
        ).first() is not None


def mark_email_processed(message_id: str) -> None:
    """Record an email as handled. Idempotent (ignores duplicates)."""
    with engine.begin() as conn:
        exists = conn.execute(
            sa.select(ProcessedEmail.message_id).where(ProcessedEmail.message_id == message_id)
        ).first()
        if exists is None:
            conn.execute(sa.insert(ProcessedEmail).values(message_id=message_id))


def get_total_stats():
    with engine.begin() as conn:
        payments = conn.execute(
            sa.select(Payment.category_name, sa.func.sum(Payment.sum)).group_by(Payment.category_name)
        ).fetchall()
    total = sum((s for _, s in payments))
    if total == 0:
        return "No payments recorded yet."
    return f"Total spent: {total}\nRatio:\n" + "\n".join(
        f"* {c}: {s / total * 100:.4}%"
        for c, s in payments
    )
