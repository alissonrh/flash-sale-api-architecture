from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator

from app.db.database import engine
from app.observability.tracing import (
    configure_tracing,
    instrument_fastapi,
    instrument_pika,
    instrument_sqlalchemy,
)
from app.routers import health, orders, products


configure_tracing()
instrument_sqlalchemy(engine)
instrument_pika()

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