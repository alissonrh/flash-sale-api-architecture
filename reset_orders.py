from sqlalchemy import delete

from app.db.database import SessionLocal
from app.models.order import OrderModel


def main():
    db = SessionLocal()
    try:
        db.execute(delete(OrderModel))
        db.commit()
        print("Pedidos removidos com sucesso.")
    finally:
        db.close()


if __name__ == "__main__":
    main()