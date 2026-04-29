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