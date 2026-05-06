from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.order import CheckoutRequest, CheckoutResponse, Order, OrderListResponse
from app.services.order_service import create_order, find_order_by_id, list_all_orders

router = APIRouter(tags=["orders"])


@router.post("/checkout", status_code=201, response_model=CheckoutResponse)
def checkout(payload: CheckoutRequest, db: Session = Depends(get_db)):
    return create_order(db=db, product_id=payload.product_id, quantity=payload.quantity)


@router.get("/orders", response_model=OrderListResponse)
def list_orders(db: Session = Depends(get_db)):
    return list_all_orders(db)


@router.get("/orders/{order_id}", response_model=Order)
def get_order(order_id: int, db: Session = Depends(get_db)):
    return find_order_by_id(db, order_id)