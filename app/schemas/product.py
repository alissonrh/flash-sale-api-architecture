from pydantic import BaseModel


class Product(BaseModel):
    id: int
    name: str
    price: float
    stock: int
    flash_sale: bool


class ProductStock(BaseModel):
    id: int
    name: str
    stock: int