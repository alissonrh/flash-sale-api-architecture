from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.data import fake_db
from app.data.fake_db import orders
from app.services.product_service import find_product_by_id


def create_order(db: Session, product_id: int, quantity: int):
    product = find_product_by_id(db, product_id)

    if quantity > product["stock"]:
        raise HTTPException(status_code=400, detail="Estoque insuficiente")

    total = round(product["price"] * quantity, 2)

    order = {
        "id": fake_db.next_order_id,
        "product_id": product["id"],
        "product_name": product["name"],
        "quantity": quantity,
        "unit_price": product["price"],
        "total_price": total,
        "status": "PENDING",
    }

    orders.append(order)
    fake_db.next_order_id += 1

    return {
        "message": "Pedido criado com sucesso",
        "order": order,
    }


def list_all_orders():
    return {"items": orders, "total": len(orders)}


def find_order_by_id(order_id: int):
    for order in orders:
        if order["id"] == order_id:
            return order
    raise HTTPException(status_code=404, detail="Pedido não encontrado")