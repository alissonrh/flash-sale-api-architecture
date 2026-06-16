import argparse
import json
import sys

from sqlalchemy import func, select

from app.db.database import SessionLocal
from app.models.order import OrderModel
from app.models.product import ProductModel


INITIAL_STOCK = 10000
PRODUCT_IDS = (1, 2, 3)
EXPECTED_STATUSES = ("PENDING", "PROCESSING", "COMPLETED", "FAILED")


def build_summary() -> dict:
    db = SessionLocal()

    try:
        status_rows = db.execute(
            select(OrderModel.status, func.count(OrderModel.id)).group_by(OrderModel.status)
        ).all()
        orders_by_status = {status: 0 for status in EXPECTED_STATUSES}
        for status, count in status_rows:
            orders_by_status[status] = count

        products = db.execute(
            select(ProductModel).where(ProductModel.id.in_(PRODUCT_IDS)).order_by(ProductModel.id)
        ).scalars().all()

        total_orders = db.execute(select(func.count(OrderModel.id))).scalar_one()
        first_created_at = db.execute(select(func.min(OrderModel.created_at))).scalar_one()
        last_created_at = db.execute(select(func.max(OrderModel.created_at))).scalar_one()
        last_processed_at = db.execute(select(func.max(OrderModel.processed_at))).scalar_one()

        return {
            "total_orders": total_orders,
            "orders_by_status": orders_by_status,
            "products": [
                {
                    "id": product.id,
                    "name": product.name,
                    "initial_stock": INITIAL_STOCK,
                    "final_stock": product.stock,
                    "units_consumed": INITIAL_STOCK - product.stock,
                }
                for product in products
            ],
            "first_order_created_at": first_created_at.isoformat()
            if first_created_at
            else None,
            "last_order_created_at": last_created_at.isoformat()
            if last_created_at
            else None,
            "last_order_processed_at": last_processed_at.isoformat()
            if last_processed_at
            else None,
        }
    finally:
        db.close()


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--assert-idle", action="store_true")
    parser.add_argument("--assert-stocks", action="store_true")
    args = parser.parse_args()

    summary = build_summary()

    if args.assert_idle:
        pending = summary["orders_by_status"].get("PENDING", 0)
        processing = summary["orders_by_status"].get("PROCESSING", 0)
        if pending != 0 or processing != 0:
            fail(f"Pedidos pendentes/processando encontrados: PENDING={pending}, PROCESSING={processing}")

    if args.assert_stocks:
        by_id = {product["id"]: product["final_stock"] for product in summary["products"]}
        for product_id in PRODUCT_IDS:
            if by_id.get(product_id) != INITIAL_STOCK:
                fail(
                    f"Estoque inesperado para produto {product_id}: "
                    f"{by_id.get(product_id)} != {INITIAL_STOCK}"
                )

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
