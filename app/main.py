from fastapi import FastAPI

from app.routers import health, orders, products

app = FastAPI(
    title="Flash Sale API",
    description="API de simulação para promoção relâmpago de produtos",
    version="0.1.0",
)

app.include_router(health.router)
app.include_router(products.router)
app.include_router(orders.router)