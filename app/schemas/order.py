from pydantic import BaseModel, Field


class CheckoutRequest(BaseModel):
    product_id: int
    quantity: int = Field(gt=0, description="Quantidade deve ser maior que zero")