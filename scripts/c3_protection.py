"""Build the C3 protection summary from collected experiment evidence."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Sequence


ORDER_STATUSES = ("COMPLETED", "PENDING", "PROCESSING", "FAILED")


def metric_count(metrics: dict[str, Any], name: str) -> int:
    metric = metrics.get(name) or {}
    return int(metric.get("count", 0))


def build_summary(
    k6: dict[str, Any],
    database: dict[str, Any],
    queue: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    metrics = k6.get("metrics") or {}
    counts = {
        "http_reqs": metric_count(metrics, "http_reqs"),
        "requests_started": metric_count(metrics, "requests_started"),
        "responses_2xx": metric_count(metrics, "responses_2xx"),
        "responses_429": metric_count(metrics, "responses_429"),
        "responses_5xx": metric_count(metrics, "responses_5xx"),
        "connection_errors": metric_count(metrics, "connection_errors"),
        "unexpected_statuses": metric_count(metrics, "unexpected_statuses"),
        "unexpected_failures": metric_count(metrics, "unexpected_failures"),
    }
    classified = sum(
        counts[name]
        for name in (
            "responses_2xx",
            "responses_429",
            "responses_5xx",
            "connection_errors",
            "unexpected_statuses",
        )
    )
    calculated_unexpected = (
        counts["responses_5xx"]
        + counts["connection_errors"]
        + counts["unexpected_statuses"]
    )
    classification_invariant_ok = (
        counts["requests_started"] == classified
        and counts["http_reqs"] == counts["requests_started"]
        and metric_count(metrics, "throughput_total") == counts["requests_started"]
        and counts["unexpected_failures"] == calculated_unexpected
    )

    published_before = int(queue[0]["publish_total"]) if queue else 0
    published_after = int(queue[-1]["publish_total"]) if queue else 0
    published_delta = max(0, published_after - published_before)

    accepted = counts["responses_2xx"]
    total_orders = int(database.get("total_orders", 0))
    database_statuses = database.get("orders_by_status") or {}
    orders_by_status = {
        status: int(database_statuses.get(status, 0)) for status in ORDER_STATUSES
    }
    database_total_matches_responses_2xx = total_orders == accepted
    database_statuses_reconcile = sum(orders_by_status.values()) == total_orders
    accepted_flow_completed = (
        orders_by_status["COMPLETED"] == accepted
        and orders_by_status["PENDING"] == 0
        and orders_by_status["PROCESSING"] == 0
        and orders_by_status["FAILED"] == 0
    )
    database_authoritative_check_ok = (
        database_total_matches_responses_2xx
        and database_statuses_reconcile
        and accepted_flow_completed
    )
    rabbitmq_publish_counter_matches = published_delta == accepted

    requests = counts["requests_started"]
    rejected = counts["responses_429"]
    return {
        "classification": counts,
        "classified_total": classified,
        "classification_invariant": (
            "requests_started = 2xx + 429 + 5xx + connection_errors + other_statuses"
        ),
        "classification_invariant_ok": classification_invariant_ok,
        "responses_429_percentage": round(rejected / requests * 100.0, 6)
        if requests
        else 0.0,
        "throughput_per_second": {
            "total": (metrics.get("throughput_total") or {}).get("rate", 0),
            "accepted_2xx": (metrics.get("throughput_accepted_2xx") or {}).get(
                "rate", 0
            ),
            "rejected_429": (metrics.get("throughput_rejected_429") or {}).get(
                "rate", 0
            ),
        },
        "rate_limit_headers_present_429": (
            metrics.get("rate_limit_headers_present_429") or {}
        ).get("value"),
        "protection_working": rejected > 0,
        "side_effect_check": {
            "database_total_orders": total_orders,
            "database_orders_by_status": orders_by_status,
            "expected_from_responses_2xx": accepted,
            "database_total_matches_responses_2xx": (
                database_total_matches_responses_2xx
            ),
            "database_statuses_reconcile": database_statuses_reconcile,
            "accepted_flow_completed": accepted_flow_completed,
            "database_authoritative_check_ok": database_authoritative_check_ok,
            "rabbitmq_publish_delta": published_delta,
            "rabbitmq_publish_counter_matches": rabbitmq_publish_counter_matches,
            "rejected_side_effects_absent": database_authoritative_check_ok,
        },
    }


def write_summary(
    k6_summary_file: Path,
    database_summary_file: Path,
    queue_file: Path,
    output_file: Path,
) -> None:
    with k6_summary_file.open(encoding="utf-8") as source:
        k6 = json.load(source)
    with database_summary_file.open(encoding="utf-8") as source:
        database = json.load(source)
    with queue_file.open(newline="", encoding="utf-8") as source:
        queue = list(csv.DictReader(source))

    summary = build_summary(k6, database, queue)
    with output_file.open("w", encoding="utf-8") as output:
        json.dump(summary, output, ensure_ascii=False, indent=2)
        output.write("\n")
