from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator

from app.observability.tracing import (
    configure_tracing,
    instrument_fastapi,
)
from app.routers import health, orders, products

configure_tracing()

app = FastAPI(
    title="Flash Sale API",
    description="API de simulação para promoção relâmpago de produtos",
    version="0.1.0",
)

app.include_router(health.router)
app.include_router(products.router)
app.include_router(orders.router)

instrument_fastapi(app)

Instrumentator().instrument(app).expose(app) # Adiciona métricas Prometheus para monitoramento /metrics