from fastapi import HTTPException

from app.data.fake_db import products


def list_all_products():
    return {"items": products, "total": len(products)}


def find_product_by_id(product_id: int):
    for product in products:
        if product["id"] == product_id:
            return product
    raise HTTPException(status_code=404, detail="Produto não encontrado")


def get_product_stock_data(product_id: int):
    product = find_product_by_id(product_id)
    return {
        "id": product["id"],
        "name": product["name"],
        "stock": product["stock"],
    }