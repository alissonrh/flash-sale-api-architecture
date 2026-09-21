import csv
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

COLLECTOR_PATH = ROOT / "scripts/collect-k8s-experiment-metrics.py"
SPEC = importlib.util.spec_from_file_location("collect_k8s_metrics", COLLECTOR_PATH)
assert SPEC is not None and SPEC.loader is not None
COLLECTOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(COLLECTOR)
calculate_restart_deltas = COLLECTOR.calculate_restart_deltas
validate_hpa_presence = COLLECTOR.validate_hpa_presence


def pod(name: str, uid: str, app: str, restarts: int) -> dict:
    return {
        "metadata": {"name": name, "uid": uid, "labels": {"app": app}},
        "status": {
            "containerStatuses": [
                {"name": app, "restartCount": restarts},
            ]
        },
    }


class C4ScenarioContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bootstrap = (ROOT / "scripts/bootstrap-k8s.sh").read_text(
            encoding="utf-8"
        )
        cls.runner = (ROOT / "scripts/run-k8s-experiment.sh").read_text(
            encoding="utf-8"
        )
        cls.load_test = (ROOT / "load-tests/kubernetes_checkout.js").read_text(
            encoding="utf-8"
        )

    def test_c4_combines_the_frozen_manifests(self) -> None:
        c4_branch = self.bootstrap.split(
            'elif [[ "$SCENARIO" == "c4" ]]', maxsplit=1
        )[1].split("\n  fi", maxsplit=1)[0]
        for manifest in (
            "k8s/api-hpa.yaml",
            "k8s/gateway-configmap.yaml",
            "k8s/gateway-service.yaml",
            "k8s/gateway-deployment.yaml",
        ):
            self.assertIn(manifest, c4_branch)

    def test_c4_uses_gateway_and_frozen_load_profile(self) -> None:
        self.assertIn("SCENARIO === 'c4'", self.load_test)
        self.assertIn("'http://gateway:8000'", self.load_test)
        self.assertIn('STAGE_2_RATE=22', self.runner)
        self.assertIn('STAGE_3_RATE=22', self.runner)

    def test_preflight_combines_hpa_kong_headers_and_keda_checks(self) -> None:
        preflight = self.runner.split("run_preflight()", maxsplit=1)[1].split(
            "prepare_data()", maxsplit=1
        )[0]
        self.assertIn("assert_keda_disabled", preflight)
        self.assertIn("validate_scenario_autoscaling", preflight)
        self.assertIn("wait_for_hpa_cpu_metrics", preflight)
        self.assertIn("wait_for_api_baseline", preflight)
        self.assertIn("validate_gateway_configuration", preflight)
        self.assertIn("ratelimit-limit", self.runner)
        self.assertNotIn("kong config parse", self.bootstrap)

    def test_c4_metadata_contains_both_treatments(self) -> None:
        self.assertIn('"autoscaling": {', self.runner)
        self.assertIn('"rate_limiting": {', self.runner)
        self.assertIn('{"c3", "c4"}', self.runner)
        self.assertIn('"fault_tolerant": False', self.runner)
        self.assertIn('"first_desired_above_one_at"', self.runner)
        self.assertIn('"first_multiple_api_pods_observed_at"', self.runner)
        self.assertIn('scenario != "c4"', self.runner)
        self.assertIn('get("status") == "stable"', self.runner)

    def test_api_log_collection_is_multipod_for_c2_and_c4(self) -> None:
        self.assertIn(
            'if [[ "$SCENARIO" == "c2" || "$SCENARIO" == "c4" ]]',
            self.runner,
        )
        for option in (
            "--all-containers=true",
            "--prefix=true",
            "--tail=-1",
            "--max-log-requests=10",
            '--since-time="$COLLECTION_STARTED_AT"',
        ):
            self.assertIn(option, self.runner)

    def test_hpa_presence_contract_preserves_previous_scenarios(self) -> None:
        hpa = {"metadata": {"name": "api-hpa"}}
        self.assertEqual(validate_hpa_presence({"items": [hpa]}, "c2"), [hpa])
        self.assertEqual(validate_hpa_presence({"items": [hpa]}, "c4"), [hpa])
        self.assertEqual(validate_hpa_presence({"items": []}, "c1"), [])
        self.assertEqual(validate_hpa_presence({"items": []}, "c3"), [])
        with self.assertRaises(RuntimeError):
            validate_hpa_presence({"items": [hpa]}, "c3")

    def test_scale_down_is_not_counted_as_restart(self) -> None:
        before = {"pods": {"items": [pod("api-old", "uid-old", "api", 0)]}}
        after = {"pods": {"items": [pod("api-new", "uid-new", "api", 0)]}}

        with tempfile.TemporaryDirectory() as directory:
            samples = Path(directory) / "pods.csv"
            with samples.open("w", newline="", encoding="utf-8") as output:
                writer = csv.DictWriter(
                    output,
                    fieldnames=("pod", "pod_uid", "app", "container_restarts"),
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "pod": "api-old",
                        "pod_uid": "uid-old",
                        "app": "api",
                        "container_restarts": '{"api": 0}',
                    }
                )
                writer.writerow(
                    {
                        "pod": "api-new",
                        "pod_uid": "uid-new",
                        "app": "api",
                        "container_restarts": '{"api": 0}',
                    }
                )

            self.assertEqual(
                calculate_restart_deltas(before, after, str(samples)),
                [],
            )

    def test_real_restart_of_scaled_pod_is_counted(self) -> None:
        before = {"pods": {"items": []}}
        after = {"pods": {"items": [pod("api-new", "uid-new", "api", 1)]}}
        deltas = calculate_restart_deltas(before, after)
        self.assertEqual(len(deltas), 1)
        self.assertEqual(deltas[0]["restart_delta"], 1)
        self.assertEqual(deltas[0]["pod_uid"], "uid-new")

    def test_c4_summary_aggregates_api_and_gateway_resources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            resources = root / "resources.csv"
            with resources.open("w", newline="", encoding="utf-8") as output:
                writer = csv.DictWriter(
                    output,
                    fieldnames=(
                        "timestamp",
                        "pod",
                        "container",
                        "app",
                        "cpu_cores",
                        "memory_mib",
                    ),
                )
                writer.writeheader()
                writer.writerows(
                    [
                        {"timestamp": "2026-01-01T00:00:00Z", "pod": "api-a", "container": "api", "app": "api", "cpu_cores": "0.1", "memory_mib": "10"},
                        {"timestamp": "2026-01-01T00:00:00Z", "pod": "api-b", "container": "api", "app": "api", "cpu_cores": "0.2", "memory_mib": "20"},
                        {"timestamp": "2026-01-01T00:00:00Z", "pod": "gateway-a", "container": "gateway", "app": "gateway", "cpu_cores": "0.05", "memory_mib": "30"},
                    ]
                )

            pods = root / "pods.csv"
            with pods.open("w", newline="", encoding="utf-8") as output:
                writer = csv.DictWriter(
                    output,
                    fieldnames=(
                        "timestamp",
                        "pod",
                        "pod_uid",
                        "app",
                        "container_restarts",
                        "api_pod_count",
                        "worker_pod_count",
                        "gateway_pod_count",
                        "all_nodes_ready",
                    ),
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "timestamp": "2026-01-01T00:00:00Z",
                        "pod": "api-a",
                        "pod_uid": "api-a-uid",
                        "app": "api",
                        "container_restarts": '{"api": 0}',
                        "api_pod_count": "2",
                        "worker_pod_count": "1",
                        "gateway_pod_count": "1",
                        "all_nodes_ready": "true",
                    }
                )

            hpa = root / "hpa.csv"
            with hpa.open("w", newline="", encoding="utf-8") as output:
                writer = csv.DictWriter(output, fieldnames=COLLECTOR.HPA_FIELDS)
                writer.writeheader()
                row = {field: "" for field in COLLECTOR.HPA_FIELDS}
                row.update(
                    {
                        "timestamp": "2026-01-01T00:00:00Z",
                        "hpa": "api-hpa",
                        "target_kind": "Deployment",
                        "target_name": "api",
                        "current_replicas": "2",
                        "desired_replicas": "3",
                        "min_replicas": "1",
                        "max_replicas": "5",
                        "current_cpu_utilization": "90",
                        "target_cpu_utilization": "70",
                        "scale_up_stabilization_seconds": "0",
                        "scale_up_select_policy": "Max",
                        "scale_up_policies": "[]",
                        "scale_down_stabilization_seconds": "120",
                        "scale_down_select_policy": "Min",
                        "scale_down_policies": "[]",
                    }
                )
                writer.writerow(row)

            queue = root / "queue.csv"
            with queue.open("w", newline="", encoding="utf-8") as output:
                writer = csv.DictWriter(
                    output,
                    fieldnames=(
                        "timestamp",
                        "messages_ready",
                        "messages_unacknowledged",
                        "messages",
                        "consumers",
                        "publish_total",
                        "confirm_total",
                    ),
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "timestamp": "2026-01-01T00:00:00Z",
                        "messages_ready": "0",
                        "messages_unacknowledged": "0",
                        "messages": "0",
                        "consumers": "1",
                        "publish_total": "0",
                        "confirm_total": "0",
                    }
                )

            before = root / "before.json"
            after = root / "after.json"
            snapshot = {"pods": {"items": []}}
            before.write_text(json.dumps(snapshot), encoding="utf-8")
            after.write_text(json.dumps(snapshot), encoding="utf-8")
            summary_path = root / "summary.json"
            COLLECTOR.summarize_collection(
                SimpleNamespace(
                    scenario="c4",
                    resources=str(resources),
                    pods=str(pods),
                    hpa=str(hpa),
                    queue=str(queue),
                    before=str(before),
                    after=str(after),
                    output=str(summary_path),
                )
            )
            summary = json.loads(summary_path.read_text(encoding="utf-8"))

        self.assertEqual(summary["resource_aggregates"]["api"]["max_cpu_cores"], 0.3)
        self.assertEqual(summary["resource_aggregates"]["api"]["max_memory_mib"], 30.0)
        self.assertEqual(summary["resource_aggregates"]["gateway"]["max_cpu_cores"], 0.05)
        self.assertEqual(summary["hpa_current_replicas_max"], 2)
        self.assertEqual(summary["hpa_desired_replicas_max"], 3)


if __name__ == "__main__":
    unittest.main()
