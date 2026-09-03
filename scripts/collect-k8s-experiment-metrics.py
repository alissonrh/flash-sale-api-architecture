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


def read_json(path: str) -> dict:
    with Path(path).open(encoding="utf-8") as source:
        return json.load(source)


def snapshot_container_restarts(snapshot: dict) -> dict:
    containers = {}
    for pod in snapshot.get("pods", {}).get("items", []):
        metadata = pod.get("metadata") or {}
        labels = metadata.get("labels") or {}
        pod_name = metadata.get("name", "")
        pod_uid = metadata.get("uid", pod_name)
        app = labels.get("app", "")
        statuses = (pod.get("status") or {}).get("containerStatuses") or []
        for status in statuses:
            container_name = status.get("name", "")
            containers[(pod_uid, container_name)] = {
                "app": app,
                "pod": pod_name,
                "pod_uid": pod_uid,
                "container": container_name,
                "restart_count": int(status.get("restartCount", 0)),
            }
    return containers


def calculate_restart_deltas(before: dict, after: dict) -> list[dict]:
    initial = snapshot_container_restarts(before)
    final = snapshot_container_restarts(after)
    deltas = []

    for key, final_state in sorted(
        final.items(),
        key=lambda item: (item[1]["app"], item[1]["pod"], item[1]["container"]),
    ):
        initial_count = initial.get(key, {}).get("restart_count", 0)
        final_count = final_state["restart_count"]
        delta = max(0, final_count - initial_count)
        if delta == 0:
            continue
        deltas.append(
            {
                "app": final_state["app"],
                "pod": final_state["pod"],
                "pod_uid": final_state["pod_uid"],
                "container": final_state["container"],
                "initial_restart_count": initial_count,
                "final_restart_count": final_count,
                "restart_delta": delta,
            }
        )

    return deltas


def summarize_collection(args: argparse.Namespace) -> None:
    resource_peaks = {}
    with Path(args.resources).open(newline="", encoding="utf-8") as source:
        for row in csv.DictReader(source):
            container = row["container"]
            peak = resource_peaks.setdefault(
                container,
                {"max_cpu_cores": 0.0, "max_memory_mib": 0.0},
            )
            peak["max_cpu_cores"] = max(
                peak["max_cpu_cores"],
                float(row["cpu_cores"]),
            )
            peak["max_memory_mib"] = max(
                peak["max_memory_mib"],
                float(row["memory_mib"]),
            )

    node_remained_ready = True
    api_pod_counts = []
    with Path(args.pods).open(newline="", encoding="utf-8") as source:
        for row in csv.DictReader(source):
            node_remained_ready = (
                node_remained_ready
                and row["all_nodes_ready"].lower() == "true"
            )
            api_pod_counts.append(int(row["api_pod_count"]))

    queue_rows = []
    with Path(args.queue).open(newline="", encoding="utf-8") as source:
        for row in csv.DictReader(source):
            queue_rows.append(
                {
                    key: int(value) if key != "timestamp" else value
                    for key, value in row.items()
                }
            )

    restart_deltas = calculate_restart_deltas(
        read_json(args.before),
        read_json(args.after),
    )
    restart_delta_total = sum(
        item["restart_delta"] for item in restart_deltas
    )
    api_worker_restart_delta = sum(
        item["restart_delta"]
        for item in restart_deltas
        if item["app"] in {"api", "worker"}
    )

    summary = {
        "node_remained_ready": node_remained_ready,
        "no_pod_restarts": restart_delta_total == 0,
        "restart_delta_total": restart_delta_total,
        "api_worker_restart_delta": api_worker_restart_delta,
        "restart_deltas": restart_deltas,
        "restart_method": (
            "final restartCount minus initial restartCount, matched by Pod UID "
            "and container; negative differences are clamped to zero"
        ),
        "api_pod_count_min": min(api_pod_counts) if api_pod_counts else None,
        "api_pod_count_max": max(api_pod_counts) if api_pod_counts else None,
        "resource_peaks": resource_peaks,
        "queue": {
            "sample_count": len(queue_rows),
            "max_messages_ready": max(
                (row["messages_ready"] for row in queue_rows),
                default=0,
            ),
            "max_messages_unacknowledged": max(
                (row["messages_unacknowledged"] for row in queue_rows),
                default=0,
            ),
            "max_messages": max(
                (row["messages"] for row in queue_rows),
                default=0,
            ),
            "final": queue_rows[-1] if queue_rows else None,
        },
    }

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as output_file:
        json.dump(summary, output_file, ensure_ascii=False, indent=2)
        output_file.write("\n")


def parse_duration_seconds(value: str) -> float:
    match = re.fullmatch(r"([0-9]+)(ms|s|m)", value)
    if match is None:
        raise ValueError(f"duracao invalida: {value}")
    multipliers = {"ms": 0.001, "s": 1.0, "m": 60.0}
    return int(match.group(1)) * multipliers[match.group(2)]


def parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def percentile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower_index = int(position)
    upper_index = min(lower_index + 1, len(ordered) - 1)
    fraction = position - lower_index
    return ordered[lower_index] + (
        ordered[upper_index] - ordered[lower_index]
    ) * fraction


def latency_summary(values: list[float]) -> dict:
    if not values:
        return {
            "count": 0,
            "average": None,
            "minimum": None,
            "p90": None,
            "p95": None,
            "p99": None,
            "maximum": None,
        }
    return {
        "count": len(values),
        "average": round(sum(values) / len(values), 3),
        "minimum": round(min(values), 3),
        "p90": round(percentile(values, 0.90), 3),
        "p95": round(percentile(values, 0.95), 3),
        "p99": round(percentile(values, 0.99), 3),
        "maximum": round(max(values), 3),
    }


def stage_for_timestamp(
    timestamp: str,
    started_at: datetime,
    stages: list[dict],
) -> str:
    elapsed = max(0.0, (parse_datetime(timestamp) - started_at).total_seconds())
    boundary = 0.0
    for stage in stages:
        boundary += stage["duration_seconds"]
        if elapsed < boundary:
            return stage["name"]
    return stages[-1]["name"]


def summarize_k6_stages(args: argparse.Namespace) -> None:
    stages = []
    offset = 0.0
    for raw_stage in args.stage:
        name, target, duration = raw_stage.split(",", 2)
        duration_seconds = parse_duration_seconds(duration)
        stages.append(
            {
                "name": name,
                "target": int(target),
                "duration": duration,
                "duration_seconds": duration_seconds,
                "start_offset_seconds": offset,
                "end_offset_seconds": offset + duration_seconds,
            }
        )
        offset += duration_seconds

    counters = {
        "http_reqs": "requests",
        "responses_2xx": "responses_2xx",
        "responses_429": "responses_429",
        "responses_5xx": "responses_5xx",
        "unexpected_statuses": "unexpected_statuses",
        "connection_errors": "connection_errors",
        "dropped_iterations": "dropped_iterations",
    }
    trends = {
        "http_req_duration": "all",
        "response_duration_2xx": "responses_2xx",
        "response_duration_429": "responses_429",
    }
    stage_data = {
        stage["name"]: {
            **{name: 0.0 for name in counters.values()},
            "latencies": {name: [] for name in trends.values()},
        }
        for stage in stages
    }
    started_at = parse_datetime(args.started_at)

    with Path(args.input).open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"JSONL do k6 invalido na linha {line_number}: {exc}"
                ) from exc
            if record.get("type") != "Point":
                continue

            metric = record.get("metric", "")
            if metric not in counters and metric not in trends:
                continue
            data = record.get("data") or {}
            tags = data.get("tags") or {}
            stage_name = tags.get("load_stage")
            if stage_name not in stage_data:
                stage_name = stage_for_timestamp(
                    data["time"],
                    started_at,
                    stages,
                )
            value = float(data.get("value", 0))
            if metric in counters:
                stage_data[stage_name][counters[metric]] += value
            else:
                stage_data[stage_name]["latencies"][trends[metric]].append(
                    value
                )

    output_stages = []
    for stage in stages:
        data = stage_data[stage["name"]]
        metrics = {
            name: int(value) if value.is_integer() else value
            for name, value in data.items()
            if name != "latencies"
        }
        metrics["latency_ms"] = {
            name: latency_summary(data["latencies"][name])
            for name in trends.values()
        }
        output_stages.append({**stage, "metrics": metrics})

    summary = {
        "source": str(args.input),
        "load_started_at": args.started_at,
        "stage_assignment": (
            "load_stage tag recorded at iteration start; timestamp relative to "
            "load_started_at is used when the k6 metric has no load_stage tag"
        ),
        "latency_percentile_method": (
            "linear interpolation at (n - 1) * p (type 7)"
        ),
        "stages": output_stages,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as output_file:
        json.dump(summary, output_file, ensure_ascii=False, indent=2)
        output_file.write("\n")


def event_observed_range(
    event: dict,
) -> tuple[datetime, datetime] | None:
    values = [
        event.get("eventTime"),
        (event.get("series") or {}).get("lastObservedTime"),
        event.get("lastTimestamp"),
        event.get("firstTimestamp"),
        (event.get("metadata") or {}).get("creationTimestamp"),
    ]
    parsed = [parse_datetime(value) for value in values if value]
    return (min(parsed), max(parsed)) if parsed else None


def filter_events(args: argparse.Namespace) -> None:
    start = parse_datetime(args.start)
    end = parse_datetime(args.end)
    events = json.load(sys.stdin).get("items", [])
    selected = []
    for event in events:
        observed_range = event_observed_range(event)
        if observed_range is None:
            continue
        first_observed, last_observed = observed_range
        if first_observed <= end and last_observed >= start:
            selected.append((last_observed, first_observed, event))
    selected.sort(key=lambda item: item[0])

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as output_file:
        output_file.write(
            "FIRST_OBSERVED\tLAST_OBSERVED\tTYPE\tREASON\tOBJECT\tCOUNT\tMESSAGE\n"
        )
        for last_observed, first_observed, event in selected:
            involved = event.get("involvedObject") or {}
            series = event.get("series") or {}
            fields = [
                first_observed.isoformat().replace("+00:00", "Z"),
                last_observed.isoformat().replace("+00:00", "Z"),
                event.get("type", ""),
                event.get("reason", ""),
                f'{involved.get("kind", "")}/{involved.get("name", "")}',
                str(series.get("count", event.get("count", ""))),
                event.get("message", ""),
            ]
            output_file.write(
                "\t".join(str(value).replace("\t", " ").replace("\n", " ") for value in fields)
                + "\n"
            )


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

    collection_summary_parser = subparsers.add_parser("summarize-collection")
    collection_summary_parser.add_argument("--resources", required=True)
    collection_summary_parser.add_argument("--pods", required=True)
    collection_summary_parser.add_argument("--queue", required=True)
    collection_summary_parser.add_argument("--before", required=True)
    collection_summary_parser.add_argument("--after", required=True)
    collection_summary_parser.add_argument("--output", required=True)

    k6_summary_parser = subparsers.add_parser("summarize-k6-stages")
    k6_summary_parser.add_argument("--input", required=True)
    k6_summary_parser.add_argument("--output", required=True)
    k6_summary_parser.add_argument("--started-at", required=True)
    k6_summary_parser.add_argument("--stage", action="append", required=True)

    events_parser = subparsers.add_parser("filter-events")
    events_parser.add_argument("--start", required=True)
    events_parser.add_argument("--end", required=True)
    events_parser.add_argument("--output", required=True)

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
    elif args.command == "summarize-collection":
        summarize_collection(args)
    elif args.command == "summarize-k6-stages":
        summarize_k6_stages(args)
    elif args.command == "filter-events":
        filter_events(args)
    elif args.command == "export-prometheus":
        export_prometheus(args)
    elif args.command == "export-jaeger":
        export_jaeger(args)


if __name__ == "__main__":
    main()
