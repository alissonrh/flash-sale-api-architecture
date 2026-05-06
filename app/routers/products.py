from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.product import Product, ProductListResponse, ProductStock
from app.services.product_service import (
    find_product_by_id,
    get_product_stock_data,
    list_all_products,
)

router = APIRouter(prefix="/products", tags=["products"])


@router.get("", response_model=ProductListResponse)
def list_products(db: Session = Depends(get_db)):
    return list_all_products(db)


@router.get("/{product_id}", response_model=Product)
def get_product(product_id: int, db: Session = Depends(get_db)):
    return find_product_by_id(db, product_id)


@router.get("/{product_id}/stock", response_model=ProductStock)
def get_product_stock(product_id: int, db: Session = Depends(get_db)):
    return get_product_stock_data(db, product_id)