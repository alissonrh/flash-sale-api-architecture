import os

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from fastapi import FastAPI
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor


_TRUE_VALUES = {"1", "true", "yes", "on"}


def otel_enabled() -> bool:
    return os.getenv("OTEL_ENABLED", "0").strip().lower() in _TRUE_VALUES


def configure_tracing() -> bool:
    if not otel_enabled():
        return False

    service_name = os.getenv(
        "OTEL_SERVICE_NAME",
        "flash-sale-api",
    )

    resource = Resource.create(
        {
            "service.name": service_name,
        }
    )

    provider = TracerProvider(resource=resource)
    trace.set_tracer_provider(provider)

    return True

def instrument_fastapi(app: FastAPI) -> bool:
    if not otel_enabled():
        return False

    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=trace.get_tracer_provider(),
    )

    return True