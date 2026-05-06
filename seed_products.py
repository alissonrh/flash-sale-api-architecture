from sqlalchemy import select

from app.db.database import SessionLocal
from app.models.product import ProductModel


INITIAL_PRODUCTS = [
    {
        "id": 1,
        "name": "Notebook Gamer",
        "price": 5999.90,
        "stock": 8,
        "flash_sale": True,
    },
    {
        "id": 2,
        "name": "Smartphone Pro",
        "price": 2499.90,
        "stock": 15,
        "flash_sale": True,
    },
    {
        "id": 3,
        "name": "Fone Bluetooth",
        "price": 299.90,
        "stock": 40,
        "flash_sale": False,
    },
]


def main():
    db = SessionLocal()
    try:
        existing_product = db.execute(select(ProductModel)).scalars().first()

        if existing_product is not None:
            print("Produtos já existem na tabela. Seed ignorado.")
            return

        db.add_all([ProductModel(**product) for product in INITIAL_PRODUCTS])
        db.commit()
        print("Produtos inseridos com sucesso.")
    finally:
        db.close()


if __name__ == "__main__":
    main()