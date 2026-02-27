from pprint import pprint

from sqlalchemy.orm import declarative_base, relationship, sessionmaker
import sqlalchemy as sa

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


engine = sa.create_engine("sqlite:///kakebo.db")
# Session = sessionmaker(bind=engine)
# session = Session()

if __name__ == '__main__':
    Base.metadata.drop_all(engine)
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
            conn.execute(sa.insert(Category).values(id=i, name=c, comment=comment))
    with engine.begin() as conn:
        res = conn.execute(sa.select(Category)).fetchall()
        pprint(("Category", res))
        res = conn.execute(sa.select(Payment)).fetchall()
        pprint(("Payment", res))
