from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.product import ProductModel


def _product_to_dict(product: ProductModel) -> dict:
    return {
        "id": product.id,
        "name": product.name,
        "price": product.price,
        "stock": product.stock,
        "flash_sale": product.flash_sale,
    }


def list_all_products(db: Session):
    result = db.execute(select(ProductModel).order_by(ProductModel.id))
    products = result.scalars().all()

    items = [_product_to_dict(product) for product in products]
    return {"items": items, "total": len(items)}


def find_product_by_id(db: Session, product_id: int):
    result = db.execute(
        select(ProductModel).where(ProductModel.id == product_id)
    )
    product = result.scalar_one_or_none()

    if product is None:
        raise HTTPException(status_code=404, detail="Produto não encontrado")

    return _product_to_dict(product)


def get_product_stock_data(db: Session, product_id: int):
    product = find_product_by_id(db, product_id)
    return {
        "id": product["id"],
        "name": product["name"],
        "stock": product["stock"],
    }