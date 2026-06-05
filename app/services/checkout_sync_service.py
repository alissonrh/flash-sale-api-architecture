from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.order import OrderModel
from app.models.product import ProductModel
from app.utils.datetime import now_utc


def process_checkout_sync(
    db: Session,
    product_id: int,
    quantity: int,
) -> OrderModel:
    if quantity <= 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A quantidade deve ser maior que zero.",
        )

    try:
        product = db.execute(
            select(ProductModel)
            .where(ProductModel.id == product_id)
            .with_for_update()
        ).scalar_one_or_none()

        if product is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Produto não encontrado.",
            )

        if product.stock < quantity:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Estoque insuficiente.",
            )

        product.stock -= quantity

        unit_price = product.price
        total_price = unit_price * quantity

        order = OrderModel(
            product_id=product.id,
            product_name=product.name,
            quantity=quantity,
            unit_price=unit_price,
            total_price=total_price,
            status="CONFIRMED",
            processed_at=now_utc(),
        )

        db.add(order)
        db.commit()
        db.refresh(order)

        return order

    except HTTPException:
        db.rollback()
        raise

    except Exception as error:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro ao processar checkout síncrono: {str(error)}",
        )