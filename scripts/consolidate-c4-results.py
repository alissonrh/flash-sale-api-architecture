#!/usr/bin/env python3
"""Consolida os tres ensaios oficiais do C4 em artefatos reproduziveis.

Os percentis sao sempre os percentis calculados em cada execucao. A etapa de
agregacao resume esses tres valores e nunca combina amostras brutas.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
OFFICIAL_RUNS_DIRECTORY = REPOSITORY_ROOT / "results/experiments/c4/official"
OUTPUT_DIRECTORY = REPOSITORY_ROOT / "results/consolidated"

EXPECTED_RUN_NAMES = ("c4-run-1", "c4-run-2", "c4-run-3")
EXPECTED_RUN_STATUS = {
    "c4-run-1": ("VALID", "UNSTABLE"),
    "c4-run-2": ("VALID", "STABLE"),
    "c4-run-3": ("VALID", "STABLE"),
}
RUN_NOTES = {
    "c4-run-1": (
        "VALID/UNSTABLE: uma reinicializacao do gateway foi observada; "
        "a execucao permanece incluida. Escala da API de 1 para 5 Pods."
    ),
    "c4-run-2": (
        "VALID/STABLE: latencia elevada foi preservada sem exclusao. "
        "Escala da API de 1 para 5 Pods."
    ),
    "c4-run-3": "VALID/STABLE. Escala da API de 1 para 5 Pods.",
}
EXPECTED_STAGE_NAMES = ("stage_1", "stage_2", "stage_3", "stage_4")
EXPECTED_STAGE_TARGETS = (20, 22, 22, 0)
EXPECTED_STAGE_DURATIONS = ("20s", "30s", "30s", "10s")
EXPECTED_LOAD_PROFILE = {
    "executor": "ramping-arrival-rate",
    "base_url": "http://gateway:8000",
    "scenario": "c4",
    "start_rate": 1,
    "time_unit": "1s",
    "stages": [
        {"target": 20, "duration": "20s"},
        {"target": 22, "duration": "30s"},
        {"target": 22, "duration": "30s"},
        {"target": 0, "duration": "10s"},
    ],
    "pre_allocated_vus": 100,
    "max_vus": 300,
    "graceful_stop": "30s",
    "request_timeout": "60s",
}

REQUIRED_FILES = (
    "run-metadata.json",
    "validity-checklist.md",
    "database/db-summary.json",
    "gateway/effective-config.yaml",
    "gateway/effective-kubernetes-resources.yaml",
    "gateway/protection-summary.json",
    "gateway/rate-limit-config.json",
    "gateway/rate-limit-headers-preflight.json",
    "k6/k6-stage-summary.json",
    "k6/k6-summary.json",
    "k6/k6-timeseries.jsonl",
    "k6/k6.log",
    "kubernetes/after.json",
    "kubernetes/before.json",
    "kubernetes/events.txt",
    "kubernetes/k6-job.yaml",
    "kubernetes/manifests/gateway-configmap.yaml",
    "kubernetes/manifests/gateway-deployment.yaml",
    "kubernetes/manifests/gateway-service.yaml",
    "logs/api.log",
    "logs/gateway.log",
    "logs/worker.log",
    "metrics/collection-summary.json",
    "metrics/collector-errors.jsonl",
    "metrics/collector.log",
    "metrics/hpa-samples.csv",
    "metrics/kubernetes-pods.csv",
    "metrics/kubernetes-resources.csv",
    "metrics/prometheus.csv",
    "rabbitmq/drain-summary.json",
    "rabbitmq/queue.csv",
    "traces/jaeger-traces.json",
)

SUMMARY_FIELDS = (
    "run_id", "validity", "stability", "included_in_aggregate",
    "methodological_note", "requests_started", "classified_http_requests",
    "responses_2xx", "responses_429", "responses_429_percent",
    "responses_5xx", "connection_errors", "unexpected_statuses",
    "unexpected_failures", "unexpected_failures_percent",
    "dropped_iterations", "dropped_iterations_percent", "throughput_rps",
    "accepted_throughput_rps", "rejected_throughput_rps",
    "latency_average_ms", "latency_p95_ms", "latency_p99_ms",
    "response_2xx_average_ms", "response_2xx_p95_ms",
    "response_2xx_p99_ms", "response_429_average_ms",
    "response_429_p95_ms", "response_429_p99_ms", "orders_created",
    "orders_completed", "orders_pending", "orders_processing",
    "orders_failed", "completion_rate_percent", "order_processing_p95_ms",
    "order_processing_p99_ms", "drain_duration_seconds", "drain_completed",
    "drain_objective_met", "max_backlog", "api_peak_cpu_cores",
    "api_peak_memory_mib", "gateway_peak_cpu_cores",
    "gateway_peak_memory_mib", "initial_api_replicas",
    "configured_min_replicas", "configured_max_replicas",
    "api_pods_observed_min", "api_pods_observed_max",
    "hpa_desired_replicas_min", "hpa_desired_replicas_max",
    "hpa_current_replicas_min", "hpa_current_replicas_max",
    "first_scale_request_at", "seconds_to_first_scale_request",
    "api_restarts", "worker_restarts", "gateway_restarts", "total_restarts",
)

STAGE_FIELDS = (
    "run_id", "validity", "stability", "stage", "target_rps",
    "duration_seconds", "requests_started", "classified_http_requests",
    "responses_2xx", "responses_429", "responses_429_percent",
    "responses_5xx", "connection_errors", "unexpected_statuses",
    "unexpected_failures", "dropped_iterations", "throughput_rps",
    "accepted_throughput_rps", "rejected_throughput_rps",
    "latency_average_ms", "latency_p95_ms", "latency_p99_ms",
    "response_2xx_average_ms", "response_2xx_p95_ms",
    "response_2xx_p99_ms", "response_429_average_ms",
    "response_429_p95_ms", "response_429_p99_ms",
)

HPA_RATE_LIMIT_FIELDS = (
    "run_id", "validity", "stability", "methodological_note",
    "total_received", "classified_http_requests", "total_accepted",
    "total_rejected_429", "accepted_percent", "rejected_percent",
    "throughput_rps", "accepted_throughput_rps", "rejected_throughput_rps",
    "rate_limiting_worked", "rejected_side_effects_absent",
    "initial_api_replicas", "configured_min_replicas",
    "configured_max_replicas", "api_pods_observed_min",
    "api_pods_observed_max", "hpa_desired_replicas_min",
    "hpa_desired_replicas_max", "hpa_current_replicas_min",
    "hpa_current_replicas_max", "first_scale_request_at",
    "seconds_to_first_scale_request", "target_cpu_utilization",
    "rate_limit_per_second", "rate_limit_window", "rate_limit_scope",
    "api_restarts", "gateway_restarts", "worker_restarts", "total_restarts",
)

NON_AGGREGATE_FIELDS = {
    "run_id", "validity", "stability", "included_in_aggregate",
    "methodological_note", "drain_completed", "drain_objective_met",
    "first_scale_request_at",
}
AGGREGATE_METRICS = tuple(
    name for name in SUMMARY_FIELDS if name not in NON_AGGREGATE_FIELDS
)
AGGREGATE_FIELDS = (
    "metric", "sample_size", "mean", "median",
    "sample_standard_deviation", "min", "max", "aggregation_method",
)
OUTPUT_FILENAMES = (
    "c4-summary.csv",
    "c4-stage-summary.csv",
    "c4-hpa-rate-limit-summary.csv",
    "c4-aggregate-summary.csv",
    "c4-planilha-experimentos.xlsx",
)
WORKBOOK_SHEETS = (
    "Resumo das execuções",
    "Resultados por estágio",
    "Agregados n=3",
    "HPA + rate limiting",
    "Metodologia",
    "Validações",
)


class ConsolidationError(Exception):
    """Falha de validacao ou consolidacao dos artefatos oficiais."""


def relative_path(path: Path) -> str:
    return path.relative_to(REPOSITORY_ROOT).as_posix()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as source:
            value = json.load(source)
    except (OSError, json.JSONDecodeError) as exc:
        raise ConsolidationError(f"JSON invalido ou ilegivel em {relative_path(path)}: {exc}") from exc
    if not isinstance(value, dict):
        raise ConsolidationError(f"JSON deve conter objeto em {relative_path(path)}")
    return value


def field(data: Any, path: str, source: Path) -> Any:
    value = data
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            raise ConsolidationError(
                f"campo obrigatorio '{path}' ausente em {relative_path(source)}"
            )
        value = value[part]
    return value


def mapping(data: Any, path: str, source: Path) -> dict[str, Any]:
    value = field(data, path, source)
    if not isinstance(value, dict):
        raise ConsolidationError(f"campo '{path}' deve ser objeto em {relative_path(source)}")
    return value


def sequence(data: Any, path: str, source: Path) -> list[Any]:
    value = field(data, path, source)
    if not isinstance(value, list):
        raise ConsolidationError(f"campo '{path}' deve ser lista em {relative_path(source)}")
    return value


def text_value(data: Any, path: str, source: Path) -> str:
    value = field(data, path, source)
    if not isinstance(value, str) or not value.strip():
        raise ConsolidationError(f"campo '{path}' deve ser texto nao vazio em {relative_path(source)}")
    return value


def number_value(
    data: Any, path: str, source: Path, *, integer: bool = False,
    nonnegative: bool = True,
) -> int | float:
    value = field(data, path, source)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConsolidationError(f"campo '{path}' deve ser numerico em {relative_path(source)}")
    if not math.isfinite(float(value)) or (nonnegative and value < 0):
        raise ConsolidationError(f"campo '{path}' possui valor numerico invalido em {relative_path(source)}")
    if integer and not float(value).is_integer():
        raise ConsolidationError(f"campo '{path}' deve ser inteiro em {relative_path(source)}")
    return int(value) if integer else float(value)


def boolean_value(data: Any, path: str, source: Path) -> bool:
    value = field(data, path, source)
    if not isinstance(value, bool):
        raise ConsolidationError(f"campo '{path}' deve ser booleano em {relative_path(source)}")
    return value


def optional_metric_count(metrics: dict[str, Any], name: str, source: Path) -> int:
    if name not in metrics:
        return 0
    return number_value(metrics, f"{name}.count", source, integer=True)


def percentage(numerator: int | float, denominator: int | float) -> float:
    return round(numerator / denominator * 100, 3) if denominator else 0.0


def rounded(value: int | float, digits: int = 6) -> float:
    return round(float(value), digits)


def parse_timestamp(value: str, source: Path, label: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ConsolidationError(f"timestamp invalido para {label} em {relative_path(source)}") from exc


def discover_runs() -> list[Path]:
    if not OFFICIAL_RUNS_DIRECTORY.is_dir():
        raise ConsolidationError(f"diretorio ausente: {relative_path(OFFICIAL_RUNS_DIRECTORY)}")
    runs = sorted((p for p in OFFICIAL_RUNS_DIRECTORY.iterdir() if p.is_dir()), key=lambda p: p.name)
    if tuple(run.name for run in runs) != EXPECTED_RUN_NAMES:
        raise ConsolidationError(
            "devem existir exatamente os tres ensaios oficiais: "
            + ", ".join(EXPECTED_RUN_NAMES)
        )
    return runs


def validate_required_files(runs: list[Path]) -> None:
    missing = [f"{run.name}/{name}" for run in runs for name in REQUIRED_FILES if not (run / name).is_file()]
    if missing:
        raise ConsolidationError("artefatos obrigatorios ausentes: " + ", ".join(missing))


def raw_tree_digest() -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in OFFICIAL_RUNS_DIRECTORY.rglob("*") if p.is_file()):
        digest.update(path.relative_to(OFFICIAL_RUNS_DIRECTORY).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def load_run_artifacts(run: Path) -> dict[str, Any]:
    paths = {
        "metadata": run / "run-metadata.json",
        "k6": run / "k6/k6-summary.json",
        "stages": run / "k6/k6-stage-summary.json",
        "database": run / "database/db-summary.json",
        "drain": run / "rabbitmq/drain-summary.json",
        "collection": run / "metrics/collection-summary.json",
        "protection": run / "gateway/protection-summary.json",
        "rate_config": run / "gateway/rate-limit-config.json",
        "headers": run / "gateway/rate-limit-headers-preflight.json",
        "hpa_samples": run / "metrics/hpa-samples.csv",
    }
    return {
        "paths": paths,
        **{name: load_json(path) for name, path in paths.items() if name != "hpa_samples"},
    }


def metric_counts(artifacts: dict[str, Any]) -> dict[str, int]:
    metrics = mapping(artifacts["k6"], "metrics", artifacts["paths"]["k6"])
    source = artifacts["paths"]["k6"]
    return {
        "requests_started": optional_metric_count(metrics, "requests_started", source),
        "classified_http_requests": optional_metric_count(metrics, "http_reqs", source),
        "responses_2xx": optional_metric_count(metrics, "responses_2xx", source),
        "responses_429": optional_metric_count(metrics, "responses_429", source),
        "responses_5xx": optional_metric_count(metrics, "responses_5xx", source),
        "connection_errors": optional_metric_count(metrics, "connection_errors", source),
        "unexpected_statuses": optional_metric_count(metrics, "unexpected_statuses", source),
        "unexpected_failures": optional_metric_count(metrics, "unexpected_failures", source),
        "dropped_iterations": optional_metric_count(metrics, "dropped_iterations", source),
    }


def restart_counts(artifacts: dict[str, Any]) -> dict[str, int]:
    collection = artifacts["collection"]
    source = artifacts["paths"]["collection"]
    counts = {"api": 0, "worker": 0, "gateway": 0}
    for item in sequence(collection, "restart_deltas", source):
        app = text_value(item, "app", source)
        delta = number_value(item, "restart_delta", source, integer=True)
        if app in counts:
            counts[app] += delta
    total = number_value(collection, "restart_delta_total", source, integer=True)
    if sum(counts.values()) != total:
        raise ConsolidationError(f"{source.parent.parent.name}: reinicios por aplicacao nao reconciliam")
    for app in counts:
        recorded = number_value(collection, f"{app}_restart_delta", source, integer=True)
        if recorded != counts[app]:
            raise ConsolidationError(f"{source.parent.parent.name}: reinicios de {app} inconsistentes")
    return counts


def expected_hpa_configuration() -> dict[str, Any]:
    return {
        "name": "api-hpa", "target_kind": "Deployment", "target_name": "api",
        "min_replicas": 1, "max_replicas": 5, "target_cpu_utilization": 70,
        "scale_up_stabilization_seconds": 0, "scale_up_select_policy": "Max",
        "scale_up_policies": [
            {"periodSeconds": 15, "type": "Pods", "value": 4},
            {"periodSeconds": 15, "type": "Percent", "value": 400},
        ],
        "scale_down_stabilization_seconds": 120, "scale_down_select_policy": "Min",
        "scale_down_policies": [{"periodSeconds": 60, "type": "Pods", "value": 1}],
    }


def validate_hpa_samples(run: Path, artifacts: dict[str, Any]) -> None:
    source = artifacts["paths"]["hpa_samples"]
    config = mapping(artifacts["collection"], "hpa_configuration", artifacts["paths"]["collection"])
    required = {
        "timestamp", "hpa", "target_kind", "target_name", "current_replicas",
        "desired_replicas", "min_replicas", "max_replicas",
        "target_cpu_utilization", "scale_up_stabilization_seconds",
        "scale_up_select_policy", "scale_up_policies",
        "scale_down_stabilization_seconds", "scale_down_select_policy",
        "scale_down_policies",
    }
    current: list[int] = []
    desired: list[int] = []
    first: str | None = None
    try:
        with source.open(encoding="utf-8", newline="") as input_file:
            reader = csv.DictReader(input_file)
            if reader.fieldnames is None or not required.issubset(reader.fieldnames):
                raise ConsolidationError(f"cabecalho HPA invalido em {relative_path(source)}")
            for line_number, row in enumerate(reader, start=2):
                parse_timestamp(row["timestamp"], source, f"linha {line_number}")
                for column, expected in {
                    "hpa": config["name"], "target_kind": config["target_kind"],
                    "target_name": config["target_name"],
                    "scale_up_select_policy": config["scale_up_select_policy"],
                    "scale_down_select_policy": config["scale_down_select_policy"],
                }.items():
                    if row[column] != str(expected):
                        raise ConsolidationError(f"{run.name}: {column} divergente na amostra HPA {line_number}")
                for column, expected in {
                    "min_replicas": config["min_replicas"],
                    "max_replicas": config["max_replicas"],
                    "target_cpu_utilization": config["target_cpu_utilization"],
                    "scale_up_stabilization_seconds": config["scale_up_stabilization_seconds"],
                    "scale_down_stabilization_seconds": config["scale_down_stabilization_seconds"],
                }.items():
                    if int(row[column]) != expected:
                        raise ConsolidationError(f"{run.name}: {column} divergente na amostra HPA {line_number}")
                for column in ("scale_up_policies", "scale_down_policies"):
                    if canonical_json(json.loads(row[column])) != canonical_json(config[column]):
                        raise ConsolidationError(f"{run.name}: {column} divergente na amostra HPA {line_number}")
                current.append(int(row["current_replicas"]))
                desired.append(int(row["desired_replicas"]))
                if desired[-1] > 1 and first is None:
                    first = row["timestamp"]
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ConsolidationError(f"serie HPA invalida em {relative_path(source)}: {exc}") from exc
    collection = artifacts["collection"]
    collection_path = artifacts["paths"]["collection"]
    expected_extremes = {
        "hpa_current_replicas_min": min(current),
        "hpa_current_replicas_max": max(current),
        "hpa_desired_replicas_min": min(desired),
        "hpa_desired_replicas_max": max(desired),
    }
    for key, observed in expected_extremes.items():
        if number_value(collection, key, collection_path, integer=True) != observed:
            raise ConsolidationError(f"{run.name}: extremo {key} diverge das amostras HPA")
    if first != text_value(collection, "hpa_first_desired_above_one_at", collection_path):
        raise ConsolidationError(f"{run.name}: primeira decisao de escala diverge das amostras")


def validate_stage_profile(run: Path, artifacts: dict[str, Any]) -> None:
    metadata_path = artifacts["paths"]["metadata"]
    stage_path = artifacts["paths"]["stages"]
    metadata_stages = sequence(artifacts["metadata"], "load.stages", metadata_path)
    stages = sequence(artifacts["stages"], "stages", stage_path)
    if len(metadata_stages) != 4 or len(stages) != 4:
        raise ConsolidationError(f"{run.name}: devem existir quatro estagios")
    for index, (metadata_stage, stage) in enumerate(zip(metadata_stages, stages)):
        observed = (
            text_value(stage, "name", stage_path),
            number_value(stage, "target", stage_path, integer=True),
            text_value(stage, "duration", stage_path),
        )
        expected = (EXPECTED_STAGE_NAMES[index], EXPECTED_STAGE_TARGETS[index], EXPECTED_STAGE_DURATIONS[index])
        if observed != expected:
            raise ConsolidationError(f"{run.name}: perfil divergente no estagio {index + 1}")
        if mapping({"stage": metadata_stage}, "stage", metadata_path) != {
            "target": expected[1], "duration": expected[2]
        }:
            raise ConsolidationError(f"{run.name}: metadados divergentes no estagio {index + 1}")


def validate_gateway(run: Path, artifacts: dict[str, Any]) -> None:
    expected = {
        "treatment": "edge protection by gateway rate limiting",
        "gateway": "Kong DB-less", "image": "kong:3.9.1-ubuntu",
        "upstream": "http://api:8000", "route": "/checkout", "limit": 20,
        "window": "1s", "limit_by": "service", "policy": "local",
        "fault_tolerant": False, "redis": False, "replicas": 1,
        "client_headers_exposed": True,
    }
    if artifacts["rate_config"] != expected:
        raise ConsolidationError(f"{run.name}: configuracao efetiva de rate limiting divergente")
    headers = artifacts["headers"]
    source = artifacts["paths"]["headers"]
    if number_value(headers, "status", source, integer=True) != 200:
        raise ConsolidationError(f"{run.name}: preflight do gateway nao retornou 200")
    observed = mapping(headers, "rate_limit_headers", source)
    if not {"ratelimit-limit", "ratelimit-remaining", "ratelimit-reset"}.issubset(observed):
        raise ConsolidationError(f"{run.name}: cabecalhos de rate limiting ausentes")
    if str(observed["ratelimit-limit"]) != "20":
        raise ConsolidationError(f"{run.name}: limite anunciado pelo gateway diverge")


def validate_http_database_and_protection(run: Path, artifacts: dict[str, Any]) -> None:
    counts = metric_counts(artifacts)
    metrics = mapping(artifacts["k6"], "metrics", artifacts["paths"]["k6"])
    source = artifacts["paths"]["k6"]
    classified = sum(counts[name] for name in (
        "responses_2xx", "responses_429", "responses_5xx",
        "connection_errors", "unexpected_statuses",
    ))
    if classified != counts["classified_http_requests"] or classified != counts["requests_started"]:
        raise ConsolidationError(f"{run.name}: invariante de classificacao HTTP violada")
    unexpected = counts["responses_5xx"] + counts["connection_errors"] + counts["unexpected_statuses"]
    if counts["unexpected_failures"] != unexpected:
        raise ConsolidationError(f"{run.name}: classes de falha inesperada nao reconciliam")
    if number_value(metrics, "http_req_failed.passes", source, integer=True) != unexpected:
        raise ConsolidationError(f"{run.name}: http_req_failed diverge das falhas inesperadas")
    observed_rate = number_value(metrics, "http_req_failed.value", source)
    if not math.isclose(observed_rate, unexpected / classified if classified else 0.0, abs_tol=1e-12):
        raise ConsolidationError(f"{run.name}: taxa de falha HTTP inconsistente")
    for metric_name, count_name in (
        ("http_req_duration", "classified_http_requests"),
        ("response_duration_2xx", "responses_2xx"),
        ("response_duration_429", "responses_429"),
    ):
        if number_value(metrics, f"{metric_name}.count", source, integer=True) != counts[count_name]:
            raise ConsolidationError(f"{run.name}: amostras de latencia {metric_name} divergentes")

    database = artifacts["database"]
    database_path = artifacts["paths"]["database"]
    total = number_value(database, "total_orders", database_path, integer=True)
    by_status = mapping(database, "orders_by_status", database_path)
    statuses = {name: number_value(by_status, name, database_path, integer=True) for name in (
        "PENDING", "PROCESSING", "COMPLETED", "FAILED"
    )}
    if sum(statuses.values()) != total:
        raise ConsolidationError(f"{run.name}: estados do banco nao reconciliam")
    if total != counts["responses_2xx"] or statuses != {
        "PENDING": 0, "PROCESSING": 0, "COMPLETED": total, "FAILED": 0
    }:
        raise ConsolidationError(f"{run.name}: respostas 2xx e pedidos no banco nao reconciliam")
    if not math.isclose(
        number_value(database, "completion_rate_percent", database_path),
        percentage(statuses["COMPLETED"], total), abs_tol=0.001,
    ):
        raise ConsolidationError(f"{run.name}: taxa de conclusao inconsistente")

    protection = artifacts["protection"]
    protection_path = artifacts["paths"]["protection"]
    expected_classification = {
        "http_reqs": counts["classified_http_requests"],
        "requests_started": counts["requests_started"],
        "responses_2xx": counts["responses_2xx"],
        "responses_429": counts["responses_429"],
        "responses_5xx": counts["responses_5xx"],
        "connection_errors": counts["connection_errors"],
        "unexpected_statuses": counts["unexpected_statuses"],
        "unexpected_failures": counts["unexpected_failures"],
    }
    if mapping(protection, "classification", protection_path) != expected_classification:
        raise ConsolidationError(f"{run.name}: protection-summary diverge do k6")
    if not boolean_value(protection, "classification_invariant_ok", protection_path):
        raise ConsolidationError(f"{run.name}: protection-summary rejeita a classificacao")
    if not boolean_value(protection, "protection_working", protection_path) or counts["responses_429"] <= 0:
        raise ConsolidationError(f"{run.name}: rate limiting nao atuou")
    side_effect = mapping(protection, "side_effect_check", protection_path)
    required_true = (
        "database_total_matches_responses_2xx", "database_statuses_reconcile",
        "accepted_flow_completed", "database_authoritative_check_ok",
        "rejected_side_effects_absent",
    )
    if any(not boolean_value(side_effect, name, protection_path) for name in required_true):
        raise ConsolidationError(f"{run.name}: ausencia de efeitos colaterais dos 429 nao comprovada")


def validate_run(run: Path, artifacts: dict[str, Any]) -> None:
    metadata = artifacts["metadata"]
    source = artifacts["paths"]["metadata"]
    expected_validity, expected_stability = EXPECTED_RUN_STATUS[run.name]
    observed = (
        text_value(metadata, "validity.status", source).upper(),
        text_value(metadata, "stability.status", source).upper(),
    )
    if text_value(metadata, "type", source).lower() != "official":
        raise ConsolidationError(f"{run.name}: tipo deve ser official")
    if text_value(metadata, "id", source) != run.name:
        raise ConsolidationError(f"{run.name}: id diverge do diretorio")
    if text_value(metadata, "scenario", source).lower() != "c4" or text_value(metadata, "load.scenario", source).lower() != "c4":
        raise ConsolidationError(f"{run.name}: cenario deve ser c4")
    if observed != (expected_validity, expected_stability):
        raise ConsolidationError(f"{run.name}: esperado {expected_validity}/{expected_stability}, observado {observed[0]}/{observed[1]}")
    if text_value(metadata, "execution_status", source).upper() != "VALID":
        raise ConsolidationError(f"{run.name}: execution_status deve ser VALID")
    if sequence(metadata, "invalid_reasons", source) or sequence(metadata, "validity.invalid_reasons", source):
        raise ConsolidationError(f"{run.name}: execucao valida nao pode ter invalid_reasons")
    if mapping(metadata, "load", source) != EXPECTED_LOAD_PROFILE:
        raise ConsolidationError(f"{run.name}: perfil de carga divergente")
    if mapping(artifacts["collection"], "hpa_configuration", artifacts["paths"]["collection"]) != expected_hpa_configuration():
        raise ConsolidationError(f"{run.name}: configuracao HPA divergente")
    validate_stage_profile(run, artifacts)
    validate_gateway(run, artifacts)
    validate_hpa_samples(run, artifacts)
    validate_http_database_and_protection(run, artifacts)
    restarts = restart_counts(artifacts)
    expected_restarts = {"api": 0, "worker": 0, "gateway": 1 if run.name == "c4-run-1" else 0}
    if restarts != expected_restarts:
        raise ConsolidationError(f"{run.name}: reinicios divergem do registro metodologico")
    collection = artifacts["collection"]
    collection_path = artifacts["paths"]["collection"]
    for key in (
        "api_pod_count_min", "hpa_desired_replicas_min", "hpa_current_replicas_min"
    ):
        if number_value(collection, key, collection_path, integer=True) != 1:
            raise ConsolidationError(f"{run.name}: {key} deve registrar 1")
    for key in (
        "api_pod_count_max", "hpa_desired_replicas_max", "hpa_current_replicas_max"
    ):
        if number_value(collection, key, collection_path, integer=True) != 5:
            raise ConsolidationError(f"{run.name}: {key} deve registrar 5")


def validate_run_consistency(loaded_runs: list[tuple[Path, dict[str, Any]]]) -> None:
    metadata_fixed_paths = (
        "load", "k6_resources", "initial_replicas", "images",
        "trace_sample_ratio", "collection_interval_seconds",
        "idle_collection_seconds", "cooldown_seconds",
    )
    for path in metadata_fixed_paths:
        values = {
            canonical_json(field(artifacts["metadata"], path, artifacts["paths"]["metadata"]))
            for _, artifacts in loaded_runs
        }
        if len(values) != 1:
            raise ConsolidationError(f"configuracao congelada divergente entre execucoes: {path}")
    for label, getter in (
        ("HPA", lambda a: mapping(a["collection"], "hpa_configuration", a["paths"]["collection"])),
        ("rate limiting", lambda a: a["rate_config"]),
    ):
        if len({canonical_json(getter(artifacts)) for _, artifacts in loaded_runs}) != 1:
            raise ConsolidationError(f"configuracao congelada divergente entre execucoes: {label}")


def nullable_number(data: Any, path: str, source: Path, digits: int = 3) -> float | None:
    value = field(data, path, source)
    return None if value is None else rounded(number_value(data, path, source), digits)


def build_summary_row(run: Path, artifacts: dict[str, Any]) -> dict[str, Any]:
    paths = artifacts["paths"]
    metadata = artifacts["metadata"]
    metrics = mapping(artifacts["k6"], "metrics", paths["k6"])
    database = artifacts["database"]
    drain = artifacts["drain"]
    collection = artifacts["collection"]
    counts = metric_counts(artifacts)
    restarts = restart_counts(artifacts)
    statuses = mapping(database, "orders_by_status", paths["database"])
    load_started_text = text_value(metadata, "timestamps.load_started_at", paths["metadata"])
    first_scale_text = text_value(collection, "hpa_first_desired_above_one_at", paths["collection"])
    seconds_to_scale = (
        parse_timestamp(first_scale_text, paths["collection"], "primeira escala")
        - parse_timestamp(load_started_text, paths["metadata"], "inicio da carga")
    ).total_seconds()
    if seconds_to_scale < 0:
        raise ConsolidationError(f"{run.name}: primeira escala anterior ao inicio da carga")
    return {
        "run_id": run.name,
        "validity": text_value(metadata, "validity.status", paths["metadata"]).upper(),
        "stability": text_value(metadata, "stability.status", paths["metadata"]).upper(),
        "included_in_aggregate": True,
        "methodological_note": RUN_NOTES[run.name],
        **counts,
        "responses_429_percent": percentage(counts["responses_429"], counts["requests_started"]),
        "unexpected_failures_percent": percentage(counts["unexpected_failures"], counts["classified_http_requests"]),
        "dropped_iterations_percent": percentage(
            counts["dropped_iterations"], counts["requests_started"] + counts["dropped_iterations"]
        ),
        "throughput_rps": rounded(number_value(metrics, "throughput_total.rate", paths["k6"])),
        "accepted_throughput_rps": rounded(number_value(metrics, "throughput_accepted_2xx.rate", paths["k6"])),
        "rejected_throughput_rps": rounded(number_value(metrics, "throughput_rejected_429.rate", paths["k6"])),
        "latency_average_ms": rounded(number_value(metrics, "http_req_duration.avg", paths["k6"]), 3),
        "latency_p95_ms": rounded(number_value(metrics, "http_req_duration.p(95)", paths["k6"]), 3),
        "latency_p99_ms": rounded(number_value(metrics, "http_req_duration.p(99)", paths["k6"]), 3),
        "response_2xx_average_ms": rounded(number_value(metrics, "response_duration_2xx.avg", paths["k6"]), 3),
        "response_2xx_p95_ms": rounded(number_value(metrics, "response_duration_2xx.p(95)", paths["k6"]), 3),
        "response_2xx_p99_ms": rounded(number_value(metrics, "response_duration_2xx.p(99)", paths["k6"]), 3),
        "response_429_average_ms": rounded(number_value(metrics, "response_duration_429.avg", paths["k6"]), 3),
        "response_429_p95_ms": rounded(number_value(metrics, "response_duration_429.p(95)", paths["k6"]), 3),
        "response_429_p99_ms": rounded(number_value(metrics, "response_duration_429.p(99)", paths["k6"]), 3),
        "orders_created": number_value(database, "total_orders", paths["database"], integer=True),
        "orders_completed": number_value(statuses, "COMPLETED", paths["database"], integer=True),
        "orders_pending": number_value(statuses, "PENDING", paths["database"], integer=True),
        "orders_processing": number_value(statuses, "PROCESSING", paths["database"], integer=True),
        "orders_failed": number_value(statuses, "FAILED", paths["database"], integer=True),
        "completion_rate_percent": rounded(number_value(database, "completion_rate_percent", paths["database"]), 3),
        "order_processing_p95_ms": rounded(number_value(database, "processing_time_ms.p95", paths["database"]), 3),
        "order_processing_p99_ms": rounded(number_value(database, "processing_time_ms.p99", paths["database"]), 3),
        "drain_duration_seconds": number_value(drain, "drain_duration_seconds", paths["drain"], integer=True),
        "drain_completed": boolean_value(drain, "completed", paths["drain"]),
        "drain_objective_met": boolean_value(drain, "objective_met", paths["drain"]),
        "max_backlog": number_value(collection, "queue.max_messages", paths["collection"], integer=True),
        "api_peak_cpu_cores": rounded(number_value(collection, "resource_peaks.api.max_cpu_cores", paths["collection"]), 9),
        "api_peak_memory_mib": rounded(number_value(collection, "resource_peaks.api.max_memory_mib", paths["collection"]), 6),
        "gateway_peak_cpu_cores": rounded(number_value(collection, "resource_peaks.gateway.max_cpu_cores", paths["collection"]), 9),
        "gateway_peak_memory_mib": rounded(number_value(collection, "resource_peaks.gateway.max_memory_mib", paths["collection"]), 6),
        "initial_api_replicas": number_value(metadata, "initial_replicas.api", paths["metadata"], integer=True),
        "configured_min_replicas": number_value(collection, "hpa_configuration.min_replicas", paths["collection"], integer=True),
        "configured_max_replicas": number_value(collection, "hpa_configuration.max_replicas", paths["collection"], integer=True),
        "api_pods_observed_min": number_value(collection, "api_pod_count_min", paths["collection"], integer=True),
        "api_pods_observed_max": number_value(collection, "api_pod_count_max", paths["collection"], integer=True),
        "hpa_desired_replicas_min": number_value(collection, "hpa_desired_replicas_min", paths["collection"], integer=True),
        "hpa_desired_replicas_max": number_value(collection, "hpa_desired_replicas_max", paths["collection"], integer=True),
        "hpa_current_replicas_min": number_value(collection, "hpa_current_replicas_min", paths["collection"], integer=True),
        "hpa_current_replicas_max": number_value(collection, "hpa_current_replicas_max", paths["collection"], integer=True),
        "first_scale_request_at": first_scale_text,
        "seconds_to_first_scale_request": rounded(seconds_to_scale, 3),
        "api_restarts": restarts["api"],
        "worker_restarts": restarts["worker"],
        "gateway_restarts": restarts["gateway"],
        "total_restarts": sum(restarts.values()),
    }


def build_stage_rows(run: Path, artifacts: dict[str, Any]) -> list[dict[str, Any]]:
    paths = artifacts["paths"]
    validity = text_value(artifacts["metadata"], "validity.status", paths["metadata"]).upper()
    stability = text_value(artifacts["metadata"], "stability.status", paths["metadata"]).upper()
    rows: list[dict[str, Any]] = []
    for stage in sequence(artifacts["stages"], "stages", paths["stages"]):
        metrics = mapping(stage, "metrics", paths["stages"])
        counts = {
            "requests_started": number_value(metrics, "requests_started", paths["stages"], integer=True),
            "classified_http_requests": number_value(metrics, "requests", paths["stages"], integer=True),
            **{
                name: number_value(metrics, name, paths["stages"], integer=True)
                for name in (
                    "responses_2xx", "responses_429", "responses_5xx",
                    "connection_errors", "unexpected_statuses",
                    "unexpected_failures", "dropped_iterations",
                )
            },
        }
        classified = sum(counts[name] for name in (
            "responses_2xx", "responses_429", "responses_5xx",
            "connection_errors", "unexpected_statuses",
        ))
        unexpected = counts["responses_5xx"] + counts["connection_errors"] + counts["unexpected_statuses"]
        if classified != counts["classified_http_requests"] or classified != counts["requests_started"]:
            raise ConsolidationError(f"{run.name}/{stage.get('name')}: classificacao HTTP divergente")
        if unexpected != counts["unexpected_failures"]:
            raise ConsolidationError(f"{run.name}/{stage.get('name')}: falhas inesperadas divergentes")
        throughput = mapping(metrics, "throughput_per_second", paths["stages"])
        latency = mapping(metrics, "latency_ms", paths["stages"])
        latency_all = mapping(latency, "all", paths["stages"])
        latency_2xx = mapping(latency, "responses_2xx", paths["stages"])
        latency_429 = mapping(latency, "responses_429", paths["stages"])
        rows.append({
            "run_id": run.name,
            "validity": validity,
            "stability": stability,
            "stage": text_value(stage, "name", paths["stages"]),
            "target_rps": number_value(stage, "target", paths["stages"], integer=True),
            "duration_seconds": rounded(number_value(stage, "duration_seconds", paths["stages"]), 3),
            **counts,
            "responses_429_percent": percentage(counts["responses_429"], counts["requests_started"]),
            "throughput_rps": rounded(number_value(throughput, "total", paths["stages"]), 6),
            "accepted_throughput_rps": rounded(number_value(throughput, "accepted_2xx", paths["stages"]), 6),
            "rejected_throughput_rps": rounded(number_value(throughput, "rejected_429", paths["stages"]), 6),
            "latency_average_ms": nullable_number(latency_all, "average", paths["stages"]),
            "latency_p95_ms": nullable_number(latency_all, "p95", paths["stages"]),
            "latency_p99_ms": nullable_number(latency_all, "p99", paths["stages"]),
            "response_2xx_average_ms": nullable_number(latency_2xx, "average", paths["stages"]),
            "response_2xx_p95_ms": nullable_number(latency_2xx, "p95", paths["stages"]),
            "response_2xx_p99_ms": nullable_number(latency_2xx, "p99", paths["stages"]),
            "response_429_average_ms": nullable_number(latency_429, "average", paths["stages"]),
            "response_429_p95_ms": nullable_number(latency_429, "p95", paths["stages"]),
            "response_429_p99_ms": nullable_number(latency_429, "p99", paths["stages"]),
        })
    return rows


def validate_stage_totals(summary_rows: list[dict[str, Any]], stage_rows: list[dict[str, Any]]) -> None:
    additive = (
        "requests_started", "classified_http_requests", "responses_2xx",
        "responses_429", "responses_5xx", "connection_errors",
        "unexpected_statuses", "unexpected_failures", "dropped_iterations",
    )
    for summary in summary_rows:
        matching = [row for row in stage_rows if row["run_id"] == summary["run_id"]]
        if len(matching) != 4:
            raise ConsolidationError(f"{summary['run_id']}: esperado quatro estagios")
        for metric in additive:
            if sum(row[metric] for row in matching) != summary[metric]:
                raise ConsolidationError(f"{summary['run_id']}: total por estagio diverge para {metric}")


def validate_explicit_observations(summary_rows: list[dict[str, Any]]) -> None:
    rows = {row["run_id"]: row for row in summary_rows}
    if rows["c4-run-1"]["gateway_restarts"] != 1 or rows["c4-run-1"]["stability"] != "UNSTABLE":
        raise ConsolidationError("c4-run-1 deve registrar reinicializacao do gateway e UNSTABLE")
    peer_p95 = max(rows[name]["latency_p95_ms"] for name in ("c4-run-1", "c4-run-3"))
    if rows["c4-run-2"]["latency_p95_ms"] <= peer_p95 * 10:
        raise ConsolidationError("latencia elevada do c4-run-2 nao esta sustentada pelos dados")
    for row in summary_rows:
        if (row["api_pods_observed_min"], row["api_pods_observed_max"]) != (1, 5):
            raise ConsolidationError(f"{row['run_id']}: escala observada deve ser 1 para 5 Pods")


def build_hpa_rate_rows(
    summary_rows: list[dict[str, Any]], loaded_runs: list[tuple[Path, dict[str, Any]]]
) -> list[dict[str, Any]]:
    artifacts_by_run = {run.name: artifacts for run, artifacts in loaded_runs}
    rows = []
    for summary in summary_rows:
        artifacts = artifacts_by_run[summary["run_id"]]
        protection = artifacts["protection"]
        protection_path = artifacts["paths"]["protection"]
        config = artifacts["rate_config"]
        rows.append({
            "run_id": summary["run_id"],
            "validity": summary["validity"],
            "stability": summary["stability"],
            "methodological_note": summary["methodological_note"],
            "total_received": summary["requests_started"],
            "classified_http_requests": summary["classified_http_requests"],
            "total_accepted": summary["responses_2xx"],
            "total_rejected_429": summary["responses_429"],
            "accepted_percent": percentage(summary["responses_2xx"], summary["requests_started"]),
            "rejected_percent": summary["responses_429_percent"],
            "throughput_rps": summary["throughput_rps"],
            "accepted_throughput_rps": summary["accepted_throughput_rps"],
            "rejected_throughput_rps": summary["rejected_throughput_rps"],
            "rate_limiting_worked": boolean_value(protection, "protection_working", protection_path),
            "rejected_side_effects_absent": boolean_value(
                mapping(protection, "side_effect_check", protection_path),
                "rejected_side_effects_absent", protection_path,
            ),
            **{name: summary[name] for name in (
                "initial_api_replicas", "configured_min_replicas",
                "configured_max_replicas", "api_pods_observed_min",
                "api_pods_observed_max", "hpa_desired_replicas_min",
                "hpa_desired_replicas_max", "hpa_current_replicas_min",
                "hpa_current_replicas_max", "first_scale_request_at",
                "seconds_to_first_scale_request", "api_restarts",
                "gateway_restarts", "worker_restarts", "total_restarts",
            )},
            "target_cpu_utilization": number_value(
                artifacts["collection"], "hpa_configuration.target_cpu_utilization",
                artifacts["paths"]["collection"], integer=True,
            ),
            "rate_limit_per_second": config["limit"],
            "rate_limit_window": config["window"],
            "rate_limit_scope": config["limit_by"],
        })
    return rows


def build_aggregate_rows(summary_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if tuple(row["run_id"] for row in summary_rows) != EXPECTED_RUN_NAMES:
        raise ConsolidationError("agregado deve incluir exatamente os tres ensaios oficiais")
    rows = []
    for metric in AGGREGATE_METRICS:
        values = [float(row[metric]) for row in summary_rows]
        is_percentile = "_p95_" in metric or "_p99_" in metric
        rows.append({
            "metric": metric,
            "sample_size": 3,
            "mean": rounded(statistics.mean(values)),
            "median": rounded(statistics.median(values)),
            "sample_standard_deviation": rounded(statistics.stdev(values)),
            "min": rounded(min(values)),
            "max": rounded(max(values)),
            "aggregation_method": (
                "run-level percentiles; raw samples were not pooled"
                if is_percentile else
                "statistics across the three official run-level values (n=3)"
            ),
        })
    return rows


def csv_value(value: Any) -> Any:
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, float):
        return format(value, ".9f").rstrip("0").rstrip(".") or "0"
    return "" if value is None else value


def write_csv(path: Path, fields: tuple[str, ...], rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fields, extrasaction="raise", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({name: csv_value(row[name]) for name in fields})


def spreadsheet_value(value: Any) -> Any:
    return None if value == "" else value


def spreadsheet_field_value(header: str, value: Any) -> Any:
    converted = spreadsheet_value(value)
    if header.endswith("_at") and isinstance(converted, str):
        return converted + " (UTC)"
    return converted


def spreadsheet_number_format(header: str, value: Any) -> str:
    if header.endswith("_at"):
        return "@"
    if value is None or isinstance(value, (str, bool)):
        return "General"
    if header.endswith("_percent"):
        return "0.000"
    if header.endswith("_rps"):
        return "0.000000"
    if header.endswith("_cpu_cores"):
        return "0.000000000"
    if header.endswith("_memory_mib"):
        return "0.000000"
    if header.endswith("_ms") or header.startswith("seconds_"):
        return "#,##0.000"
    return "#,##0" if isinstance(value, int) else "0.000000"


def excel_column_name(number: int) -> str:
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


def table_definition(
    headers: tuple[str, ...], rows: list[dict[str, Any]], *,
    sheet_name: str, table_name: str, start_row: int = 6,
) -> dict[str, Any]:
    values = [[spreadsheet_field_value(name, row[name]) for name in headers] for row in rows]
    widths = []
    for index, header in enumerate(headers):
        maximum = max([len(header), *(len("" if row[index] is None else str(row[index])) for row in values)])
        if header in {"methodological_note", "aggregation_method"}:
            widths.append(min(max(maximum + 2, 36), 72))
        elif header == "metric":
            widths.append(min(max(maximum + 2, 24), 48))
        else:
            widths.append(min(max(maximum + 2, 12), 28))
    address = f"A{start_row}:{excel_column_name(len(headers))}{start_row + len(values)}"
    return {
        "sheetName": sheet_name, "tableName": table_name,
        "headers": list(headers), "rows": values, "widths": widths,
        "numberFormats": [
            [spreadsheet_number_format(header, value) for header, value in zip(headers, row)]
            for row in values
        ],
        "startRow": start_row, "address": address,
    }


def methodology_rows() -> list[list[str]]:
    return [
        ["Escopo", "Os tres ensaios oficiais do C4 sao VALID e integram todos os agregados descritivos com n=3."],
        ["c4-run-1", "VALID/UNSTABLE: uma reinicializacao do gateway foi preservada e nao motivou exclusao."],
        ["c4-run-2", "VALID/STABLE: a latencia elevada foi preservada e nao motivou exclusao."],
        ["c4-run-3", "VALID/STABLE, sem reinicializacoes."],
        ["Escala", "A API escalou de 1 para 5 Pods nas tres execucoes; replicas desejadas e observadas foram reconciliadas com as amostras do HPA."],
        ["Rate limiting", "O Kong aplicou limite global de 20 requisicoes por segundo por servico e produziu respostas 429 sem efeitos colaterais no banco."],
        ["Percentis", "p95 e p99 agregados resumem os tres percentis de execucao; amostras brutas nunca foram combinadas."],
        ["Estatisticas", "Media, mediana, desvio-padrao amostral, minimo e maximo usam exatamente n=3."],
        ["Imutabilidade", "Os resultados brutos sao entradas somente leitura e seu hash de arvore e verificado antes e depois da consolidacao."],
        ["Reproducibilidade", "Duas geracoes independentes de cada CSV e do XLSX devem ser identicas byte a byte antes da publicacao."],
    ]


def validation_rows() -> list[dict[str, str]]:
    return [
        {"validation": "Artefatos obrigatorios", "result": "PASS", "scope": "3 execucoes"},
        {"validation": "Invariantes de classificacao HTTP", "result": "PASS", "scope": "geral e 12 estagios"},
        {"validation": "Respostas 2xx versus pedidos no banco", "result": "PASS", "scope": "3 execucoes"},
        {"validation": "Ausencia de efeitos colaterais dos 429", "result": "PASS", "scope": "3 execucoes"},
        {"validation": "Totais por estagio versus totais gerais", "result": "PASS", "scope": "9 metricas aditivas"},
        {"validation": "Configuracoes congeladas", "result": "PASS", "scope": "carga, imagens, HPA e gateway"},
        {"validation": "Escala de 1 para 5 Pods", "result": "PASS", "scope": "3 execucoes"},
        {"validation": "Reproducao deterministica", "result": "PASS", "scope": "2 geracoes byte a byte"},
        {"validation": "XLSX: estrutura, tabelas, formulas e valores", "result": "PASS", "scope": "6 abas"},
        {"validation": "XLSX: renderizacao", "result": "PASS", "scope": "todas as abas"},
        {"validation": "Resultados brutos inalterados", "result": "PASS", "scope": "hash SHA-256 da arvore"},
    ]


def build_workbook_specification(
    summary_rows: list[dict[str, Any]], stage_rows: list[dict[str, Any]],
    hpa_rate_rows: list[dict[str, Any]], aggregate_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    validations = validation_rows()
    validation_fields = ("validation", "result", "scope")
    tables = [
        table_definition(SUMMARY_FIELDS, summary_rows, sheet_name=WORKBOOK_SHEETS[0], table_name="C4RunsTable"),
        table_definition(STAGE_FIELDS, stage_rows, sheet_name=WORKBOOK_SHEETS[1], table_name="C4StageTable"),
        table_definition(AGGREGATE_FIELDS, aggregate_rows, sheet_name=WORKBOOK_SHEETS[2], table_name="C4AggregateTable"),
        table_definition(HPA_RATE_LIMIT_FIELDS, hpa_rate_rows, sheet_name=WORKBOOK_SHEETS[3], table_name="C4HpaRateLimitTable"),
        table_definition(validation_fields, validations, sheet_name=WORKBOOK_SHEETS[5], table_name="C4ValidationTable"),
    ]
    return {
        "sheetNames": list(WORKBOOK_SHEETS),
        "summary": tables[0], "stages": tables[1], "aggregates": tables[2],
        "hpaRate": tables[3], "validations": tables[4], "tables": tables,
        "methodologyRows": methodology_rows(),
        "formulaChecks": [
            {"address": "B14", "formula": "=SUM(E7:E9)", "value": sum(row["total_received"] for row in hpa_rate_rows)},
            {"address": "B15", "formula": "=SUM(G7:G9)", "value": sum(row["total_accepted"] for row in hpa_rate_rows)},
            {"address": "B16", "formula": "=SUM(H7:H9)", "value": sum(row["total_rejected_429"] for row in hpa_rate_rows)},
            {"address": "B17", "formula": "=MAX(T7:T9)", "value": max(row["api_pods_observed_max"] for row in hpa_rate_rows)},
            {"address": "B18", "formula": "=MAX(V7:V9)", "value": max(row["hpa_desired_replicas_max"] for row in hpa_rate_rows)},
        ],
    }


WORKBOOK_SCRIPT = r'''
import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const specification = JSON.parse(await fs.readFile(process.env.C4_WORKBOOK_SPECIFICATION, "utf8"));
const outputPath = process.env.C4_WORKBOOK_OUTPUT;
const mode = process.env.C4_WORKBOOK_MODE;
const renderDirectory = process.env.C4_RENDER_DIRECTORY;
const COLORS = {
  navy: "#17365D", blue: "#2F75B5", lightBlue: "#D9EAF7",
  paleBlue: "#EAF3F8", white: "#FFFFFF", text: "#404040",
  border: "#B4C7E7", amber: "#FFF2CC", amberStrong: "#F4B183",
  redText: "#9C0006", green: "#70AD47",
};
const FONT = "Arial";

function columnLetter(columnNumber) {
  let result = "";
  while (columnNumber > 0) {
    columnNumber -= 1;
    result = String.fromCharCode(65 + (columnNumber % 26)) + result;
    columnNumber = Math.floor(columnNumber / 26);
  }
  return result;
}

function tableAddress(startRow, rowCount, columnCount) {
  return `A${startRow}:${columnLetter(columnCount)}${startRow + rowCount - 1}`;
}

function applyBase(sheet) {
  sheet.showGridLines = true;
  sheet.tabColor = COLORS.navy;
}

function applyTitle(sheet, title, subtitle) {
  sheet.getRange("A2").values = [[title]];
  sheet.getRange("A2").format = {
    font: { name: FONT, size: 14, bold: true, color: COLORS.navy },
    verticalAlignment: "center",
  };
  sheet.getRange("A2").format.rowHeight = 24;
  sheet.getRange("A3").values = [[subtitle]];
  sheet.getRange("A3").format = {
    font: { name: FONT, size: 10, italic: true, color: COLORS.text },
    verticalAlignment: "center",
  };
  sheet.getRange("A3").format.rowHeight = 20;
  sheet.getRange("A4:H4").format.borders = {
    bottom: { style: "thin", color: COLORS.navy },
  };
}

function applySection(sheet, address, label) {
  const range = sheet.getRange(address);
  range.merge();
  range.values = [[label]];
  range.format = {
    fill: COLORS.lightBlue,
    font: { name: FONT, size: 10, bold: true, color: COLORS.navy },
    verticalAlignment: "center",
    borders: { preset: "outside", style: "thin", color: COLORS.border },
  };
  range.format.rowHeight = 22;
}

function addTable(sheet, definition) {
  const rows = [definition.headers, ...definition.rows];
  const address = tableAddress(definition.startRow, rows.length, definition.headers.length);
  const range = sheet.getRange(address);
  range.format.numberFormat = [
    definition.headers.map(() => "General"),
    ...definition.numberFormats,
  ];
  range.values = rows;
  range.format.font = { name: FONT, size: 10, color: COLORS.text };
  range.format.verticalAlignment = "center";
  const table = sheet.tables.add(address, true, definition.tableName);
  table.style = "TableStyleMedium2";
  table.showHeaders = true;
  table.showTotals = false;
  table.showBandedColumns = false;
  table.showFilterButton = true;
  const lastColumn = columnLetter(definition.headers.length);
  const header = sheet.getRange(`A${definition.startRow}:${lastColumn}${definition.startRow}`);
  header.format = {
    fill: COLORS.navy,
    font: { name: FONT, size: 10, bold: true, color: COLORS.white },
    horizontalAlignment: "center", verticalAlignment: "center", wrapText: true,
    borders: { preset: "all", style: "thin", color: COLORS.white },
  };
  header.format.rowHeight = 48;
  if (definition.rows.length > 0) {
    const body = sheet.getRange(
      `A${definition.startRow + 1}:${lastColumn}${definition.startRow + definition.rows.length}`,
    );
    body.format.rowHeight = 20;
    body.format.wrapText = false;
    body.format.numberFormat = definition.numberFormats;
  }
  for (let index = 0; index < definition.widths.length; index += 1) {
    const column = columnLetter(index + 1);
    sheet.getRange(
      `${column}${definition.startRow}:${column}${definition.startRow + definition.rows.length}`,
    ).format.columnWidth = definition.widths[index];
  }
}

function safeFilename(name) {
  return name.normalize("NFD").replace(/[\u0300-\u036f]/g, "")
    .replace(/[^A-Za-z0-9]+/g, "-").replace(/^-|-$/g, "").toLowerCase();
}

async function formulaErrorScan(workbook, label) {
  const errors = await workbook.inspect({
    kind: "match",
    searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
    options: { useRegex: true, maxResults: 100 },
    summary: label,
  });
  const tokens = ["#REF!", "#DIV/0!", "#VALUE!", "#NAME?", "#N/A", "#NUM!", "#NULL!", "#SPILL!", "#CALC!"];
  if (tokens.some((token) => errors.ndjson.includes(token))) {
    throw new Error(`erros de formula: ${errors.ndjson}`);
  }
}

if (mode === "build") {
  const workbook = Workbook.create();
  for (const name of specification.sheetNames) {
    applyBase(workbook.worksheets.add(name));
  }

  const summarySheet = workbook.worksheets.getItem("Resumo das execuções");
  applyTitle(summarySheet, "Resumo dos ensaios oficiais do C4",
    "As três execuções válidas compõem a síntese descritiva (n=3); instabilidade e latência elevada permanecem explícitas.");
  applySection(summarySheet, "A5:H5", "Resultados das três execuções oficiais");
  addTable(summarySheet, specification.summary);
  const summaryLastColumn = columnLetter(specification.summary.headers.length);
  summarySheet.getRange(`A7:${summaryLastColumn}7`).format.fill = COLORS.amber;
  summarySheet.getRange("B7:C7").format = {
    fill: COLORS.amberStrong,
    font: { name: FONT, size: 10, bold: true, color: COLORS.redText },
  };
  summarySheet.getRange(`A8:${summaryLastColumn}8`).format.fill = COLORS.paleBlue;
  summarySheet.getRange("E7:E9").format.wrapText = true;
  summarySheet.getRange("A7:E9").format.rowHeight = 42;
  summarySheet.freezePanes.freezeRows(6);

  const stageSheet = workbook.worksheets.getItem("Resultados por estágio");
  applyTitle(stageSheet, "Resultados do C4 por estágio de carga",
    "Doze linhas preservam os quatro estágios de cada execução, inclusive latências de 2xx e 429.");
  applySection(stageSheet, "A5:H5", "Métricas por execução e estágio");
  addTable(stageSheet, specification.stages);
  stageSheet.freezePanes.freezeRows(6);

  const aggregateSheet = workbook.worksheets.getItem("Agregados n=3");
  applyTitle(aggregateSheet, "Agregados descritivos do C4",
    "Média, mediana, desvio-padrão amostral, mínimo e máximo usam exatamente três valores por execução.");
  applySection(aggregateSheet, "A5:H5", "Síntese descritiva dos três ensaios válidos");
  addTable(aggregateSheet, specification.aggregates);
  aggregateSheet.freezePanes.freezeRows(6);

  const hpaSheet = workbook.worksheets.getItem("HPA + rate limiting");
  applyTitle(hpaSheet, "Combinação HPA + rate limiting",
    "Todas as execuções escalaram a API de 1 para 5 Pods e o gateway protegeu a entrada com respostas 429.");
  applySection(hpaSheet, "A5:H5", "Proteção de entrada e decisões de escala por execução");
  addTable(hpaSheet, specification.hpaRate);
  const hpaLastColumn = columnLetter(specification.hpaRate.headers.length);
  hpaSheet.getRange(`A7:${hpaLastColumn}7`).format.fill = COLORS.amber;
  hpaSheet.getRange(`A8:${hpaLastColumn}8`).format.fill = COLORS.paleBlue;
  hpaSheet.getRange("D7:D9").format.wrapText = true;
  hpaSheet.getRange("A7:D9").format.rowHeight = 42;
  hpaSheet.getRange("A13:B13").values = [["Indicador calculado", "Valor"]];
  hpaSheet.getRange("A14:A18").values = [["Requisições recebidas"], ["Aceitas (2xx)"], ["Rejeitadas (429)"], ["Máximo de Pods observado"], ["Máximo desejado pelo HPA"]];
  for (const item of specification.formulaChecks) {
    hpaSheet.getRange(item.address).formulas = [[item.formula]];
  }
  hpaSheet.getRange("A13:B13").format = {
    fill: COLORS.navy,
    font: { name: FONT, size: 10, bold: true, color: COLORS.white },
    horizontalAlignment: "center", verticalAlignment: "center",
  };
  hpaSheet.getRange("A14:A18").format = {
    fill: COLORS.lightBlue,
    font: { name: FONT, size: 10, bold: true, color: COLORS.navy },
  };
  hpaSheet.getRange("A13:A18").format.columnWidth = 31;
  hpaSheet.getRange("B13:B18").format.columnWidth = 18;
  const chart = hpaSheet.charts.add("bar", [
    hpaSheet.getRange("A6:A9"), hpaSheet.getRange("G6:G9"), hpaSheet.getRange("H6:H9"),
  ]);
  chart.title = "Requisições aceitas e rejeitadas com 429";
  chart.titleTextStyle.fontSize = 12;
  chart.titleTextStyle.typeface = FONT;
  chart.legend = { position: "top", textStyle: { typeface: FONT } };
  chart.xAxis = { axisType: "textAxis", textStyle: { typeface: FONT, fontSize: 9 } };
  chart.yAxis = { numberFormatCode: "#,##0", numberFormatSourceLinked: false,
    textStyle: { typeface: FONT, fontSize: 9 } };
  chart.setPosition("D13", "M30");
  if (chart.series.items.length >= 2) {
    chart.series.items[0].fill = COLORS.blue;
    chart.series.items[1].fill = COLORS.green;
  }
  hpaSheet.freezePanes.freezeRows(6);

  const methodologySheet = workbook.worksheets.getItem("Metodologia");
  applyTitle(methodologySheet, "Metodologia da consolidação do C4",
    "Registro de inclusão, anomalias preservadas, estatística e garantias de reprodutibilidade.");
  methodologySheet.getRange("A5:B5").values = [["Tópico", "Registro metodológico"]];
  methodologySheet.getRange("A6:B15").values = specification.methodologyRows;
  methodologySheet.getRange("A5:B5").format = {
    fill: COLORS.navy,
    font: { name: FONT, size: 10, bold: true, color: COLORS.white },
    horizontalAlignment: "center", verticalAlignment: "center", wrapText: true,
  };
  methodologySheet.getRange("A6:A15").format = {
    fill: COLORS.lightBlue,
    font: { name: FONT, size: 10, bold: true, color: COLORS.navy },
    verticalAlignment: "center", wrapText: true,
    borders: { preset: "inside", style: "thin", color: COLORS.border },
  };
  methodologySheet.getRange("B6:B15").format = {
    font: { name: FONT, size: 10, color: COLORS.text },
    verticalAlignment: "center", wrapText: true,
    borders: { preset: "inside", style: "thin", color: COLORS.border },
  };
  methodologySheet.getRange("A6:B15").format.rowHeight = 48;
  methodologySheet.getRange("A5:A15").format.columnWidth = 30;
  methodologySheet.getRange("B5:B15").format.columnWidth = 110;

  const validationSheet = workbook.worksheets.getItem("Validações");
  applyTitle(validationSheet, "Validações da consolidação do C4",
    "A publicação ocorre somente quando todas as verificações terminam com sucesso.");
  applySection(validationSheet, "A5:C5", "Matriz de validação executada");
  addTable(validationSheet, specification.validations);
  validationSheet.getRange("B7:B17").format = {
    fill: "#E2F0D9", font: { name: FONT, size: 10, bold: true, color: "#375623" },
    horizontalAlignment: "center",
  };
  validationSheet.freezePanes.freezeRows(6);

  workbook.recalculate();
  await formulaErrorScan(workbook, "formula error scan before export");
  const output = await SpreadsheetFile.exportXlsx(workbook);
  await output.save(outputPath);
} else if (mode === "validate") {
  const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(outputPath));
  const actualNames = workbook.worksheets.items.map((sheet) => sheet.name);
  if (JSON.stringify(actualNames) !== JSON.stringify(specification.sheetNames)) {
    throw new Error(`abas divergentes: ${JSON.stringify(actualNames)}`);
  }
  for (const definition of specification.tables) {
    const sheet = workbook.worksheets.getItem(definition.sheetName);
    const table = sheet.tables.items.find((item) => item.name === definition.tableName);
    if (!table || table.style !== "TableStyleMedium2" || !table.showFilterButton) {
      throw new Error(`tabela invalida: ${definition.tableName}`);
    }
    const actual = sheet.getRange(definition.address).values;
    const expected = [definition.headers, ...definition.rows];
    if (JSON.stringify(actual) !== JSON.stringify(expected)) {
      for (let row = 0; row < Math.max(actual.length, expected.length); row += 1) {
        const actualRow = actual[row] ?? [];
        const expectedRow = expected[row] ?? [];
        for (let column = 0; column < Math.max(actualRow.length, expectedRow.length); column += 1) {
          if (JSON.stringify(actualRow[column]) !== JSON.stringify(expectedRow[column])) {
            throw new Error(
              `valor divergente em ${definition.tableName} linha ${row + 1}, ` +
              `coluna ${column + 1}: esperado=${JSON.stringify(expectedRow[column])}, ` +
              `obtido=${JSON.stringify(actualRow[column])}`,
            );
          }
        }
      }
      throw new Error(`dimensoes divergentes na tabela ${definition.tableName}`);
    }
  }
  const hpaSheet = workbook.worksheets.getItem("HPA + rate limiting");
  for (const item of specification.formulaChecks) {
    const actualFormula = hpaSheet.getRange(item.address).formulas[0][0];
    const actualValue = hpaSheet.getRange(item.address).values[0][0];
    if (actualFormula !== item.formula || actualValue !== item.value) {
      throw new Error(`formula/valor divergente em ${item.address}: ${actualFormula} -> ${actualValue}`);
    }
  }
  await formulaErrorScan(workbook, "final formula error scan");
  if (renderDirectory) {
    await fs.mkdir(renderDirectory, { recursive: true });
  }
  for (const sheet of workbook.worksheets.items) {
    const preview = await workbook.render({ sheetName: sheet.name, autoCrop: "all", scale: 0.6, format: "png" });
    const bytes = new Uint8Array(await preview.arrayBuffer());
    if (bytes.length === 0) {
      throw new Error(`renderizacao vazia: ${sheet.name}`);
    }
    if (renderDirectory) {
      await fs.writeFile(path.join(renderDirectory, `${safeFilename(sheet.name)}.png`), bytes);
    }
  }
} else {
  throw new Error(`modo desconhecido: ${mode}`);
}
'''


def artifact_tool_node_modules() -> Path:
    configured = os.environ.get("C4_ARTIFACT_TOOL_NODE_MODULES")
    candidates = [
        Path(configured) if configured else None,
        Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules",
    ]
    for candidate in candidates:
        if candidate is not None and (candidate / "@oai/artifact-tool/package.json").is_file():
            return candidate.resolve()
    raise ConsolidationError(
        "dependencia @oai/artifact-tool nao encontrada; defina C4_ARTIFACT_TOOL_NODE_MODULES"
    )


def create_node_modules_link(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
        return
    except OSError as symlink_error:
        if os.name != "nt":
            raise ConsolidationError(f"nao foi possivel criar link para artifact-tool: {symlink_error}") from symlink_error
    command = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )
    if command.returncode != 0:
        detail = command.stderr.strip() or command.stdout.strip()
        raise ConsolidationError(f"nao foi possivel criar juncao para artifact-tool: {detail}")


def relationship_owner(relationship_path: str) -> str | None:
    if relationship_path == "_rels/.rels":
        return None
    path = Path(relationship_path)
    if path.parent.name != "_rels" or not path.name.endswith(".rels"):
        return None
    return (path.parent.parent / path.name.removesuffix(".rels")).as_posix()


def normalize_workbook_package(path: Path) -> None:
    normalized = path.with_suffix(".normalized.xlsx")
    with zipfile.ZipFile(path, "r") as source:
        contents = {name: source.read(name) for name in source.namelist()}
    for name in sorted(contents):
        if not name.endswith(".rels"):
            continue
        relationship_ids = re.findall(rb'\bId="([^"]+)"', contents[name])
        owner = relationship_owner(name)
        for index, relationship_id in enumerate(relationship_ids, start=1):
            stable_id = f"rId{index}".encode("ascii")
            contents[name] = contents[name].replace(relationship_id, stable_id)
            if owner is not None and owner in contents:
                contents[owner] = contents[owner].replace(relationship_id, stable_id)
    with zipfile.ZipFile(normalized, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as output:
        for name in sorted(contents):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o600 << 16
            output.writestr(info, contents[name])
    os.replace(normalized, path)


def run_workbook_script(
    node: str, script_path: Path, working_directory: Path,
    environment: dict[str, str], mode: str,
) -> None:
    environment = {**environment, "C4_WORKBOOK_MODE": mode}
    command = subprocess.run(
        [node, str(script_path)], cwd=working_directory, env=environment,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=False, timeout=240,
    )
    if command.returncode != 0:
        detail = command.stderr.strip() or command.stdout.strip()
        raise ConsolidationError(f"falha ao {mode} planilha do C4: {detail}")


def write_workbook(
    path: Path, specification: dict[str, Any], temporary_directory: Path,
    script_path: Path, node: str, environment: dict[str, str],
) -> None:
    specification_path = path.with_suffix(".specification.json")
    specification_path.write_text(
        json.dumps(specification, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    workbook_environment = {
        **environment,
        "C4_WORKBOOK_SPECIFICATION": str(specification_path),
        "C4_WORKBOOK_OUTPUT": str(path),
    }
    run_workbook_script(node, script_path, temporary_directory, workbook_environment, "build")
    normalize_workbook_package(path)
    run_workbook_script(node, script_path, temporary_directory, workbook_environment, "validate")


def artifact_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_artifact_set(
    directory: Path, summary_rows: list[dict[str, Any]],
    stage_rows: list[dict[str, Any]], hpa_rate_rows: list[dict[str, Any]],
    aggregate_rows: list[dict[str, Any]], specification: dict[str, Any],
    script_path: Path, node: str, environment: dict[str, str],
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    write_csv(directory / OUTPUT_FILENAMES[0], SUMMARY_FIELDS, summary_rows)
    write_csv(directory / OUTPUT_FILENAMES[1], STAGE_FIELDS, stage_rows)
    write_csv(directory / OUTPUT_FILENAMES[2], HPA_RATE_LIMIT_FIELDS, hpa_rate_rows)
    write_csv(directory / OUTPUT_FILENAMES[3], AGGREGATE_FIELDS, aggregate_rows)
    write_workbook(
        directory / OUTPUT_FILENAMES[4], specification, directory,
        script_path, node, environment,
    )


def consolidate() -> tuple[
    list[dict[str, Any]], list[dict[str, Any]],
    list[dict[str, Any]], list[dict[str, Any]],
]:
    runs = discover_runs()
    validate_required_files(runs)
    raw_digest_before = raw_tree_digest()
    loaded_runs = [(run, load_run_artifacts(run)) for run in runs]
    for run, artifacts in loaded_runs:
        validate_run(run, artifacts)
    validate_run_consistency(loaded_runs)
    summary_rows = [build_summary_row(run, artifacts) for run, artifacts in loaded_runs]
    stage_rows = [row for run, artifacts in loaded_runs for row in build_stage_rows(run, artifacts)]
    validate_stage_totals(summary_rows, stage_rows)
    validate_explicit_observations(summary_rows)
    hpa_rate_rows = build_hpa_rate_rows(summary_rows, loaded_runs)
    aggregate_rows = build_aggregate_rows(summary_rows)
    specification = build_workbook_specification(
        summary_rows, stage_rows, hpa_rate_rows, aggregate_rows
    )

    node = shutil.which("node")
    if node is None:
        raise ConsolidationError("Node.js nao encontrado para gerar a planilha")
    OUTPUT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="c4-consolidation-") as temporary:
            temporary_path = Path(temporary)
            create_node_modules_link(temporary_path / "node_modules", artifact_tool_node_modules())
            script_path = temporary_path / "build-and-validate-c4-workbook.mjs"
            script_path.write_text(WORKBOOK_SCRIPT, encoding="utf-8")
            environment = os.environ.copy()
            first = temporary_path / "first"
            second = temporary_path / "second"
            write_artifact_set(
                first, summary_rows, stage_rows, hpa_rate_rows, aggregate_rows,
                specification, script_path, node, environment,
            )
            write_artifact_set(
                second, summary_rows, stage_rows, hpa_rate_rows, aggregate_rows,
                specification, script_path, node, environment,
            )
            for filename in OUTPUT_FILENAMES:
                first_hash = artifact_sha256(first / filename)
                second_hash = artifact_sha256(second / filename)
                if first_hash != second_hash:
                    raise ConsolidationError(
                        f"reproducao nao deterministica de {filename}: {first_hash} != {second_hash}"
                    )
            if raw_tree_digest() != raw_digest_before:
                raise ConsolidationError("resultados brutos foram alterados durante a consolidacao")
            for filename in OUTPUT_FILENAMES:
                os.replace(first / filename, OUTPUT_DIRECTORY / filename)
    except subprocess.TimeoutExpired as exc:
        raise ConsolidationError("tempo excedido ao gerar ou validar a planilha do C4") from exc
    return summary_rows, stage_rows, hpa_rate_rows, aggregate_rows


def main() -> int:
    try:
        summary_rows, stage_rows, hpa_rate_rows, aggregate_rows = consolidate()
    except ConsolidationError as exc:
        print(f"Erro de consolidacao do C4: {exc}", file=sys.stderr)
        return 1
    print(
        f"C4 consolidado: {len(summary_rows)} execucoes VALID, "
        f"{len(stage_rows)} estagios, {len(hpa_rate_rows)} resumos HPA + rate limiting "
        f"e {len(aggregate_rows)} agregados descritivos com n=3."
    )
    print("Validacoes: artefatos, HTTP, banco, 429, estagios, configuracoes, HPA, XLSX, determinismo e imutabilidade: PASS")
    for filename in OUTPUT_FILENAMES:
        print(relative_path(OUTPUT_DIRECTORY / filename))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
