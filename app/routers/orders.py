import time

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.order import CheckoutRequest, CheckoutResponse, Order, OrderListResponse
from app.services.checkout_sync_service import process_checkout_sync
from app.services.order_service import create_order, find_order_by_id, list_all_orders
from app.utils.diagnostics import log_event
from uuid import uuid4

router = APIRouter(tags=["orders"])

@router.post("/checkout", status_code=201, response_model=CheckoutResponse)
def checkout(
    payload: CheckoutRequest,
    response: Response,
    db: Session = Depends(get_db),
):
    started_at = time.perf_counter()
    correlation_id = str(uuid4())

    response.headers["X-Correlation-ID"] = correlation_id

    log_event(
        component="api",
        event="checkout_received",
        message="Async checkout request received",
        correlation_id=correlation_id,
        product_id=payload.product_id,
        quantity=payload.quantity,
    )

    try:
        return create_order(
            db=db,
            product_id=payload.product_id,
            quantity=payload.quantity,
            correlation_id=correlation_id,
        )
    finally:
        log_event(
            component="api",
            event="checkout_async_http",
            message="Async checkout HTTP request finished",
            correlation_id=correlation_id,
            product_id=payload.product_id,
            quantity=payload.quantity,
            total_ms=round(
                (time.perf_counter() - started_at) * 1000,
                3,
            ),
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
