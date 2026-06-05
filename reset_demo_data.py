from sqlalchemy import delete, select

from app.db.database import SessionLocal
from app.models.order import OrderModel
from app.models.product import ProductModel


DEFAULT_STOCKS = {
    1: 1000,
    2: 1000,
    3: 1000,
}


def main():
    db = SessionLocal()

    try:
        print("Removendo pedidos antigos...")
        db.execute(delete(OrderModel))

        print("Buscando produtos...")
        result = db.execute(select(ProductModel).order_by(ProductModel.id))
        products = result.scalars().all()

        if not products:
            print("Nenhum produto encontrado. Rode o seed primeiro.")
            db.rollback()
            return

        for product in products:
            if product.id in DEFAULT_STOCKS:
                old_stock = product.stock
                product.stock = DEFAULT_STOCKS[product.id]
                print(
                    f"Produto {product.id} ({product.name}): "
                    f"estoque {old_stock} -> {product.stock}"
                )

        db.commit()
        print("Dados resetados com sucesso.")

    except Exception as exc:
        db.rollback()
        print(f"Erro ao resetar dados: {exc}")
        raise

    finally:
        db.close()


if __name__ == "__main__":
    main()