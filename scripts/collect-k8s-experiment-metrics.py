#!/usr/bin/env python3
import argparse
import base64
import csv
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


PROMETHEUS_QUERIES = [
    "process_resident_memory_bytes",
    "process_virtual_memory_bytes",
    "rate(process_cpu_seconds_total[15s])",
    'http_requests_total{handler="/checkout",method="POST"}',
    'rate(http_requests_total{handler="/checkout",method="POST"}[15s])',
    'http_request_duration_seconds_count{handler="/checkout",method="POST"}',
    'http_request_duration_seconds_sum{handler="/checkout",method="POST"}',
]


def utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def request_json(
    url: str,
    auth: tuple[str, str] | None = None,
    timeout: int = 10,
) -> dict:
    headers = {}
    if auth:
        token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        headers["Authorization"] = f"Basic {token}"

    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def kubectl_json(*arguments: str) -> dict:
    completed = subprocess.run(
        ["kubectl", *arguments],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return json.loads(completed.stdout)


def parse_cpu_cores(quantity: str) -> float:
    value = str(quantity).strip()
    suffixes = {
        "n": 1e-9,
        "u": 1e-6,
        "m": 1e-3,
    }

    for suffix, multiplier in suffixes.items():
        if value.endswith(suffix):
            return float(value[: -len(suffix)]) * multiplier

    return float(value)


def parse_memory_mib(quantity: str) -> float:
    value = str(quantity).strip()
    match = re.fullmatch(r"([+-]?[0-9]*\.?[0-9]+)([A-Za-z]*)", value)
    if match is None:
        raise ValueError(f"quantidade de memoria invalida: {quantity}")

    number = float(match.group(1))
    suffix = match.group(2)
    binary_bytes = {
        "Ki": 1024,
        "Mi": 1024**2,
        "Gi": 1024**3,
        "Ti": 1024**4,
        "Pi": 1024**5,
        "Ei": 1024**6,
    }
    decimal_bytes = {
        "": 1,
        "k": 1000,
        "K": 1000,
        "M": 1000**2,
        "G": 1000**3,
        "T": 1000**4,
        "P": 1000**5,
        "E": 1000**6,
    }

    if suffix in binary_bytes:
        bytes_value = number * binary_bytes[suffix]
    elif suffix in decimal_bytes:
        bytes_value = number * decimal_bytes[suffix]
    else:
        raise ValueError(f"unidade de memoria nao suportada: {quantity}")

    return bytes_value / (1024**2)


def rabbitmq_auth() -> tuple[str, str]:
    username = os.environ.get("RABBITMQ_USERNAME")
    password = os.environ.get("RABBITMQ_PASSWORD")
    if not username or not password:
        raise RuntimeError(
            "RABBITMQ_USERNAME e RABBITMQ_PASSWORD precisam estar definidos"
        )
    return username, password


def rabbitmq_queue_snapshot(url: str) -> dict:
    data = request_json(url, rabbitmq_auth())
    message_stats = data.get("message_stats") or {}
    return {
        "timestamp": utc_now(),
        "messages_ready": int(data.get("messages_ready", 0)),
        "messages_unacknowledged": int(
            data.get("messages_unacknowledged", 0)
        ),
        "messages": int(data.get("messages", 0)),
        "consumers": int(data.get("consumers", 0)),
        "publish_total": int(message_stats.get("publish", 0)),
        "confirm_total": int(message_stats.get("confirm", 0)),
    }


def ready_condition(item: dict) -> bool:
    return any(
        condition.get("type") == "Ready" and condition.get("status") == "True"
        for condition in item.get("status", {}).get("conditions", [])
    )


def container_state(status: dict) -> str:
    state = status.get("state") or {}
    return next(iter(state), "unknown")


def write_resource_samples(
    writer: csv.DictWriter,
    timestamp: str,
    metrics: dict,
) -> None:
    for pod in metrics.get("items", []):
        pod_name = pod.get("metadata", {}).get("name", "")
        for container in pod.get("containers", []):
            usage = container.get("usage") or {}
            writer.writerow(
                {
                    "timestamp": timestamp,
                    "pod": pod_name,
                    "container": container.get("name", ""),
                    "cpu_cores": round(
                        parse_cpu_cores(usage.get("cpu", "0")),
                        9,
                    ),
                    "memory_mib": round(
                        parse_memory_mib(usage.get("memory", "0")),
                        6,
                    ),
                }
            )


def write_pod_samples(
    writer: csv.DictWriter,
    timestamp: str,
    pods: dict,
    nodes: dict,
) -> None:
    node_states = {
        item.get("metadata", {}).get("name", ""): ready_condition(item)
        for item in nodes.get("items", [])
    }
    all_nodes_ready = bool(node_states) and all(node_states.values())
    active_pods = [
        pod
        for pod in pods.get("items", [])
        if not pod.get("metadata", {}).get("deletionTimestamp")
    ]
    api_pod_count = sum(
        pod.get("metadata", {}).get("labels", {}).get("app") == "api"
        for pod in active_pods
    )

    for pod in active_pods:
        metadata = pod.get("metadata") or {}
        status = pod.get("status") or {}
        container_statuses = status.get("containerStatuses") or []
        restarts = {
            item.get("name", ""): int(item.get("restartCount", 0))
            for item in container_statuses
        }
        states = {
            item.get("name", ""): container_state(item)
            for item in container_statuses
        }
        ready_containers = sum(bool(item.get("ready")) for item in container_statuses)

        writer.writerow(
            {
                "timestamp": timestamp,
                "pod": metadata.get("name", ""),
                "app": (metadata.get("labels") or {}).get("app", ""),
                "phase": status.get("phase", ""),
                "pod_ready": ready_condition(pod),
                "ready_containers": ready_containers,
                "total_containers": len(container_statuses),
                "restart_count": sum(restarts.values()),
                "container_restarts": json.dumps(restarts, sort_keys=True),
                "container_states": json.dumps(states, sort_keys=True),
                "api_pod_count": api_pod_count,
                "node": (pod.get("spec") or {}).get("nodeName", ""),
                "all_nodes_ready": all_nodes_ready,
                "node_states": json.dumps(node_states, sort_keys=True),
            }
        )


def record_error(error_file, source: str, exception: Exception) -> None:
    error_file.write(
        json.dumps(
            {
                "timestamp": utc_now(),
                "source": source,
                "error": str(exception),
            },
            ensure_ascii=False,
        )
        + "\n"
    )
    error_file.flush()
    print(f"Erro de coleta em {source}: {exception}", file=sys.stderr, flush=True)


def collect(args: argparse.Namespace) -> None:
    resources_path = Path(args.resources_output)
    pods_path = Path(args.pods_output)
    queue_path = Path(args.queue_output)
    errors_path = Path(args.errors_output)
    stop_file = Path(args.stop_file)

    resources_path.parent.mkdir(parents=True, exist_ok=True)
    pods_path.parent.mkdir(parents=True, exist_ok=True)
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    errors_path.parent.mkdir(parents=True, exist_ok=True)

    resource_fields = [
        "timestamp",
        "pod",
        "container",
        "cpu_cores",
        "memory_mib",
    ]
    pod_fields = [
        "timestamp",
        "pod",
        "app",
        "phase",
        "pod_ready",
        "ready_containers",
        "total_containers",
        "restart_count",
        "container_restarts",
        "container_states",
        "api_pod_count",
        "node",
        "all_nodes_ready",
        "node_states",
    ]
    queue_fields = [
        "timestamp",
        "messages_ready",
        "messages_unacknowledged",
        "messages",
        "consumers",
        "publish_total",
        "confirm_total",
    ]
    had_error = False

    with (
        resources_path.open("w", newline="", encoding="utf-8") as resources_file,
        pods_path.open("w", newline="", encoding="utf-8") as pods_file,
        queue_path.open("w", newline="", encoding="utf-8") as queue_file,
        errors_path.open("w", encoding="utf-8") as errors_file,
    ):
        resources_writer = csv.DictWriter(
            resources_file,
            fieldnames=resource_fields,
        )
        pods_writer = csv.DictWriter(pods_file, fieldnames=pod_fields)
        queue_writer = csv.DictWriter(queue_file, fieldnames=queue_fields)
        resources_writer.writeheader()
        pods_writer.writeheader()
        queue_writer.writeheader()

        while True:
            sample_started = time.monotonic()
            timestamp = utc_now()

            try:
                metrics = kubectl_json(
                    "get",
                    "--raw",
                    f"/apis/metrics.k8s.io/v1beta1/namespaces/{args.namespace}/pods",
                )
                write_resource_samples(resources_writer, timestamp, metrics)
            except Exception as exc:
                had_error = True
                record_error(errors_file, "metrics-api", exc)

            try:
                pods = kubectl_json(
                    "get",
                    "pods",
                    "-n",
                    args.namespace,
                    "-o",
                    "json",
                )
                nodes = kubectl_json("get", "nodes", "-o", "json")
                write_pod_samples(pods_writer, timestamp, pods, nodes)
            except Exception as exc:
                had_error = True
                record_error(errors_file, "kubernetes-pods", exc)

            try:
                queue_writer.writerow(rabbitmq_queue_snapshot(args.rabbitmq_url))
            except Exception as exc:
                had_error = True
                record_error(errors_file, "rabbitmq", exc)

            resources_file.flush()
            pods_file.flush()
            queue_file.flush()

            if stop_file.exists():
                break

            elapsed = time.monotonic() - sample_started
            time.sleep(max(0, args.interval - elapsed))

    if had_error:
        raise SystemExit(1)


def export_prometheus(args: argparse.Namespace) -> None:
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    fields = ["timestamp", "query", "metric", "labels", "value"]

    with output.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fields)
        writer.writeheader()

        for query in PROMETHEUS_QUERIES:
            params = urllib.parse.urlencode(
                {
                    "query": query,
                    "start": args.start,
                    "end": args.end,
                    "step": args.step,
                }
            )
            data = request_json(
                f"{args.prometheus_url}/api/v1/query_range?{params}"
            )
            if data.get("status") != "success":
                raise RuntimeError(f"consulta Prometheus sem sucesso: {query}")

            for series in data.get("data", {}).get("result", []):
                metric = series.get("metric") or {}
                metric_name = metric.get("__name__", query)
                labels = {
                    key: value
                    for key, value in metric.items()
                    if key != "__name__"
                }
                labels_json = json.dumps(
                    labels,
                    ensure_ascii=False,
                    sort_keys=True,
                )
                for sample_timestamp, sample_value in series.get("values", []):
                    writer.writerow(
                        {
                            "timestamp": sample_timestamp,
                            "query": query,
                            "metric": metric_name,
                            "labels": labels_json,
                            "value": sample_value,
                        }
                    )


def iso_to_epoch_microseconds(value: str) -> int:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return int(parsed.timestamp() * 1_000_000)


def export_jaeger(args: argparse.Namespace) -> None:
    params = urllib.parse.urlencode(
        {
            "service": args.service,
            "start": iso_to_epoch_microseconds(args.start),
            "end": iso_to_epoch_microseconds(args.end),
            "limit": args.limit,
        }
    )
    data = request_json(f"{args.jaeger_url}/api/traces?{params}")
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as output_file:
        json.dump(data, output_file, ensure_ascii=False, indent=2)
        output_file.write("\n")


def snapshot_kubernetes(args: argparse.Namespace) -> None:
    snapshot = {
        "captured_at": utc_now(),
        "nodes": kubectl_json("get", "nodes", "-o", "json"),
        "deployments": kubectl_json(
            "get",
            "deployments",
            "-n",
            args.namespace,
            "-o",
            "json",
        ),
        "pods": kubectl_json(
            "get",
            "pods",
            "-n",
            args.namespace,
            "-o",
            "json",
        ),
        "services": kubectl_json(
            "get",
            "services",
            "-n",
            args.namespace,
            "-o",
            "json",
        ),
        "persistent_volume_claims": kubectl_json(
            "get",
            "pvc",
            "-n",
            args.namespace,
            "-o",
            "json",
        ),
        "horizontal_pod_autoscalers": kubectl_json(
            "get",
            "hpa",
            "-n",
            args.namespace,
            "-o",
            "json",
        ),
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as output_file:
        json.dump(snapshot, output_file, ensure_ascii=False, indent=2)
        output_file.write("\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    collect_parser = subparsers.add_parser("collect")
    collect_parser.add_argument("--namespace", default="flash-sale")
    collect_parser.add_argument("--resources-output", required=True)
    collect_parser.add_argument("--pods-output", required=True)
    collect_parser.add_argument("--queue-output", required=True)
    collect_parser.add_argument("--errors-output", required=True)
    collect_parser.add_argument("--stop-file", required=True)
    collect_parser.add_argument("--rabbitmq-url", required=True)
    collect_parser.add_argument("--interval", type=int, default=5)

    rabbitmq_parser = subparsers.add_parser("rabbitmq-snapshot")
    rabbitmq_parser.add_argument("--url", required=True)

    snapshot_parser = subparsers.add_parser("snapshot-kubernetes")
    snapshot_parser.add_argument("--namespace", default="flash-sale")
    snapshot_parser.add_argument("--output", required=True)

    prometheus_parser = subparsers.add_parser("export-prometheus")
    prometheus_parser.add_argument("--output", required=True)
    prometheus_parser.add_argument("--start", required=True)
    prometheus_parser.add_argument("--end", required=True)
    prometheus_parser.add_argument("--step", default="5s")
    prometheus_parser.add_argument(
        "--prometheus-url",
        default="http://localhost:29090",
    )

    jaeger_parser = subparsers.add_parser("export-jaeger")
    jaeger_parser.add_argument("--output", required=True)
    jaeger_parser.add_argument("--start", required=True)
    jaeger_parser.add_argument("--end", required=True)
    jaeger_parser.add_argument("--service", default="flash-sale-api")
    jaeger_parser.add_argument("--limit", type=int, default=10000)
    jaeger_parser.add_argument(
        "--jaeger-url",
        default="http://localhost:26686",
    )

    args = parser.parse_args()

    if args.command == "collect":
        collect(args)
    elif args.command == "rabbitmq-snapshot":
        print(
            json.dumps(
                rabbitmq_queue_snapshot(args.url),
                ensure_ascii=False,
            )
        )
    elif args.command == "snapshot-kubernetes":
        snapshot_kubernetes(args)
    elif args.command == "export-prometheus":
        export_prometheus(args)
    elif args.command == "export-jaeger":
        export_jaeger(args)


if __name__ == "__main__":
    main()
