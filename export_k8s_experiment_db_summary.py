import argparse
import json
import math
import sys

from sqlalchemy import func, select

from app.db.database import SessionLocal
from app.models.order import OrderModel
from app.models.product import ProductModel


INITIAL_STOCK = 10000
PRODUCT_IDS = (1, 2, 3)
EXPECTED_STATUSES = ("PENDING", "PROCESSING", "COMPLETED", "FAILED")


def percentile(values: list[float], probability: float) -> float | None:
    """Return a percentile using linear interpolation at (n - 1) * p.

    This is the same rank convention commonly called the type-7 percentile:
    sort the observations, locate the fractional zero-based index
    ``(sample_size - 1) * probability``, and linearly interpolate between the
    adjacent observations when that index is not an integer.
    """
    if not values:
        return None

    ordered_values = sorted(values)
    position = (len(ordered_values) - 1) * probability
    lower_index = math.floor(position)
    upper_index = math.ceil(position)

    if lower_index == upper_index:
        return ordered_values[lower_index]

    fraction = position - lower_index
    return (
        ordered_values[lower_index]
        + (ordered_values[upper_index] - ordered_values[lower_index]) * fraction
    )


def isoformat(value):
    return value.isoformat() if value is not None else None


def rounded(value: float | None) -> float | None:
    return round(value, 3) if value is not None else None


def build_summary() -> dict:
    db = SessionLocal()

    try:
        status_rows = db.execute(
            select(OrderModel.status, func.count(OrderModel.id)).group_by(
                OrderModel.status
            )
        ).all()
        orders_by_status = {status: 0 for status in EXPECTED_STATUSES}
        for status, count in status_rows:
            orders_by_status[status] = count

        products = db.execute(
            select(ProductModel)
            .where(ProductModel.id.in_(PRODUCT_IDS))
            .order_by(ProductModel.id)
        ).scalars().all()

        orders = db.execute(
            select(OrderModel).order_by(OrderModel.created_at, OrderModel.id)
        ).scalars().all()

        processing_times_ms = [
            (order.processed_at - order.created_at).total_seconds() * 1000
            for order in orders
            if order.processed_at is not None
        ]

        first_order = orders[0] if orders else None
        last_order = orders[-1] if orders else None
        last_processed_at = db.execute(
            select(func.max(OrderModel.processed_at))
        ).scalar_one()

        total_orders = len(orders)
        completed_orders = orders_by_status["COMPLETED"]
        completion_rate = completed_orders / total_orders if total_orders else None

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
            "first_order": {
                "id": first_order.id,
                "created_at": isoformat(first_order.created_at),
            }
            if first_order
            else None,
            "last_order": {
                "id": last_order.id,
                "created_at": isoformat(last_order.created_at),
            }
            if last_order
            else None,
            "last_processed_at": isoformat(last_processed_at),
            "processing_time_ms": {
                "sample_count": len(processing_times_ms),
                "average": rounded(
                    sum(processing_times_ms) / len(processing_times_ms)
                    if processing_times_ms
                    else None
                ),
                "minimum": rounded(min(processing_times_ms))
                if processing_times_ms
                else None,
                "p90": rounded(percentile(processing_times_ms, 0.90)),
                "p95": rounded(percentile(processing_times_ms, 0.95)),
                "p99": rounded(percentile(processing_times_ms, 0.99)),
                "maximum": rounded(max(processing_times_ms))
                if processing_times_ms
                else None,
                "percentile_method": "linear interpolation at (n - 1) * p (type 7)",
            },
            "completion_rate": round(completion_rate, 6)
            if completion_rate is not None
            else None,
            "completion_rate_percent": round(completion_rate * 100, 3)
            if completion_rate is not None
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
        pending = summary["orders_by_status"]["PENDING"]
        processing = summary["orders_by_status"]["PROCESSING"]
        if pending != 0 or processing != 0:
            fail(
                "Pedidos ainda ativos: "
                f"PENDING={pending}, PROCESSING={processing}"
            )

    if args.assert_stocks:
        stocks_by_id = {
            product["id"]: product["final_stock"]
            for product in summary["products"]
        }
        for product_id in PRODUCT_IDS:
            if stocks_by_id.get(product_id) != INITIAL_STOCK:
                fail(
                    f"Estoque inesperado para produto {product_id}: "
                    f"{stocks_by_id.get(product_id)} != {INITIAL_STOCK}"
                )

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
