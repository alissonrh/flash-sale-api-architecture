import os


def diagnostic_logs_enabled() -> bool:
    return os.getenv("DIAGNOSTIC_LOGS", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
