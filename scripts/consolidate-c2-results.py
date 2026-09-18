#!/usr/bin/env python3
"""Consolidate the three official C2 runs into reproducible CSV files.

Percentiles are first calculated inside each official run by the original
collectors. This consolidation summarizes those run-level percentiles and
never pools raw request or order samples across runs.
"""

import csv
import json
import math
import statistics
import sys
from bisect import bisect_left
from datetime import datetime
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
OFFICIAL_RUNS_DIRECTORY = REPOSITORY_ROOT / "results/experiments/c2/official"
OUTPUT_DIRECTORY = REPOSITORY_ROOT / "results/consolidated"

EXPECTED_RUN_NAMES = ("c2-run-1", "c2-run-2", "c2-run-3")
EXPECTED_STAGE_NAMES = ("stage_1", "stage_2", "stage_3", "stage_4")
EXPECTED_STAGE_TARGETS = (20, 22, 22, 0)
EXPECTED_STAGE_DURATIONS = ("20s", "30s", "30s", "10s")
EXPECTED_LOAD_PROFILE = {
    "executor": "ramping-arrival-rate",
    "base_url": "http://api:8000",
    "scenario": "c2",
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

# Keep this list aligned with validate_required_files in run-k8s-experiment.sh.
RUNNER_REQUIRED_FILES = (
    "run-metadata.json",
    "k6/k6-summary.json",
    "k6/k6-stage-summary.json",
    "k6/k6-timeseries.jsonl",
    "k6/k6.log",
    "metrics/kubernetes-resources.csv",
    "metrics/kubernetes-pods.csv",
    "metrics/hpa-samples.csv",
    "metrics/prometheus.csv",
    "logs/api.log",
    "logs/worker.log",
    "traces/jaeger-traces.json",
    "kubernetes/before.json",
    "kubernetes/after.json",
    "kubernetes/events.txt",
    "rabbitmq/queue.csv",
    "rabbitmq/drain-summary.json",
    "database/db-summary.json",
)

# The collector summary is an additional required consolidation input.
CONSOLIDATION_REQUIRED_FILES = ("metrics/collection-summary.json",)
REQUIRED_FILES = RUNNER_REQUIRED_FILES + CONSOLIDATION_REQUIRED_FILES

SUMMARY_FIELDS = (
    "run_id",
    "validity",
    "stability",
    "requests_started",
    "responses_2xx",
    "responses_5xx",
    "connection_errors",
    "unexpected_failures",
    "unexpected_failures_percent",
    "dropped_iterations",
    "dropped_iterations_percent",
    "throughput_rps",
    "accepted_throughput_rps",
    "latency_average_ms",
    "latency_p95_ms",
    "latency_p99_ms",
    "response_2xx_average_ms",
    "response_2xx_p95_ms",
    "response_2xx_p99_ms",
    "orders_created",
    "orders_completed",
    "orders_pending",
    "orders_failed",
    "completion_rate_percent",
    "order_processing_p95_ms",
    "order_processing_p99_ms",
    "drain_duration_seconds",
    "drain_completed",
    "drain_objective_met",
    "restarts",
    "api_peak_cpu_cores",
    "api_peak_memory_mib",
    "max_backlog",
    "app_cpu_seconds_load",
    "app_memory_mib_minutes_load",
    "app_cpu_seconds_per_1000_completed_orders",
    "app_memory_mib_minutes_per_1000_completed_orders",
)

STAGE_FIELDS = (
    "run_id",
    "stage",
    "target_rps",
    "duration_seconds",
    "requests_started",
    "responses_2xx",
    "responses_5xx",
    "connection_errors",
    "dropped_iterations",
    "latency_average_ms",
    "latency_p95_ms",
    "latency_p99_ms",
    "response_2xx_average_ms",
    "response_2xx_p95_ms",
    "response_2xx_p99_ms",
)

HPA_FIELDS = (
    "run_id",
    "hpa_name",
    "target_kind",
    "target_name",
    "initial_api_replicas",
    "configured_min_replicas",
    "configured_max_replicas",
    "api_pods_observed_min",
    "api_pods_observed_max",
    "hpa_desired_replicas_min",
    "hpa_desired_replicas_max",
    "first_scale_request_at",
    "seconds_from_load_start_to_first_scale_request",
    "multiple_api_pods_observed_at",
    "seconds_from_load_start_to_multiple_api_pods",
    "target_cpu_utilization",
    "scale_up_stabilization_seconds",
    "scale_up_select_policy",
    "scale_up_policies",
    "scale_down_stabilization_seconds",
    "scale_down_select_policy",
    "scale_down_policies",
)

SUMMARY_AGGREGATE_FIELDS = (
    "requests_started",
    "responses_2xx",
    "responses_5xx",
    "connection_errors",
    "unexpected_failures",
    "unexpected_failures_percent",
    "dropped_iterations",
    "dropped_iterations_percent",
    "throughput_rps",
    "accepted_throughput_rps",
    "latency_average_ms",
    "latency_p95_ms",
    "latency_p99_ms",
    "response_2xx_average_ms",
    "response_2xx_p95_ms",
    "response_2xx_p99_ms",
    "orders_created",
    "orders_completed",
    "orders_pending",
    "orders_failed",
    "completion_rate_percent",
    "order_processing_p95_ms",
    "order_processing_p99_ms",
    "drain_duration_seconds",
    "restarts",
    "api_peak_cpu_cores",
    "api_peak_memory_mib",
    "max_backlog",
    "app_cpu_seconds_load",
    "app_memory_mib_minutes_load",
    "app_cpu_seconds_per_1000_completed_orders",
    "app_memory_mib_minutes_per_1000_completed_orders",
)

HPA_AGGREGATE_FIELDS = (
    "initial_api_replicas",
    "configured_min_replicas",
    "configured_max_replicas",
    "api_pods_observed_min",
    "api_pods_observed_max",
    "hpa_desired_replicas_min",
    "hpa_desired_replicas_max",
    "seconds_from_load_start_to_first_scale_request",
    "seconds_from_load_start_to_multiple_api_pods",
    "target_cpu_utilization",
    "scale_up_stabilization_seconds",
    "scale_down_stabilization_seconds",
)

AGGREGATE_OUTPUT_FIELDS = (
    "metric",
    "mean",
    "median",
    "sample_stddev",
    "minimum",
    "maximum",
    "aggregation_method",
)


class ConsolidationError(Exception):
    """Raised when official artifacts cannot be consolidated safely."""


def relative_path(path: Path) -> str:
    return path.relative_to(REPOSITORY_ROOT).as_posix()


def load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as source:
            value = json.load(source)
    except OSError as exc:
        raise ConsolidationError(
            f"nao foi possivel ler {relative_path(path)}: {exc}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise ConsolidationError(
            f"JSON invalido em {relative_path(path)}: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise ConsolidationError(
            f"JSON deve conter um objeto em {relative_path(path)}"
        )
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
        raise ConsolidationError(
            f"campo '{path}' deve ser um objeto em {relative_path(source)}"
        )
    return value


def sequence(data: Any, path: str, source: Path) -> list[Any]:
    value = field(data, path, source)
    if not isinstance(value, list):
        raise ConsolidationError(
            f"campo '{path}' deve ser uma lista em {relative_path(source)}"
        )
    return value


def text_value(data: Any, path: str, source: Path) -> str:
    value = field(data, path, source)
    if not isinstance(value, str) or not value.strip():
        raise ConsolidationError(
            f"campo '{path}' deve ser texto nao vazio em {relative_path(source)}"
        )
    return value


def number_value(
    data: Any,
    path: str,
    source: Path,
    *,
    integer: bool = False,
    nonnegative: bool = True,
) -> int | float:
    value = field(data, path, source)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConsolidationError(
            f"campo '{path}' deve ser numerico em {relative_path(source)}"
        )
    if not math.isfinite(float(value)):
        raise ConsolidationError(
            f"campo '{path}' deve ser finito em {relative_path(source)}"
        )
    if integer and not float(value).is_integer():
        raise ConsolidationError(
            f"campo '{path}' deve ser inteiro em {relative_path(source)}"
        )
    if nonnegative and value < 0:
        raise ConsolidationError(
            f"campo '{path}' nao pode ser negativo em {relative_path(source)}"
        )
    return int(value) if integer else float(value)


def boolean_value(data: Any, path: str, source: Path) -> bool:
    value = field(data, path, source)
    if not isinstance(value, bool):
        raise ConsolidationError(
            f"campo '{path}' deve ser booleano em {relative_path(source)}"
        )
    return value


def optional_metric_count(
    metrics: dict[str, Any], metric_name: str, source: Path
) -> int:
    if metric_name not in metrics:
        return 0
    return number_value(
        metrics, f"{metric_name}.count", source, integer=True
    )


def percentage(numerator: int, denominator: int) -> float:
    return round((numerator / denominator) * 100, 3) if denominator else 0.0


def rounded(value: int | float, digits: int) -> float:
    return round(float(value), digits)


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def parse_timestamp(value: Any, source: Path, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ConsolidationError(
            f"timestamp '{label}' invalido em {relative_path(source)}"
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ConsolidationError(
            f"timestamp '{label}' invalido em {relative_path(source)}: {value}"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ConsolidationError(
            f"timestamp '{label}' deve possuir fuso horario em "
            f"{relative_path(source)}"
        )
    return parsed


def resource_number(
    row: dict[str, str], column: str, source: Path, line_number: int
) -> float:
    try:
        value = float(row[column])
    except (KeyError, TypeError, ValueError) as exc:
        raise ConsolidationError(
            f"valor invalido em {relative_path(source)}, linha {line_number}, "
            f"coluna '{column}'"
        ) from exc
    if not math.isfinite(value) or value < 0:
        raise ConsolidationError(
            f"valor deve ser finito e nao negativo em {relative_path(source)}, "
            f"linha {line_number}, coluna '{column}'"
        )
    return value


def load_application_resource_series(
    source: Path,
) -> dict[str, list[tuple[datetime, float, float]]]:
    required_columns = {
        "timestamp",
        "pod",
        "container",
        "cpu_cores",
        "memory_mib",
    }
    grouped: dict[str, dict[datetime, list[float]]] = {
        "api": {},
        "worker": {},
    }
    try:
        with source.open(encoding="utf-8", newline="") as input_file:
            reader = csv.DictReader(input_file)
            if reader.fieldnames is None or not required_columns.issubset(
                reader.fieldnames
            ):
                raise ConsolidationError(
                    f"cabecalho invalido em {relative_path(source)}; esperado "
                    f"conter {sorted(required_columns)}"
                )
            for line_number, row in enumerate(reader, start=2):
                role = row["container"]
                if role not in grouped:
                    continue
                timestamp = parse_timestamp(
                    row["timestamp"], source, f"linha {line_number}"
                )
                totals = grouped[role].setdefault(timestamp, [0.0, 0.0])
                totals[0] += resource_number(
                    row, "cpu_cores", source, line_number
                )
                totals[1] += resource_number(
                    row, "memory_mib", source, line_number
                )
    except OSError as exc:
        raise ConsolidationError(
            f"nao foi possivel ler {relative_path(source)}: {exc}"
        ) from exc

    series = {}
    for role, samples in grouped.items():
        if not samples:
            raise ConsolidationError(
                f"serie do container '{role}' ausente em {relative_path(source)}"
            )
        series[role] = [
            (timestamp, values[0], values[1])
            for timestamp, values in sorted(samples.items())
        ]
    return series


def interpolate_sample(
    samples: list[tuple[datetime, float, float]],
    timestamp: datetime,
    value_index: int,
) -> float:
    timestamps = [sample[0] for sample in samples]
    position = bisect_left(timestamps, timestamp)
    if position < len(samples) and samples[position][0] == timestamp:
        return samples[position][value_index]
    left = samples[position - 1]
    right = samples[position]
    interval_seconds = (right[0] - left[0]).total_seconds()
    elapsed_seconds = (timestamp - left[0]).total_seconds()
    fraction = elapsed_seconds / interval_seconds
    return left[value_index] + (
        right[value_index] - left[value_index]
    ) * fraction


def trapezoidal_integral(
    samples: list[tuple[datetime, float, float]],
    started_at: datetime,
    finished_at: datetime,
    value_index: int,
) -> float:
    clipped_samples = [
        (started_at, interpolate_sample(samples, started_at, value_index)),
        *[
            (sample[0], sample[value_index])
            for sample in samples
            if started_at < sample[0] < finished_at
        ],
        (finished_at, interpolate_sample(samples, finished_at, value_index)),
    ]
    return sum(
        (right[0] - left[0]).total_seconds()
        * (left[1] + right[1])
        / 2
        for left, right in zip(clipped_samples, clipped_samples[1:])
    )


def application_load_consumption(
    metadata: dict[str, Any], metadata_path: Path, resources_path: Path
) -> tuple[float, float]:
    started_at = parse_timestamp(
        text_value(metadata, "timestamps.load_started_at", metadata_path),
        metadata_path,
        "timestamps.load_started_at",
    )
    finished_at = parse_timestamp(
        text_value(metadata, "timestamps.load_finished_at", metadata_path),
        metadata_path,
        "timestamps.load_finished_at",
    )
    if finished_at <= started_at:
        raise ConsolidationError(
            f"janela de carga invalida em {relative_path(metadata_path)}"
        )

    resource_series = load_application_resource_series(resources_path)
    cpu_seconds = 0.0
    memory_mib_seconds = 0.0
    for role, samples in resource_series.items():
        if samples[0][0] > started_at or samples[-1][0] < finished_at:
            raise ConsolidationError(
                f"{relative_path(resources_path)}: serie de '{role}' nao cobre "
                f"a janela {started_at.isoformat()} a {finished_at.isoformat()}; "
                f"cobertura observada {samples[0][0].isoformat()} a "
                f"{samples[-1][0].isoformat()}"
            )
        cpu_seconds += trapezoidal_integral(
            samples, started_at, finished_at, 1
        )
        memory_mib_seconds += trapezoidal_integral(
            samples, started_at, finished_at, 2
        )
    return cpu_seconds, memory_mib_seconds / 60


def discover_runs() -> list[Path]:
    if not OFFICIAL_RUNS_DIRECTORY.is_dir():
        raise ConsolidationError(
            "diretorio de execucoes oficiais ausente: "
            f"{relative_path(OFFICIAL_RUNS_DIRECTORY)}"
        )
    runs = sorted(
        (entry for entry in OFFICIAL_RUNS_DIRECTORY.iterdir() if entry.is_dir()),
        key=lambda entry: entry.name,
    )
    discovered_names = tuple(run.name for run in runs)
    if len(runs) != 3 or discovered_names != EXPECTED_RUN_NAMES:
        names = ", ".join(discovered_names) if discovered_names else "nenhuma"
        raise ConsolidationError(
            "devem existir exatamente as tres execucoes oficiais "
            f"{', '.join(EXPECTED_RUN_NAMES)}; encontradas: {names}"
        )
    return runs


def validate_required_files(runs: list[Path]) -> None:
    missing = [
        f"{run.name}/{required_file}"
        for run in runs
        for required_file in REQUIRED_FILES
        if not (run / required_file).is_file()
    ]
    if missing:
        raise ConsolidationError(
            "arquivos obrigatorios ausentes: " + ", ".join(missing)
        )


def load_run_artifacts(run: Path) -> dict[str, Any]:
    paths = {
        "metadata": run / "run-metadata.json",
        "k6": run / "k6/k6-summary.json",
        "stages": run / "k6/k6-stage-summary.json",
        "database": run / "database/db-summary.json",
        "drain": run / "rabbitmq/drain-summary.json",
        "collection": run / "metrics/collection-summary.json",
        "resources": run / "metrics/kubernetes-resources.csv",
        "hpa_samples": run / "metrics/hpa-samples.csv",
    }
    return {
        "paths": paths,
        **{
            name: load_json(path)
            for name, path in paths.items()
            if name not in {"resources", "hpa_samples"}
        },
    }


def validate_stage_profile(run: Path, artifacts: dict[str, Any]) -> None:
    metadata = artifacts["metadata"]
    stage_summary = artifacts["stages"]
    metadata_path = artifacts["paths"]["metadata"]
    stage_path = artifacts["paths"]["stages"]
    metadata_stages = sequence(metadata, "load.stages", metadata_path)
    summarized_stages = sequence(stage_summary, "stages", stage_path)
    if len(metadata_stages) != 4 or len(summarized_stages) != 4:
        raise ConsolidationError(
            f"{run.name}: perfil e resumo devem possuir quatro estagios"
        )
    for index, (metadata_stage, summarized_stage) in enumerate(
        zip(metadata_stages, summarized_stages), start=1
    ):
        expected_name = EXPECTED_STAGE_NAMES[index - 1]
        target = number_value(
            summarized_stage, "target", stage_path, integer=True
        )
        duration = text_value(summarized_stage, "duration", stage_path)
        metadata_target = number_value(
            metadata_stage, "target", metadata_path, integer=True
        )
        metadata_duration = text_value(
            metadata_stage, "duration", metadata_path
        )
        if text_value(summarized_stage, "name", stage_path) != expected_name:
            raise ConsolidationError(
                f"{run.name}: estagio {index} deve se chamar '{expected_name}'"
            )
        if target != metadata_target or target != EXPECTED_STAGE_TARGETS[index - 1]:
            raise ConsolidationError(
                f"{run.name}/{expected_name}: alvo diverge do perfil oficial"
            )
        if (
            duration != metadata_duration
            or duration != EXPECTED_STAGE_DURATIONS[index - 1]
        ):
            raise ConsolidationError(
                f"{run.name}/{expected_name}: duracao diverge do perfil oficial"
            )


def validate_hpa_configuration(
    run: Path, artifacts: dict[str, Any]
) -> dict[str, Any]:
    metadata = artifacts["metadata"]
    collection = artifacts["collection"]
    metadata_path = artifacts["paths"]["metadata"]
    collection_path = artifacts["paths"]["collection"]
    if not boolean_value(metadata, "autoscaling.enabled", metadata_path):
        raise ConsolidationError(f"{run.name}: autoscaling deve estar habilitado")

    required_metadata_values = {
        "autoscaling.min_replicas": 1,
        "autoscaling.max_replicas": 5,
        "autoscaling.target_cpu_utilization": 70,
        "autoscaling.initial_api_replicas": 1,
        "initial_replicas.api": 1,
    }
    for path, expected in required_metadata_values.items():
        observed = number_value(
            metadata, path, metadata_path, integer=True
        )
        if observed != expected:
            raise ConsolidationError(
                f"{run.name}: {path}={observed}; esperado {expected}"
            )

    config = mapping(collection, "hpa_configuration", collection_path)
    required_config_values: dict[str, Any] = {
        "name": "api-hpa",
        "target_kind": "Deployment",
        "target_name": "api",
        "min_replicas": 1,
        "max_replicas": 5,
        "target_cpu_utilization": 70,
    }
    for key, expected in required_config_values.items():
        observed = field(config, key, collection_path)
        if observed != expected:
            raise ConsolidationError(
                f"{run.name}: hpa_configuration.{key}={observed!r}; "
                f"esperado {expected!r}"
            )

    for key in (
        "scale_up_stabilization_seconds",
        "scale_down_stabilization_seconds",
    ):
        number_value(config, key, collection_path, integer=True)
    for key in ("scale_up_select_policy", "scale_down_select_policy"):
        text_value(config, key, collection_path)
    for key in ("scale_up_policies", "scale_down_policies"):
        policies = sequence(config, key, collection_path)
        if not policies:
            raise ConsolidationError(
                f"{run.name}: hpa_configuration.{key} nao pode ser vazio"
            )
        for index, policy in enumerate(policies, start=1):
            text_value(policy, "type", collection_path)
            number_value(
                policy, "value", collection_path, integer=True
            )
            number_value(
                policy, "periodSeconds", collection_path, integer=True
            )
    return config


def csv_integer(
    row: dict[str, str], column: str, source: Path, line_number: int
) -> int:
    try:
        value = int(row[column])
    except (KeyError, TypeError, ValueError) as exc:
        raise ConsolidationError(
            f"valor inteiro invalido em {relative_path(source)}, linha "
            f"{line_number}, coluna '{column}'"
        ) from exc
    return value


def validate_hpa_samples(
    run: Path, artifacts: dict[str, Any], config: dict[str, Any]
) -> None:
    source = artifacts["paths"]["hpa_samples"]
    collection = artifacts["collection"]
    collection_path = artifacts["paths"]["collection"]
    required_columns = {
        "timestamp",
        "hpa",
        "target_kind",
        "target_name",
        "desired_replicas",
        "min_replicas",
        "max_replicas",
        "target_cpu_utilization",
        "scale_up_stabilization_seconds",
        "scale_up_select_policy",
        "scale_up_policies",
        "scale_down_stabilization_seconds",
        "scale_down_select_policy",
        "scale_down_policies",
    }
    desired_values: list[int] = []
    first_desired_above_one: str | None = None
    try:
        with source.open(encoding="utf-8", newline="") as input_file:
            reader = csv.DictReader(input_file)
            if reader.fieldnames is None or not required_columns.issubset(
                reader.fieldnames
            ):
                raise ConsolidationError(
                    f"cabecalho invalido em {relative_path(source)}; esperado "
                    f"conter {sorted(required_columns)}"
                )
            for line_number, row in enumerate(reader, start=2):
                parse_timestamp(row["timestamp"], source, f"linha {line_number}")
                expected_text = {
                    "hpa": config["name"],
                    "target_kind": config["target_kind"],
                    "target_name": config["target_name"],
                    "scale_up_select_policy": config[
                        "scale_up_select_policy"
                    ],
                    "scale_down_select_policy": config[
                        "scale_down_select_policy"
                    ],
                }
                for column, expected in expected_text.items():
                    if row[column] != expected:
                        raise ConsolidationError(
                            f"{run.name}: {column} divergente na linha "
                            f"{line_number} de {relative_path(source)}"
                        )
                expected_numbers = {
                    "min_replicas": config["min_replicas"],
                    "max_replicas": config["max_replicas"],
                    "target_cpu_utilization": config[
                        "target_cpu_utilization"
                    ],
                    "scale_up_stabilization_seconds": config[
                        "scale_up_stabilization_seconds"
                    ],
                    "scale_down_stabilization_seconds": config[
                        "scale_down_stabilization_seconds"
                    ],
                }
                for column, expected in expected_numbers.items():
                    if csv_integer(row, column, source, line_number) != expected:
                        raise ConsolidationError(
                            f"{run.name}: {column} divergente na linha "
                            f"{line_number} de {relative_path(source)}"
                        )
                for column, config_key in (
                    ("scale_up_policies", "scale_up_policies"),
                    ("scale_down_policies", "scale_down_policies"),
                ):
                    try:
                        policies = json.loads(row[column])
                    except json.JSONDecodeError as exc:
                        raise ConsolidationError(
                            f"JSON invalido em {relative_path(source)}, linha "
                            f"{line_number}, coluna '{column}'"
                        ) from exc
                    if canonical_json(policies) != canonical_json(
                        config[config_key]
                    ):
                        raise ConsolidationError(
                            f"{run.name}: {column} divergente na linha "
                            f"{line_number} de {relative_path(source)}"
                        )
                desired = csv_integer(
                    row, "desired_replicas", source, line_number
                )
                desired_values.append(desired)
                if desired > 1 and first_desired_above_one is None:
                    first_desired_above_one = row["timestamp"]
    except OSError as exc:
        raise ConsolidationError(
            f"nao foi possivel ler {relative_path(source)}: {exc}"
        ) from exc

    if not desired_values:
        raise ConsolidationError(f"{run.name}: serie do HPA esta vazia")
    expected_min = number_value(
        collection,
        "hpa_desired_replicas_min",
        collection_path,
        integer=True,
    )
    expected_max = number_value(
        collection,
        "hpa_desired_replicas_max",
        collection_path,
        integer=True,
    )
    if min(desired_values) != expected_min or max(desired_values) != expected_max:
        raise ConsolidationError(
            f"{run.name}: extremos de replicas desejadas divergem do resumo"
        )
    summarized_first = text_value(
        collection,
        "hpa_first_desired_above_one_at",
        collection_path,
    )
    if first_desired_above_one != summarized_first:
        raise ConsolidationError(
            f"{run.name}: primeira solicitacao de escala diverge do resumo"
        )


def validate_run(run: Path, artifacts: dict[str, Any]) -> None:
    metadata = artifacts["metadata"]
    metadata_path = artifacts["paths"]["metadata"]
    run_type = text_value(metadata, "type", metadata_path).lower()
    metadata_id = text_value(metadata, "id", metadata_path)
    root_scenario = text_value(metadata, "scenario", metadata_path).lower()
    load_scenario = text_value(
        metadata, "load.scenario", metadata_path
    ).lower()
    validity = text_value(metadata, "validity.status", metadata_path).upper()
    execution_status = text_value(
        metadata, "execution_status", metadata_path
    ).upper()
    if run_type != "official":
        raise ConsolidationError(f"{run.name}: tipo deve ser 'official'")
    if metadata_id != run.name:
        raise ConsolidationError(
            f"{run.name}: id dos metadados diverge ('{metadata_id}')"
        )
    if root_scenario != "c2" or load_scenario != "c2":
        raise ConsolidationError(
            f"{run.name}: cenario deve ser c2 nos metadados e na carga"
        )
    if validity != "VALID" or execution_status != "VALID":
        raise ConsolidationError(
            f"{run.name}: execucao deve estar VALID; validity={validity}, "
            f"execution_status={execution_status}"
        )
    load_profile = mapping(metadata, "load", metadata_path)
    if load_profile != EXPECTED_LOAD_PROFILE:
        raise ConsolidationError(
            f"{run.name}: perfil de carga diverge do perfil oficial do C2"
        )
    validate_stage_profile(run, artifacts)
    config = validate_hpa_configuration(run, artifacts)
    validate_hpa_samples(run, artifacts, config)


def validate_run_consistency(
    loaded_runs: list[tuple[Path, dict[str, Any]]]
) -> None:
    load_profiles = {
        canonical_json(mapping(artifacts["metadata"], "load", artifacts["paths"]["metadata"]))
        for _, artifacts in loaded_runs
    }
    if len(load_profiles) != 1:
        raise ConsolidationError(
            "as tres execucoes devem usar exatamente o mesmo perfil de carga"
        )

    for role in ("api", "worker"):
        images = {
            text_value(
                artifacts["metadata"],
                f"images.{role}",
                artifacts["paths"]["metadata"],
            )
            for _, artifacts in loaded_runs
        }
        if len(images) != 1:
            raise ConsolidationError(
                f"as tres execucoes devem usar a mesma imagem de {role}"
            )

    configurations = {
        canonical_json(
            mapping(
                artifacts["collection"],
                "hpa_configuration",
                artifacts["paths"]["collection"],
            )
        )
        for _, artifacts in loaded_runs
    }
    if len(configurations) != 1:
        raise ConsolidationError(
            "as tres execucoes devem usar a mesma configuracao de HPA"
        )


def build_summary_row(run: Path, artifacts: dict[str, Any]) -> dict[str, Any]:
    metadata = artifacts["metadata"]
    k6 = artifacts["k6"]
    database = artifacts["database"]
    drain = artifacts["drain"]
    collection = artifacts["collection"]
    paths = artifacts["paths"]
    metrics = mapping(k6, "metrics", paths["k6"])
    requests = number_value(
        metrics, "http_reqs.count", paths["k6"], integer=True
    )
    responses_2xx = optional_metric_count(metrics, "responses_2xx", paths["k6"])
    responses_429 = optional_metric_count(metrics, "responses_429", paths["k6"])
    responses_5xx = optional_metric_count(metrics, "responses_5xx", paths["k6"])
    connection_errors = optional_metric_count(
        metrics, "connection_errors", paths["k6"]
    )
    unexpected_statuses = optional_metric_count(
        metrics, "unexpected_statuses", paths["k6"]
    )
    unexpected_failures = (
        responses_5xx + connection_errors + unexpected_statuses
    )
    http_req_failed_count = number_value(
        metrics, "http_req_failed.passes", paths["k6"], integer=True
    )
    http_req_failed_rate = number_value(
        metrics, "http_req_failed.value", paths["k6"]
    )
    if unexpected_failures != http_req_failed_count:
        raise ConsolidationError(
            f"{run.name}: falhas inesperadas divergem de http_req_failed"
        )
    expected_failure_rate = unexpected_failures / requests if requests else 0.0
    if not math.isclose(
        http_req_failed_rate,
        expected_failure_rate,
        rel_tol=1e-12,
        abs_tol=1e-12,
    ):
        raise ConsolidationError(
            f"{run.name}: taxa de falhas inesperadas inconsistente"
        )
    classified_requests = (
        responses_2xx
        + responses_429
        + responses_5xx
        + connection_errors
        + unexpected_statuses
    )
    if classified_requests != requests:
        raise ConsolidationError(
            f"{run.name}: classificacao das respostas soma "
            f"{classified_requests}, mas http_reqs.count={requests}"
        )

    dropped_iterations = optional_metric_count(
        metrics, "dropped_iterations", paths["k6"]
    )
    orders_by_status = mapping(
        database, "orders_by_status", paths["database"]
    )
    orders_created = number_value(
        database, "total_orders", paths["database"], integer=True
    )
    statuses = {
        status: number_value(
            orders_by_status, status, paths["database"], integer=True
        )
        for status in ("PENDING", "PROCESSING", "COMPLETED", "FAILED")
    }
    if sum(statuses.values()) != orders_created:
        raise ConsolidationError(
            f"{run.name}: pedidos por status divergem de total_orders"
        )
    completion_rate = number_value(
        database, "completion_rate_percent", paths["database"]
    )
    expected_completion_rate = percentage(statuses["COMPLETED"], orders_created)
    if not math.isclose(completion_rate, expected_completion_rate, abs_tol=0.001):
        raise ConsolidationError(
            f"{run.name}: completion_rate_percent inconsistente"
        )
    if statuses["COMPLETED"] == 0:
        raise ConsolidationError(
            f"{run.name}: nao ha pedidos concluidos para normalizar o consumo"
        )
    response_2xx_samples = number_value(
        metrics, "response_duration_2xx.count", paths["k6"], integer=True
    )
    if response_2xx_samples != responses_2xx:
        raise ConsolidationError(
            f"{run.name}: amostras de latencia 2xx divergem das respostas 2xx"
        )
    app_cpu_seconds, app_memory_mib_minutes = application_load_consumption(
        metadata, paths["metadata"], paths["resources"]
    )

    return {
        "run_id": run.name,
        "validity": text_value(
            metadata, "validity.status", paths["metadata"]
        ).upper(),
        "stability": text_value(
            metadata, "stability.status", paths["metadata"]
        ).upper(),
        "requests_started": requests,
        "responses_2xx": responses_2xx,
        "responses_5xx": responses_5xx,
        "connection_errors": connection_errors,
        "unexpected_failures": unexpected_failures,
        "unexpected_failures_percent": rounded(http_req_failed_rate * 100, 3),
        "dropped_iterations": dropped_iterations,
        "dropped_iterations_percent": percentage(
            dropped_iterations, requests + dropped_iterations
        ),
        "throughput_rps": rounded(
            number_value(metrics, "http_reqs.rate", paths["k6"]), 6
        ),
        "accepted_throughput_rps": rounded(
            number_value(metrics, "responses_2xx.rate", paths["k6"]), 6
        ),
        "latency_average_ms": rounded(
            number_value(metrics, "http_req_duration.avg", paths["k6"]), 3
        ),
        "latency_p95_ms": rounded(
            number_value(metrics, "http_req_duration.p(95)", paths["k6"]), 3
        ),
        "latency_p99_ms": rounded(
            number_value(metrics, "http_req_duration.p(99)", paths["k6"]), 3
        ),
        "response_2xx_average_ms": rounded(
            number_value(metrics, "response_duration_2xx.avg", paths["k6"]), 3
        ),
        "response_2xx_p95_ms": rounded(
            number_value(metrics, "response_duration_2xx.p(95)", paths["k6"]),
            3,
        ),
        "response_2xx_p99_ms": rounded(
            number_value(metrics, "response_duration_2xx.p(99)", paths["k6"]),
            3,
        ),
        "orders_created": orders_created,
        "orders_completed": statuses["COMPLETED"],
        "orders_pending": statuses["PENDING"],
        "orders_failed": statuses["FAILED"],
        "completion_rate_percent": rounded(completion_rate, 3),
        "order_processing_p95_ms": rounded(
            number_value(database, "processing_time_ms.p95", paths["database"]),
            3,
        ),
        "order_processing_p99_ms": rounded(
            number_value(database, "processing_time_ms.p99", paths["database"]),
            3,
        ),
        "drain_duration_seconds": number_value(
            drain, "drain_duration_seconds", paths["drain"], integer=True
        ),
        "drain_completed": boolean_value(drain, "completed", paths["drain"]),
        "drain_objective_met": boolean_value(
            drain, "objective_met", paths["drain"]
        ),
        "restarts": number_value(
            collection,
            "restart_delta_total",
            paths["collection"],
            integer=True,
        ),
        "api_peak_cpu_cores": rounded(
            number_value(
                collection,
                "resource_peaks.api.max_cpu_cores",
                paths["collection"],
            ),
            9,
        ),
        "api_peak_memory_mib": rounded(
            number_value(
                collection,
                "resource_peaks.api.max_memory_mib",
                paths["collection"],
            ),
            6,
        ),
        "max_backlog": number_value(
            collection, "queue.max_messages", paths["collection"], integer=True
        ),
        "app_cpu_seconds_load": rounded(app_cpu_seconds, 6),
        "app_memory_mib_minutes_load": rounded(app_memory_mib_minutes, 6),
        "app_cpu_seconds_per_1000_completed_orders": rounded(
            app_cpu_seconds / statuses["COMPLETED"] * 1000, 6
        ),
        "app_memory_mib_minutes_per_1000_completed_orders": rounded(
            app_memory_mib_minutes / statuses["COMPLETED"] * 1000, 6
        ),
    }


def nullable_number(
    data: Any, path: str, source: Path, *, digits: int = 3
) -> float | None:
    value = field(data, path, source)
    if value is None:
        return None
    return rounded(number_value(data, path, source), digits)


def build_stage_rows(
    run: Path, artifacts: dict[str, Any]
) -> list[dict[str, Any]]:
    stage_summary = artifacts["stages"]
    stage_path = artifacts["paths"]["stages"]
    rows = []
    for stage in sequence(stage_summary, "stages", stage_path):
        metrics = mapping(stage, "metrics", stage_path)
        requests = number_value(metrics, "requests", stage_path, integer=True)
        responses_2xx = number_value(
            metrics, "responses_2xx", stage_path, integer=True
        )
        responses_429 = number_value(
            metrics, "responses_429", stage_path, integer=True
        )
        responses_5xx = number_value(
            metrics, "responses_5xx", stage_path, integer=True
        )
        connection_errors = number_value(
            metrics, "connection_errors", stage_path, integer=True
        )
        unexpected_statuses = number_value(
            metrics, "unexpected_statuses", stage_path, integer=True
        )
        if (
            responses_2xx
            + responses_429
            + responses_5xx
            + connection_errors
            + unexpected_statuses
            != requests
        ):
            raise ConsolidationError(
                f"{run.name}/{stage.get('name', 'estagio')}: classificacao "
                "das respostas nao corresponde ao total de requisicoes"
            )
        rows.append(
            {
                "run_id": run.name,
                "stage": text_value(stage, "name", stage_path),
                "target_rps": number_value(
                    stage, "target", stage_path, integer=True
                ),
                "duration_seconds": rounded(
                    number_value(stage, "duration_seconds", stage_path), 3
                ),
                "requests_started": requests,
                "responses_2xx": responses_2xx,
                "responses_5xx": responses_5xx,
                "connection_errors": connection_errors,
                "dropped_iterations": number_value(
                    metrics, "dropped_iterations", stage_path, integer=True
                ),
                "latency_average_ms": nullable_number(
                    metrics, "latency_ms.all.average", stage_path
                ),
                "latency_p95_ms": nullable_number(
                    metrics, "latency_ms.all.p95", stage_path
                ),
                "latency_p99_ms": nullable_number(
                    metrics, "latency_ms.all.p99", stage_path
                ),
                "response_2xx_average_ms": nullable_number(
                    metrics, "latency_ms.responses_2xx.average", stage_path
                ),
                "response_2xx_p95_ms": nullable_number(
                    metrics, "latency_ms.responses_2xx.p95", stage_path
                ),
                "response_2xx_p99_ms": nullable_number(
                    metrics, "latency_ms.responses_2xx.p99", stage_path
                ),
            }
        )
    return rows


def build_hpa_row(run: Path, artifacts: dict[str, Any]) -> dict[str, Any]:
    metadata = artifacts["metadata"]
    collection = artifacts["collection"]
    metadata_path = artifacts["paths"]["metadata"]
    collection_path = artifacts["paths"]["collection"]
    config = mapping(collection, "hpa_configuration", collection_path)
    load_started_text = text_value(
        metadata, "timestamps.load_started_at", metadata_path
    )
    first_scale_text = text_value(
        collection,
        "hpa_first_desired_above_one_at",
        collection_path,
    )
    multiple_pods_text = text_value(
        collection,
        "api_first_multiple_pods_observed_at",
        collection_path,
    )
    load_started = parse_timestamp(
        load_started_text, metadata_path, "timestamps.load_started_at"
    )
    first_scale = parse_timestamp(
        first_scale_text,
        collection_path,
        "hpa_first_desired_above_one_at",
    )
    multiple_pods = parse_timestamp(
        multiple_pods_text,
        collection_path,
        "api_first_multiple_pods_observed_at",
    )
    seconds_to_scale = (first_scale - load_started).total_seconds()
    seconds_to_multiple = (multiple_pods - load_started).total_seconds()
    if seconds_to_scale < 0 or seconds_to_multiple < 0:
        raise ConsolidationError(
            f"{run.name}: evento de escala anterior ao inicio da carga"
        )
    row = {
        "run_id": run.name,
        "hpa_name": text_value(config, "name", collection_path),
        "target_kind": text_value(config, "target_kind", collection_path),
        "target_name": text_value(config, "target_name", collection_path),
        "initial_api_replicas": number_value(
            metadata, "initial_replicas.api", metadata_path, integer=True
        ),
        "configured_min_replicas": number_value(
            config, "min_replicas", collection_path, integer=True
        ),
        "configured_max_replicas": number_value(
            config, "max_replicas", collection_path, integer=True
        ),
        "api_pods_observed_min": number_value(
            collection, "api_pod_count_min", collection_path, integer=True
        ),
        "api_pods_observed_max": number_value(
            collection, "api_pod_count_max", collection_path, integer=True
        ),
        "hpa_desired_replicas_min": number_value(
            collection,
            "hpa_desired_replicas_min",
            collection_path,
            integer=True,
        ),
        "hpa_desired_replicas_max": number_value(
            collection,
            "hpa_desired_replicas_max",
            collection_path,
            integer=True,
        ),
        "first_scale_request_at": first_scale_text,
        "seconds_from_load_start_to_first_scale_request": rounded(
            seconds_to_scale, 3
        ),
        "multiple_api_pods_observed_at": multiple_pods_text,
        "seconds_from_load_start_to_multiple_api_pods": rounded(
            seconds_to_multiple, 3
        ),
        "target_cpu_utilization": number_value(
            config, "target_cpu_utilization", collection_path, integer=True
        ),
        "scale_up_stabilization_seconds": number_value(
            config,
            "scale_up_stabilization_seconds",
            collection_path,
            integer=True,
        ),
        "scale_up_select_policy": text_value(
            config, "scale_up_select_policy", collection_path
        ),
        "scale_up_policies": canonical_json(
            sequence(config, "scale_up_policies", collection_path)
        ),
        "scale_down_stabilization_seconds": number_value(
            config,
            "scale_down_stabilization_seconds",
            collection_path,
            integer=True,
        ),
        "scale_down_select_policy": text_value(
            config, "scale_down_select_policy", collection_path
        ),
        "scale_down_policies": canonical_json(
            sequence(config, "scale_down_policies", collection_path)
        ),
    }
    if row["api_pods_observed_min"] != 1:
        raise ConsolidationError(
            f"{run.name}: minimo observado de Pods da API deve ser 1"
        )
    if row["hpa_desired_replicas_min"] != 1:
        raise ConsolidationError(
            f"{run.name}: minimo de replicas desejadas deve ser 1"
        )
    return row


def build_aggregate_rows(
    summary_rows: list[dict[str, Any]],
    hpa_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    sources = {
        **{metric: summary_rows for metric in SUMMARY_AGGREGATE_FIELDS},
        **{metric: hpa_rows for metric in HPA_AGGREGATE_FIELDS},
    }
    aggregate_rows = []
    for metric, rows in sources.items():
        values = [float(row[metric]) for row in rows]
        is_percentile = "_p95_" in metric or "_p99_" in metric
        method = (
            "run-level percentiles; raw samples were not pooled"
            if is_percentile
            else "statistics across the three official run-level values"
        )
        aggregate_rows.append(
            {
                "metric": metric,
                "mean": rounded(statistics.mean(values), 6),
                "median": rounded(statistics.median(values), 6),
                "sample_stddev": rounded(statistics.stdev(values), 6),
                "minimum": rounded(min(values), 6),
                "maximum": rounded(max(values), 6),
                "aggregation_method": method,
            }
        )
    return aggregate_rows


def validate_stage_totals(
    summary_rows: list[dict[str, Any]],
    stage_rows: list[dict[str, Any]],
) -> None:
    for summary_row in summary_rows:
        run_stage_rows = [
            row
            for row in stage_rows
            if row["run_id"] == summary_row["run_id"]
        ]
        for metric in (
            "requests_started",
            "responses_2xx",
            "responses_5xx",
            "connection_errors",
            "dropped_iterations",
        ):
            stage_total = sum(row[metric] for row in run_stage_rows)
            if stage_total != summary_row[metric]:
                raise ConsolidationError(
                    f"{summary_row['run_id']}: soma por estagio de {metric} "
                    f"({stage_total}) diverge do resumo ({summary_row[metric]})"
                )


def csv_value(value: Any) -> Any:
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, float):
        return format(value, ".9f").rstrip("0").rstrip(".") or "0"
    if value is None:
        return ""
    return value


def write_csv(
    path: Path,
    fieldnames: tuple[str, ...],
    rows: list[dict[str, Any]],
) -> None:
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(
            output,
            fieldnames=fieldnames,
            extrasaction="raise",
            lineterminator="\n",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {key: csv_value(value) for key, value in row.items()}
            )


def consolidate() -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    runs = discover_runs()
    validate_required_files(runs)
    loaded_runs = [(run, load_run_artifacts(run)) for run in runs]
    for run, artifacts in loaded_runs:
        validate_run(run, artifacts)
    validate_run_consistency(loaded_runs)

    summary_rows = [
        build_summary_row(run, artifacts) for run, artifacts in loaded_runs
    ]
    stage_rows = [
        row
        for run, artifacts in loaded_runs
        for row in build_stage_rows(run, artifacts)
    ]
    hpa_rows = [
        build_hpa_row(run, artifacts) for run, artifacts in loaded_runs
    ]
    validate_stage_totals(summary_rows, stage_rows)
    aggregate_rows = build_aggregate_rows(summary_rows, hpa_rows)

    OUTPUT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    write_csv(
        OUTPUT_DIRECTORY / "c2-summary.csv", SUMMARY_FIELDS, summary_rows
    )
    write_csv(
        OUTPUT_DIRECTORY / "c2-stage-summary.csv", STAGE_FIELDS, stage_rows
    )
    write_csv(
        OUTPUT_DIRECTORY / "c2-hpa-summary.csv", HPA_FIELDS, hpa_rows
    )
    write_csv(
        OUTPUT_DIRECTORY / "c2-aggregate-summary.csv",
        AGGREGATE_OUTPUT_FIELDS,
        aggregate_rows,
    )
    return summary_rows, stage_rows, hpa_rows


def main() -> int:
    try:
        summary_rows, stage_rows, hpa_rows = consolidate()
    except ConsolidationError as exc:
        print(f"Erro de consolidacao do C2: {exc}", file=sys.stderr)
        return 1

    print(
        f"C2 consolidado: {len(summary_rows)} execucoes, "
        f"{len(stage_rows)} estagios e {len(hpa_rows)} resumos de HPA."
    )
    for filename in (
        "c2-summary.csv",
        "c2-stage-summary.csv",
        "c2-hpa-summary.csv",
        "c2-aggregate-summary.csv",
    ):
        print(relative_path(OUTPUT_DIRECTORY / filename))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
