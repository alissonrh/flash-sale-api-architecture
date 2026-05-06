from fastapi import APIRouter

from app.schemas.product import Product, ProductListResponse, ProductStock
from app.services.product_service import (
    find_product_by_id,
    get_product_stock_data,
    list_all_products,
)

router = APIRouter(prefix="/products", tags=["products"])


@router.get("", response_model=ProductListResponse)
def list_products():
    return list_all_products()


@router.get("/{product_id}", response_model=Product)
def get_product(product_id: int):
    return find_product_by_id(product_id)


@router.get("/{product_id}/stock", response_model=ProductStock)
def get_product_stock(product_id: int):
    return get_product_stock_data(product_id)