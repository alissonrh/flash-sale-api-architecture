import json
import os
from datetime import datetime, timezone
from opentelemetry import trace


def diagnostic_logs_enabled() -> bool:
    return os.getenv("DIAGNOSTIC_LOGS", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

def _current_trace_fields() -> dict:
    span_context = trace.get_current_span().get_span_context()

    if not span_context.is_valid:
        return {}

    return {
        "trace_id": format(span_context.trace_id, "032x"),
        "span_id": format(span_context.span_id, "016x"),
    }

def log_event(
    *,
    component: str,
    event: str,
    message: str,
    level: str = "INFO",
    **fields,
) -> None:
    normalized_level = level.upper()

    if not diagnostic_logs_enabled() and normalized_level != "ERROR":
        return

    payload = {
        "timestamp": datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
        "level": normalized_level,
        "component": component,
        "event": event,
        "message": message,
        **_current_trace_fields(),
        **fields,
    }

    print(json.dumps(payload, ensure_ascii=False), flush=True)
