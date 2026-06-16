import json
import time

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.order import CheckoutRequest, CheckoutResponse, Order, OrderListResponse
from app.services.checkout_sync_service import process_checkout_sync
from app.services.order_service import create_order, find_order_by_id, list_all_orders
from app.utils.diagnostics import diagnostic_logs_enabled

router = APIRouter(tags=["orders"])


def _log_json(payload: dict):
    if not diagnostic_logs_enabled():
        return

    print(json.dumps(payload, ensure_ascii=False), flush=True)


@router.post("/checkout", status_code=201, response_model=CheckoutResponse)
def checkout(payload: CheckoutRequest, db: Session = Depends(get_db)):
    started_at = time.perf_counter()

    try:
        return create_order(db=db, product_id=payload.product_id, quantity=payload.quantity)
    finally:
        _log_json(
            {
                "event": "checkout_async_http",
                "product_id": payload.product_id,
                "quantity": payload.quantity,
                "total_ms": round((time.perf_counter() - started_at) * 1000, 3),
            }
        )


@router.post("/checkout-sync", status_code=201, response_model=Order)
def checkout_sync(payload: CheckoutRequest, db: Session = Depends(get_db)):
    return process_checkout_sync(
        db=db,
        product_id=payload.product_id,
        quantity=payload.quantity,
    )


@router.get("/orders", response_model=OrderListResponse)
def list_orders(db: Session = Depends(get_db)):
    return list_all_orders(db)


@router.get("/orders/{order_id}", response_model=Order)
def get_order(order_id: int, db: Session = Depends(get_db)):
    return find_order_by_id(db, order_id)
