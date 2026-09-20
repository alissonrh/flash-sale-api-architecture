import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from c3_protection import build_summary  # noqa: E402


def k6_summary(responses_2xx: int) -> dict:
    responses_429 = 91
    requests = responses_2xx + responses_429
    return {
        "metrics": {
            "http_reqs": {"count": requests},
            "requests_started": {"count": requests},
            "responses_2xx": {"count": responses_2xx},
            "responses_429": {"count": responses_429},
            "responses_5xx": {"count": 0},
            "connection_errors": {"count": 0},
            "unexpected_statuses": {"count": 0},
            "unexpected_failures": {"count": 0},
            "throughput_total": {"count": requests, "rate": 20},
            "throughput_accepted_2xx": {"rate": 19},
            "throughput_rejected_429": {"rate": 1},
        }
    }


def database_summary(
    *, completed: int, pending: int = 0, processing: int = 0, failed: int = 0,
    total_orders: int | None = None,
) -> dict:
    statuses = {
        "COMPLETED": completed,
        "PENDING": pending,
        "PROCESSING": processing,
        "FAILED": failed,
    }
    return {
        "total_orders": sum(statuses.values()) if total_orders is None else total_orders,
        "orders_by_status": statuses,
    }


def queue_samples(published_delta: int) -> list[dict[str, str]]:
    return [{"publish_total": "100"}, {"publish_total": str(100 + published_delta)}]


class C3ProtectionValidityTest(unittest.TestCase):
    def assert_database_check(
        self, responses_2xx: int, database: dict, expected: bool
    ) -> dict:
        summary = build_summary(
            k6_summary(responses_2xx),
            database,
            queue_samples(responses_2xx),
        )
        check = summary["side_effect_check"]
        self.assertEqual(check["database_authoritative_check_ok"], expected)
        self.assertEqual(check["rejected_side_effects_absent"], expected)
        return check

    def test_lower_rabbitmq_counter_is_diagnostic_only(self) -> None:
        responses_2xx = 1518
        summary = build_summary(
            k6_summary(responses_2xx),
            database_summary(completed=responses_2xx),
            queue_samples(1432),
        )

        check = summary["side_effect_check"]
        self.assertTrue(check["database_authoritative_check_ok"])
        self.assertTrue(check["rejected_side_effects_absent"])
        self.assertEqual(check["rabbitmq_publish_delta"], 1432)
        self.assertFalse(check["rabbitmq_publish_counter_matches"])

    def test_more_orders_than_2xx_is_invalid(self) -> None:
        self.assert_database_check(10, database_summary(completed=11), False)

    def test_fewer_orders_than_2xx_is_invalid(self) -> None:
        self.assert_database_check(10, database_summary(completed=9), False)

    def test_non_completed_orders_are_invalid(self) -> None:
        for status in ("pending", "processing", "failed"):
            with self.subTest(status=status):
                values = {"completed": 9, status: 1}
                self.assert_database_check(10, database_summary(**values), False)

    def test_status_totals_must_reconcile_with_total_orders(self) -> None:
        database = database_summary(completed=10, total_orders=11)
        check = self.assert_database_check(10, database, False)
        self.assertFalse(check["database_statuses_reconcile"])


if __name__ == "__main__":
    unittest.main()
