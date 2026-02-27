import sqlalchemy as sa
from db import engine, Category, Payment


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


def add_payment(category_name: str, amount: int, comment: str | None):
    with engine.begin() as conn:
        conn.execute(
            sa.insert(Payment).values(category_name=category_name, sum=amount, comment=comment)
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
