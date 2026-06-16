#!/usr/bin/env python3
import argparse
import base64
import csv
import json
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path


CONTAINERS = [
    "flash-sale-api",
    "flash-sale-worker",
    "flash-sale-postgres",
    "flash-sale-rabbitmq",
    "flash-sale-prometheus",
]
RABBITMQ_URL = "http://localhost:15672/api/queues/%2F/checkout_requests"
RABBITMQ_AUTH = ("app", "app")
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
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def request_json(url: str, auth: tuple[str, str] | None = None) -> dict:
    headers = {}
    if auth:
        token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        headers["Authorization"] = f"Basic {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def append_docker_stats(writer: csv.DictWriter) -> None:
    command = [
        "docker",
        "stats",
        "--no-stream",
        "--format",
        "{{json .}}",
        *CONTAINERS,
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    timestamp = utc_now()

    if completed.returncode != 0:
        print(completed.stderr.strip(), flush=True)
        return

    for line in completed.stdout.splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        memory_usage = item.get("MemUsage", "")
        memory_parts = [part.strip() for part in memory_usage.split("/", 1)]
        writer.writerow(
            {
                "timestamp": timestamp,
                "container": item.get("Name", ""),
                "cpu_percent": item.get("CPUPerc", ""),
                "memory_usage": memory_parts[0] if memory_parts else memory_usage,
                "memory_limit": memory_parts[1] if len(memory_parts) == 2 else "",
                "memory_percent": item.get("MemPerc", ""),
                "network_io": item.get("NetIO", ""),
                "block_io": item.get("BlockIO", ""),
                "pids": item.get("PIDs", ""),
            }
        )


def append_rabbitmq(writer: csv.DictWriter) -> None:
    timestamp = utc_now()
    data = request_json(RABBITMQ_URL, RABBITMQ_AUTH)
    message_stats = data.get("message_stats", {})
    writer.writerow(
        {
            "timestamp": timestamp,
            "messages_ready": data.get("messages_ready", 0),
            "messages_unacknowledged": data.get("messages_unacknowledged", 0),
            "messages": data.get("messages", 0),
            "publish_total": message_stats.get("publish", 0),
            "ack_total": message_stats.get("ack", 0),
            "consumers": data.get("consumers", 0),
            "consumer_capacity": data.get("consumer_capacity", ""),
        }
    )


def collect(args: argparse.Namespace) -> None:
    results_dir = Path(args.results_dir)
    stop_file = Path(args.stop_file)

    docker_fields = [
        "timestamp",
        "container",
        "cpu_percent",
        "memory_usage",
        "memory_limit",
        "memory_percent",
        "network_io",
        "block_io",
        "pids",
    ]
    rabbitmq_fields = [
        "timestamp",
        "messages_ready",
        "messages_unacknowledged",
        "messages",
        "publish_total",
        "ack_total",
        "consumers",
        "consumer_capacity",
    ]

    with (
        (results_dir / "docker-stats.csv").open("w", newline="", encoding="utf-8") as docker_file,
        (results_dir / "rabbitmq.csv").open("w", newline="", encoding="utf-8") as rabbitmq_file,
    ):
        docker_writer = csv.DictWriter(docker_file, fieldnames=docker_fields)
        rabbitmq_writer = csv.DictWriter(rabbitmq_file, fieldnames=rabbitmq_fields)
        docker_writer.writeheader()
        rabbitmq_writer.writeheader()

        while True:
            append_docker_stats(docker_writer)
            try:
                append_rabbitmq(rabbitmq_writer)
            except Exception as exc:
                print(f"Erro ao coletar RabbitMQ: {exc}", flush=True)

            docker_file.flush()
            rabbitmq_file.flush()

            if stop_file.exists():
                break

            time.sleep(args.interval)


def export_prometheus(args: argparse.Namespace) -> None:
    output = Path(args.output)
    fields = ["timestamp", "metric", "labels", "value"]

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
            data = request_json(f"{args.prometheus_url}/api/v1/query_range?{params}")
            if data.get("status") != "success":
                print(f"Consulta Prometheus sem sucesso: {query}", flush=True)
                continue

            for series in data.get("data", {}).get("result", []):
                metric = series.get("metric", {})
                metric_name = metric.get("__name__", query)
                labels = {key: value for key, value in metric.items() if key != "__name__"}
                labels_json = json.dumps(labels, ensure_ascii=False, sort_keys=True)
                for sample_ts, sample_value in series.get("values", []):
                    writer.writerow(
                        {
                            "timestamp": sample_ts,
                            "metric": metric_name,
                            "labels": labels_json,
                            "value": sample_value,
                        }
                    )


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    collect_parser = subparsers.add_parser("collect")
    collect_parser.add_argument("--results-dir", required=True)
    collect_parser.add_argument("--stop-file", required=True)
    collect_parser.add_argument("--interval", type=int, default=5)

    prometheus_parser = subparsers.add_parser("export-prometheus")
    prometheus_parser.add_argument("--output", required=True)
    prometheus_parser.add_argument("--start", required=True)
    prometheus_parser.add_argument("--end", required=True)
    prometheus_parser.add_argument("--step", default="5s")
    prometheus_parser.add_argument("--prometheus-url", default="http://localhost:9090")

    args = parser.parse_args()
    if args.command == "collect":
        collect(args)
    elif args.command == "export-prometheus":
        export_prometheus(args)


if __name__ == "__main__":
    main()
