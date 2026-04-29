from fastapi import APIRouter, HTTPException

from app.data import fake_db
from app.data.fake_db import orders, products
from app.schemas.order import CheckoutRequest, Order

router = APIRouter(tags=["orders"])


@router.post("/checkout", status_code=201)
def checkout(payload: CheckoutRequest):
    product = None
    for item in products:
        if item["id"] == payload.product_id:
            product = item
            break

    if product is None:
        raise HTTPException(status_code=404, detail="Produto não encontrado")

    if payload.quantity > product["stock"]:
        raise HTTPException(status_code=400, detail="Estoque insuficiente")

    total = round(product["price"] * payload.quantity, 2)

    order = {
        "id": fake_db.next_order_id,
        "product_id": product["id"],
        "product_name": product["name"],
        "quantity": payload.quantity,
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


@router.get("/orders", response_model=dict)
def list_orders():
    return {"items": orders, "total": len(orders)}


@router.get("/orders/{order_id}", response_model=Order)
def get_order(order_id: int):
    for order in orders:
        if order["id"] == order_id:
            return order
    raise HTTPException(status_code=404, detail="Pedido não encontrado")