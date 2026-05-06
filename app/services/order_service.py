from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.order import OrderModel
from app.services.product_service import find_product_by_id


def _order_to_dict(order: OrderModel) -> dict:
    return {
        "id": order.id,
        "product_id": order.product_id,
        "product_name": order.product_name,
        "quantity": order.quantity,
        "unit_price": order.unit_price,
        "total_price": order.total_price,
        "status": order.status,
    }


def create_order(db: Session, product_id: int, quantity: int):
    product = find_product_by_id(db, product_id)

    if quantity > product["stock"]:
        raise HTTPException(status_code=400, detail="Estoque insuficiente")

    total = round(product["price"] * quantity, 2)

    order = OrderModel(
        product_id=product["id"],
        product_name=product["name"],
        quantity=quantity,
        unit_price=product["price"],
        total_price=total,
        status="PENDING",
    )

    db.add(order)
    db.commit()
    db.refresh(order)

    return {
        "message": "Pedido criado com sucesso",
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