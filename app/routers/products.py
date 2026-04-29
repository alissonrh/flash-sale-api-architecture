from fastapi import APIRouter, HTTPException

from app.data.fake_db import products
from app.schemas.product import Product, ProductStock

router = APIRouter(prefix="/products", tags=["products"])


@router.get("", response_model=dict)
def list_products():
    return {"items": products, "total": len(products)}


@router.get("/{product_id}", response_model=Product)
def get_product(product_id: int):
    for product in products:
        if product["id"] == product_id:
            return product
    raise HTTPException(status_code=404, detail="Produto não encontrado")


@router.get("/{product_id}/stock", response_model=ProductStock)
def get_product_stock(product_id: int):
    for product in products:
        if product["id"] == product_id:
            return {
                "id": product["id"],
                "name": product["name"],
                "stock": product["stock"],
            }
    raise HTTPException(status_code=404, detail="Produto não encontrado")