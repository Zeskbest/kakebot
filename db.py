import os
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import declarative_base, relationship

_BASE_DIR = Path(os.path.dirname(os.path.abspath(__file__)))

Base = declarative_base()


class Category(Base):
    __tablename__ = "category"

    id = sa.Column(sa.Integer)
    name = sa.Column(sa.Text, nullable=False, primary_key=True)
    comment = sa.Column(sa.Text, nullable=False)

    payments = relationship("Payment", back_populates="category")


class Payment(Base):
    __tablename__ = "payment"

    id = sa.Column(sa.Integer, primary_key=True, autoincrement=True)
    created_at = sa.Column(sa.DateTime, server_default=sa.text("DATETIME('now')"))

    category_name = sa.Column(sa.Text, sa.ForeignKey("category.name"), nullable=False)
    sum = sa.Column(sa.Integer, nullable=False)
    comment = sa.Column(sa.Text)

    category = relationship("Category", back_populates="payments")


class MerchantCategory(Base):
    """Remembered merchant -> category mapping ("remember this merchant forever")."""
    __tablename__ = "merchant_category"

    merchant = sa.Column(sa.Text, primary_key=True)
    category_name = sa.Column(sa.Text, sa.ForeignKey("category.name"), nullable=False)


class ProcessedEmail(Base):
    """Dedup log so each bank email is handled at most once."""
    __tablename__ = "processed_email"

    message_id = sa.Column(sa.Text, primary_key=True)
    processed_at = sa.Column(sa.DateTime, server_default=sa.text("DATETIME('now')"))
    # The email's own Date header (UTC, naive). Anchors the incremental fetch
    # window (SINCE last email date - 1h) so we don't rescan the whole inbox.
    email_date = sa.Column(sa.DateTime)


engine = sa.create_engine(f"sqlite:///{_BASE_DIR / 'kakebo.db'}")
# Session = sessionmaker(bind=engine)
# session = Session()

if __name__ == '__main__':
    # Incremental: create_all only adds missing tables; the category seed is
    # idempotent (skips categories that already exist), so re-running is safe
    # and preserves existing payments.
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        for i, (c, comment) in enumerate({
            "Groceries": "любые походы в магазины за едой/для дома: закуп в лотусе, вилла маркете",
            "Restaurants": "любые кафе/рестораны/кофе",
            "Entertainment": "развлечения типа походов в зоопарки или аквапарки, спорт и тд",
            "Shopping": "одежда, онлайн шопинг (кроме подарков), иные покупки (кроме базовых закупов в лотусе и тд)",
            "Transport": "такси, билеты на авиа и тд",
            "Health": "врачи, лекарства (кроме линз)",
            "Special occasions": "подарки, цветы, волосы, бордеры, визы",
            "Rental": "дом, машина",
        }.items()):
            conn.execute(
                sqlite_insert(Category)
                .values(id=i, name=c, comment=comment)
                .on_conflict_do_nothing(index_elements=[Category.name])
            )
