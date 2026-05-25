from datetime import datetime

from pydantic import BaseModel, Field


class CheckoutRequest(BaseModel):
    product_id: int
    quantity: int = Field(gt=0, description="Quantidade deve ser maior que zero")


class Order(BaseModel):
    id: int
    product_id: int
    product_name: str
    quantity: int
    unit_price: float
    total_price: float
    status: str
    created_at: datetime
    updated_at: datetime
    processed_at: datetime | None = None
    failure_reason: str | None = None


class OrderListResponse(BaseModel):
    items: list[Order]
    total: int


class CheckoutResponse(BaseModel):
    message: str
    order: Order