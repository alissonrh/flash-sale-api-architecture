import json
import os
from datetime import datetime, timezone


def diagnostic_logs_enabled() -> bool:
    return os.getenv("DIAGNOSTIC_LOGS", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def log_event(
    *,
    component: str,
    event: str,
    message: str,
    level: str = "INFO",
    **fields,
) -> None:
    if not diagnostic_logs_enabled():
        return

    payload = {
        "timestamp": datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
        "level": level.upper(),
        "component": component,
        "event": event,
        "message": message,
        **fields,
    }

    print(json.dumps(payload, ensure_ascii=False), flush=True)