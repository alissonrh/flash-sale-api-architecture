import os

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from fastapi import FastAPI
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from sqlalchemy.engine import Engine
from opentelemetry.instrumentation.pika import PikaInstrumentor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
    OTLPSpanExporter,
)
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.sdk.trace.export import BatchSpanProcessor


_TRUE_VALUES = {"1", "true", "yes", "on"}


def otel_enabled() -> bool:
    return os.getenv("OTEL_ENABLED", "0").strip().lower() in _TRUE_VALUES

def otlp_export_enabled() -> bool:
    return (
        os.getenv("OTEL_EXPORTER_OTLP_ENABLED", "0")
        .strip()
        .lower()
        in _TRUE_VALUES
    )


def trace_sample_ratio() -> float:
    raw_ratio = os.getenv("OTEL_TRACE_SAMPLE_RATIO", "1.0").strip()

    try:
        ratio = float(raw_ratio)
    except ValueError as exc:
        raise ValueError(
            "OTEL_TRACE_SAMPLE_RATIO must be a number between 0 and 1"
        ) from exc

    if not 0 <= ratio <= 1:
        raise ValueError(
            "OTEL_TRACE_SAMPLE_RATIO must be between 0 and 1"
        )

    return ratio


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

    provider = TracerProvider(
        resource=resource,
        sampler=ParentBased(
            root=TraceIdRatioBased(trace_sample_ratio()),
        ),
    )
   

    if otlp_export_enabled():
        endpoint = os.getenv(
            "OTEL_EXPORTER_OTLP_ENDPOINT",
            "http://otel-collector:4317",
        )

        exporter = OTLPSpanExporter(
            endpoint=endpoint,
            insecure=True,
        )

        provider.add_span_processor(
            BatchSpanProcessor(exporter)
        )

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

def instrument_sqlalchemy(engine: Engine) -> bool:
    if not otel_enabled():
        return False

    SQLAlchemyInstrumentor().instrument(
        engine=engine,
        tracer_provider=trace.get_tracer_provider(),
    )

    return True

def instrument_pika() -> bool:
    if not otel_enabled():
        return False

    PikaInstrumentor().instrument(
        tracer_provider=trace.get_tracer_provider(),
    )

    return True
