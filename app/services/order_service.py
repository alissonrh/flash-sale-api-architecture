from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.messaging.rabbitmq import CHECKOUT_QUEUE, publish_json_message
from app.models.order import OrderModel
from app.models.product import ProductModel
from app.utils.datetime import now_utc


ORDER_STATUS_PENDING = "PENDING"
ORDER_STATUS_PROCESSING = "PROCESSING"
ORDER_STATUS_COMPLETED = "COMPLETED"
ORDER_STATUS_FAILED = "FAILED"


def _order_to_dict(order: OrderModel) -> dict:
    return {
        "id": order.id,
        "product_id": order.product_id,
        "product_name": order.product_name,
        "quantity": order.quantity,
        "unit_price": order.unit_price,
        "total_price": order.total_price,
        "status": order.status,
        "created_at": order.created_at,
        "updated_at": order.updated_at,
        "processed_at": order.processed_at,
        "failure_reason": order.failure_reason,
    }


def create_order(db: Session, product_id: int, quantity: int):
    result = db.execute(
        select(ProductModel).where(ProductModel.id == product_id)
    )
    product = result.scalar_one_or_none()

    if product is None:
        raise HTTPException(status_code=404, detail="Produto não encontrado")

    total = round(product.price * quantity, 2)

    order = OrderModel(
        product_id=product.id,
        product_name=product.name,
        quantity=quantity,
        unit_price=product.price,
        total_price=total,
        status=ORDER_STATUS_PENDING,
        created_at=now_utc(),
        updated_at=now_utc(),
        processed_at=None,
        failure_reason=None,
    )

    try:
        db.add(order)
        db.commit()
        db.refresh(order)

        publish_json_message(
            queue_name=CHECKOUT_QUEUE,
            payload={
                "order_id": order.id,
            },
        )

    except Exception:
        db.rollback()
        raise

    return {
        "message": "Pedido recebido e enviado para processamento",
        "order": _order_to_dict(order),
    }


def list_all_orders(db: Session):
    result = db.execute(select(OrderModel).order_by(OrderModel.id))
    orders = result.scalars().all()

    items = [_order_to_dict(order) for order in orders]
    return {"items": items, "total": len(items)}


def find_order_by_id(db: Session, order_id: int):
    result = db.execute(
        select(OrderModel).where(OrderModel.id == order_id)
    )
    order = result.scalar_one_or_none()

    if order is None:
        raise HTTPException(status_code=404, detail="Pedido não encontrado")

    return _order_to_dict(order)