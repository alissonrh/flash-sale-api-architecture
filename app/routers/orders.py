from fastapi import APIRouter

from app.schemas.order import CheckoutRequest, CheckoutResponse, Order, OrderListResponse
from app.services.order_service import create_order, find_order_by_id, list_all_orders

router = APIRouter(tags=["orders"])


@router.post("/checkout", status_code=201, response_model=CheckoutResponse)
def checkout(payload: CheckoutRequest):
    return create_order(product_id=payload.product_id, quantity=payload.quantity)


@router.get("/orders", response_model=OrderListResponse)
def list_orders():
    return list_all_orders()


@router.get("/orders/{order_id}", response_model=Order)
def get_order(order_id: int):
    return find_order_by_id(order_id)