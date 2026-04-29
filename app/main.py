from fastapi import FastAPI

app = FastAPI(
    title="Flash Sale API",
    description="API de simulação para promoção relâmpago de produtos",
    version="0.1.0",
)

@app.get("/")
def root():
    return {
        "message": "Flash Sale API no ar",
        "docs": "/docs",
        "health": "/health",
    }

@app.get("/health")
def health():
    return {"status": "ok"}