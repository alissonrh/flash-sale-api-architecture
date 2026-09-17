#!/usr/bin/env python3
"""Consolidate the three official C1 experiment runs into reproducible CSVs."""

import csv
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
from bisect import bisect_left
from datetime import datetime
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
OFFICIAL_RUNS_DIRECTORY = REPOSITORY_ROOT / "results/experiments/c1/official"
OUTPUT_DIRECTORY = REPOSITORY_ROOT / "results/consolidated"
WORKBOOK_PATH = OUTPUT_DIRECTORY / "c1-planilha-experimentos.xlsx"

WORKBOOK_SHEETS = (
    (
        "c1-summary.csv",
        "Execuções",
        "C1SummaryTable",
        "#17365D",
    ),
    (
        "c1-stage-summary.csv",
        "Estágios",
        "C1StageSummaryTable",
        "#17365D",
    ),
    (
        "c1-aggregate-summary.csv",
        "Agregados",
        "C1AggregateSummaryTable",
        "#17365D",
    ),
)

WORKBOOK_BUILDER_JS = r"""
import { FileBlob, SpreadsheetFile, Workbook } from "@oai/artifact-tool";
import fs from "node:fs/promises";

const specification = JSON.parse(
  await fs.readFile(process.env.C1_WORKBOOK_SPECIFICATION, "utf8"),
);
const outputPath = process.env.C1_WORKBOOK_OUTPUT;
const workbook = Workbook.create();

function columnLetter(columnNumber) {
  let result = "";
  while (columnNumber > 0) {
    columnNumber -= 1;
    result = String.fromCharCode(65 + (columnNumber % 26)) + result;
    columnNumber = Math.floor(columnNumber / 26);
  }
  return result;
}

for (const definition of specification) {
  const sheet = workbook.worksheets.add(definition.sheetName);
  const usedRange = sheet.getRange(definition.address);
  usedRange.values = definition.rows;
  usedRange.format.font = {
    name: "Arial",
    size: 10,
    color: "#404040",
  };
  usedRange.format.verticalAlignment = "center";

  const table = sheet.tables.add(
    definition.address,
    true,
    definition.tableName,
  );
  table.style = "TableStyleMedium2";
  table.showHeaders = true;
  table.showTotals = false;
  table.showBandedColumns = false;
  table.showFilterButton = true;

  const lastColumn = columnLetter(definition.rows[0].length);
  const header = sheet.getRange(`A1:${lastColumn}1`);
  header.format = {
    fill: definition.headerColor,
    font: {
      name: "Arial",
      size: 10,
      bold: true,
      color: "#FFFFFF",
    },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    wrapText: true,
    borders: {
      preset: "all",
      style: "thin",
      color: "#FFFFFF",
    },
  };
  header.format.rowHeight = 48;

  if (definition.rows.length > 1) {
    const body = sheet.getRange(`A2:${lastColumn}${definition.rows.length}`);
    body.format.rowHeight = 20;
    body.format.wrapText = false;
    body.format.numberFormat = definition.numberFormats;
  }

  for (let index = 0; index < definition.widths.length; index += 1) {
    const column = columnLetter(index + 1);
    sheet.getRange(`${column}1:${column}${definition.rows.length}`)
      .format.columnWidth = definition.widths[index];
  }

  sheet.freezePanes.freezeRows(1);
  sheet.showGridLines = true;
  sheet.tabColor = definition.headerColor;
}

workbook.recalculate();
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);

const saved = await FileBlob.load(outputPath);
const verificationWorkbook = await SpreadsheetFile.importXlsx(saved);
if (verificationWorkbook.worksheets.items.length !== specification.length) {
  throw new Error("quantidade de abas diverge da especificacao");
}

for (let index = 0; index < specification.length; index += 1) {
  const definition = specification[index];
  const sheet = verificationWorkbook.worksheets.getItemAt(index);
  if (sheet.name !== definition.sheetName) {
    throw new Error(
      `aba ${index + 1}: esperado ${definition.sheetName}, obtido ${sheet.name}`,
    );
  }
  const actualRows = sheet.getRange(definition.address).values;
  if (JSON.stringify(actualRows) !== JSON.stringify(definition.rows)) {
    throw new Error(`conteudo da aba ${definition.sheetName} diverge do CSV`);
  }
}
"""

WORKBOOK_VALIDATOR_JS = r"""
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";
import fs from "node:fs/promises";

const specification = JSON.parse(
  await fs.readFile(process.env.C1_WORKBOOK_SPECIFICATION, "utf8"),
);
const saved = await FileBlob.load(process.env.C1_WORKBOOK_OUTPUT);
const workbook = await SpreadsheetFile.importXlsx(saved);
if (workbook.worksheets.items.length !== specification.length) {
  throw new Error("quantidade de abas diverge da especificacao");
}

for (let index = 0; index < specification.length; index += 1) {
  const definition = specification[index];
  const sheet = workbook.worksheets.getItemAt(index);
  if (sheet.name !== definition.sheetName) {
    throw new Error(
      `aba ${index + 1}: esperado ${definition.sheetName}, obtido ${sheet.name}`,
    );
  }
  const actualRows = sheet.getRange(definition.address).values;
  if (JSON.stringify(actualRows) !== JSON.stringify(definition.rows)) {
    throw new Error(`conteudo da aba ${definition.sheetName} diverge do CSV`);
  }
  const tables = sheet.tables.items;
  if (
    tables.length !== 1 ||
    tables[0].name !== definition.tableName ||
    tables[0].style !== "TableStyleMedium2" ||
    !tables[0].showFilterButton
  ) {
    throw new Error(`tabela ou filtro invalido na aba ${definition.sheetName}`);
  }
}
"""

EXPECTED_RUN_NAMES = ("c1-run-1", "c1-run-2", "c1-run-3")
EXPECTED_STAGE_NAMES = ("stage_1", "stage_2", "stage_3", "stage_4")
EXPECTED_STAGE_TARGETS = (20, 22, 22, 0)
EXPECTED_STAGE_DURATIONS = ("20s", "30s", "30s", "10s")

# Keep this list aligned with validate_required_files in run-k8s-experiment.sh.
RUNNER_REQUIRED_FILES = (
    "run-metadata.json",
    "k6/k6-summary.json",
    "k6/k6-stage-summary.json",
    "k6/k6-timeseries.jsonl",
    "k6/k6.log",
    "metrics/kubernetes-resources.csv",
    "metrics/kubernetes-pods.csv",
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

# This collector output is also a required input for the consolidated resource,
# restart and backlog metrics.
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
    "latency_p95_ms",
    "latency_p99_ms",
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
    "latency_p95_ms",
    "latency_p99_ms",
    "response_2xx_p95_ms",
    "response_2xx_p99_ms",
)

AGGREGATE_FIELDS = (
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
    "latency_p95_ms",
    "latency_p99_ms",
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
            f"campo '{path}' deve ser um texto nao vazio em "
            f"{relative_path(source)}"
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
    if integer and (not float(value).is_integer()):
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
        metrics,
        f"{metric_name}.count",
        source,
        integer=True,
    )


def percentage(numerator: int, denominator: int) -> float:
    return round((numerator / denominator) * 100, 3) if denominator else 0.0


def rounded(value: int | float, digits: int) -> float:
    return round(float(value), digits)


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
    return left[value_index] + (right[value_index] - left[value_index]) * fraction


def trapezoidal_integral(
    samples: list[tuple[datetime, float, float]],
    started_at: datetime,
    finished_at: datetime,
    value_index: int,
) -> float:
    clipped_samples = [
        (
            started_at,
            interpolate_sample(samples, started_at, value_index),
        ),
        *[
            (sample[0], sample[value_index])
            for sample in samples
            if started_at < sample[0] < finished_at
        ],
        (
            finished_at,
            interpolate_sample(samples, finished_at, value_index),
        ),
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
    }
    return {
        "paths": paths,
        **{
            name: load_json(path)
            for name, path in paths.items()
            if name != "resources"
        },
    }


def validate_run(run: Path, artifacts: dict[str, Any]) -> None:
    metadata = artifacts["metadata"]
    stage_summary = artifacts["stages"]
    paths = artifacts["paths"]
    metadata_path = paths["metadata"]
    stage_path = paths["stages"]

    run_type = text_value(metadata, "type", metadata_path).lower()
    metadata_id = text_value(metadata, "id", metadata_path)
    validity = text_value(metadata, "validity.status", metadata_path).upper()
    execution_status = text_value(
        metadata, "execution_status", metadata_path
    ).upper()
    if run_type != "official":
        raise ConsolidationError(
            f"{run.name}: tipo deve ser 'official', encontrado '{run_type}'"
        )
    if metadata_id != run.name:
        raise ConsolidationError(
            f"{run.name}: id dos metadados diverge ('{metadata_id}')"
        )
    if validity != "VALID" or execution_status != "VALID":
        raise ConsolidationError(
            f"{run.name}: execucao deve estar VALID; validity={validity}, "
            f"execution_status={execution_status}"
        )

    metadata_stages = sequence(metadata, "load.stages", metadata_path)
    if len(metadata_stages) != len(EXPECTED_STAGE_TARGETS):
        raise ConsolidationError(
            f"{run.name}: perfil deve possuir quatro estagios"
        )
    metadata_targets = tuple(
        number_value(
            stage,
            "target",
            metadata_path,
            integer=True,
        )
        for stage in metadata_stages
    )
    if metadata_targets != EXPECTED_STAGE_TARGETS:
        raise ConsolidationError(
            f"{run.name}: perfil invalido {metadata_targets}; esperado "
            f"{EXPECTED_STAGE_TARGETS} req/s"
        )
    metadata_durations = tuple(
        text_value(stage, "duration", metadata_path)
        for stage in metadata_stages
    )
    if metadata_durations != EXPECTED_STAGE_DURATIONS:
        raise ConsolidationError(
            f"{run.name}: duracoes do perfil invalidas {metadata_durations}; "
            f"esperado {EXPECTED_STAGE_DURATIONS}"
        )

    summarized_stages = sequence(stage_summary, "stages", stage_path)
    if len(summarized_stages) != len(EXPECTED_STAGE_TARGETS):
        raise ConsolidationError(
            f"{run.name}: resumo por estagio deve possuir quatro estagios"
        )
    for index, (metadata_stage, summarized_stage) in enumerate(
        zip(metadata_stages, summarized_stages), start=1
    ):
        expected_name = EXPECTED_STAGE_NAMES[index - 1]
        stage_name = text_value(summarized_stage, "name", stage_path)
        target = number_value(
            summarized_stage,
            "target",
            stage_path,
            integer=True,
        )
        duration = text_value(summarized_stage, "duration", stage_path)
        metadata_duration = text_value(metadata_stage, "duration", metadata_path)
        if stage_name != expected_name:
            raise ConsolidationError(
                f"{run.name}: estagio {index} deve se chamar '{expected_name}', "
                f"encontrado '{stage_name}'"
            )
        if target != EXPECTED_STAGE_TARGETS[index - 1]:
            raise ConsolidationError(
                f"{run.name}/{stage_name}: alvo {target} diverge do perfil "
                f"esperado {EXPECTED_STAGE_TARGETS[index - 1]} req/s"
            )
        if duration != metadata_duration:
            raise ConsolidationError(
                f"{run.name}/{stage_name}: duracao '{duration}' diverge dos "
                f"metadados ('{metadata_duration}')"
            )


def validate_images(loaded_runs: list[tuple[Path, dict[str, Any]]]) -> None:
    images_by_role: dict[str, list[str]] = {"api": [], "worker": []}
    for run, artifacts in loaded_runs:
        metadata = artifacts["metadata"]
        metadata_path = artifacts["paths"]["metadata"]
        for role in images_by_role:
            image = text_value(metadata, f"images.{role}", metadata_path)
            images_by_role[role].append(image)

    for role, images in images_by_role.items():
        if len(set(images)) != 1:
            details = ", ".join(
                f"{run.name}={image}"
                for (run, _), image in zip(loaded_runs, images)
            )
            raise ConsolidationError(
                f"as tres execucoes devem usar a mesma imagem de {role}: "
                f"{details}"
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
            f"{run.name}: falhas inesperadas somam {unexpected_failures}, mas "
            f"http_req_failed.passes={http_req_failed_count}"
        )
    expected_failure_rate = unexpected_failures / requests if requests else 0.0
    if not math.isclose(
        http_req_failed_rate,
        expected_failure_rate,
        rel_tol=1e-12,
        abs_tol=1e-12,
    ):
        raise ConsolidationError(
            f"{run.name}: http_req_failed.value={http_req_failed_rate} diverge "
            f"de unexpected_failures/http_reqs.count={expected_failure_rate}"
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
            orders_by_status,
            status,
            paths["database"],
            integer=True,
        )
        for status in ("PENDING", "PROCESSING", "COMPLETED", "FAILED")
    }
    if sum(statuses.values()) != orders_created:
        raise ConsolidationError(
            f"{run.name}: pedidos por status somam {sum(statuses.values())}, "
            f"mas total_orders={orders_created}"
        )

    completion_rate = number_value(
        database,
        "completion_rate_percent",
        paths["database"],
    )
    expected_completion_rate = percentage(statuses["COMPLETED"], orders_created)
    if not math.isclose(completion_rate, expected_completion_rate, abs_tol=0.001):
        raise ConsolidationError(
            f"{run.name}: completion_rate_percent={completion_rate} diverge de "
            f"COMPLETED/total_orders={expected_completion_rate}"
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
            f"{run.name}: response_duration_2xx.count={response_2xx_samples} "
            f"diverge de responses_2xx.count={responses_2xx}"
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
        "unexpected_failures_percent": rounded(
            http_req_failed_rate * 100, 3
        ),
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
        "latency_p95_ms": rounded(
            number_value(metrics, "http_req_duration.p(95)", paths["k6"]), 3
        ),
        "latency_p99_ms": rounded(
            number_value(metrics, "http_req_duration.p(99)", paths["k6"]), 3
        ),
        "response_2xx_p95_ms": rounded(
            number_value(
                metrics, "response_duration_2xx.p(95)", paths["k6"]
            ),
            3,
        ),
        "response_2xx_p99_ms": rounded(
            number_value(
                metrics, "response_duration_2xx.p(99)", paths["k6"]
            ),
            3,
        ),
        "orders_created": orders_created,
        "orders_completed": statuses["COMPLETED"],
        "orders_pending": statuses["PENDING"],
        "orders_failed": statuses["FAILED"],
        "completion_rate_percent": rounded(completion_rate, 3),
        "order_processing_p95_ms": rounded(
            number_value(
                database,
                "processing_time_ms.p95",
                paths["database"],
            ),
            3,
        ),
        "order_processing_p99_ms": rounded(
            number_value(
                database,
                "processing_time_ms.p99",
                paths["database"],
            ),
            3,
        ),
        "drain_duration_seconds": number_value(
            drain,
            "drain_duration_seconds",
            paths["drain"],
            integer=True,
        ),
        "drain_completed": boolean_value(
            drain, "completed", paths["drain"]
        ),
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
            collection,
            "queue.max_messages",
            paths["collection"],
            integer=True,
        ),
        "app_cpu_seconds_load": rounded(app_cpu_seconds, 6),
        "app_memory_mib_minutes_load": rounded(
            app_memory_mib_minutes, 6
        ),
        "app_cpu_seconds_per_1000_completed_orders": rounded(
            app_cpu_seconds / statuses["COMPLETED"] * 1000,
            6,
        ),
        "app_memory_mib_minutes_per_1000_completed_orders": rounded(
            app_memory_mib_minutes / statuses["COMPLETED"] * 1000,
            6,
        ),
    }


def nullable_number(
    data: Any,
    path: str,
    source: Path,
    *,
    digits: int = 3,
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
                f"{run.name}/{stage.get('name', 'estagio desconhecido')}: "
                "classificacao das respostas nao corresponde ao total de "
                "requisicoes"
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
                "latency_p95_ms": nullable_number(
                    metrics, "latency_ms.all.p95", stage_path
                ),
                "latency_p99_ms": nullable_number(
                    metrics, "latency_ms.all.p99", stage_path
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


def build_aggregate_rows(
    summary_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    aggregate_rows = []
    for metric in AGGREGATE_FIELDS:
        # Percentile fields are run-level values here. They are deliberately
        # summarized without pooling request or order samples across runs.
        values = [float(row[metric]) for row in summary_rows]
        aggregate_rows.append(
            {
                "metric": metric,
                "mean": rounded(statistics.mean(values), 6),
                "median": rounded(statistics.median(values), 6),
                "sample_stddev": rounded(statistics.stdev(values), 6),
                "minimum": rounded(min(values), 6),
                "maximum": rounded(max(values), 6),
            }
        )
    return aggregate_rows


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
            writer.writerow({key: csv_value(value) for key, value in row.items()})


def spreadsheet_value(value: str) -> str | int | float | None:
    if value == "":
        return None
    if value in {"true", "false"}:
        return value
    try:
        if value.lstrip("-").isdigit():
            return int(value)
        number = float(value)
    except ValueError:
        return value
    return number if math.isfinite(number) else value


def spreadsheet_number_format(value: str) -> str:
    parsed = spreadsheet_value(value)
    if not isinstance(parsed, (int, float)) or isinstance(parsed, bool):
        return "General"
    decimal_places = len(value.split(".", maxsplit=1)[1]) if "." in value else 0
    if decimal_places == 0:
        return "0"
    return "0." + "0" * decimal_places


def excel_column_name(column_number: int) -> str:
    result = ""
    while column_number:
        column_number, remainder = divmod(column_number - 1, 26)
        result = chr(65 + remainder) + result
    return result


def workbook_sheet_definition(
    csv_path: Path,
    sheet_name: str,
    table_name: str,
    header_color: str,
) -> dict[str, Any]:
    try:
        with csv_path.open(encoding="utf-8", newline="") as source:
            rows = list(csv.reader(source))
    except OSError as exc:
        raise ConsolidationError(
            f"nao foi possivel ler {relative_path(csv_path)}: {exc}"
        ) from exc

    if len(rows) < 2 or not rows[0]:
        raise ConsolidationError(
            f"CSV vazio ou sem dados em {relative_path(csv_path)}"
        )
    column_count = len(rows[0])
    if any(len(row) != column_count for row in rows):
        raise ConsolidationError(
            f"linhas com quantidades de colunas diferentes em "
            f"{relative_path(csv_path)}"
        )

    columns = list(zip(*rows))
    widths = []
    for index, column in enumerate(columns):
        header = column[0]
        maximum_length = max(len(value) for value in column)
        if index == 0 and header == "metric":
            widths.append(min(max(maximum_length + 2, 18), 52))
        else:
            widths.append(min(max(maximum_length + 2, 12), 24))

    typed_rows = [
        list(rows[0]),
        *[
            [spreadsheet_value(value) for value in row]
            for row in rows[1:]
        ],
    ]
    return {
        "source": relative_path(csv_path),
        "sheetName": sheet_name,
        "tableName": table_name,
        "headerColor": header_color,
        "address": (
            f"A1:{excel_column_name(column_count)}{len(typed_rows)}"
        ),
        "rows": typed_rows,
        "widths": widths,
        "numberFormats": [
            [spreadsheet_number_format(value) for value in row]
            for row in rows[1:]
        ],
    }


def artifact_tool_node_modules() -> Path:
    configured = os.environ.get("C1_ARTIFACT_TOOL_NODE_MODULES")
    candidates = [
        Path(configured) if configured else None,
        Path.home()
        / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node"
        / "node_modules",
    ]
    for candidate in candidates:
        if candidate is not None and (
            candidate / "@oai/artifact-tool/package.json"
        ).is_file():
            return candidate.resolve()
    raise ConsolidationError(
        "dependencia @oai/artifact-tool nao encontrada; defina "
        "C1_ARTIFACT_TOOL_NODE_MODULES com o diretorio node_modules existente"
    )


def create_node_modules_link(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
        return
    except OSError as symlink_error:
        if os.name != "nt":
            raise ConsolidationError(
                f"nao foi possivel criar o link temporario para "
                f"@oai/artifact-tool: {symlink_error}"
            ) from symlink_error

    command = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if command.returncode != 0:
        detail = command.stderr.strip() or command.stdout.strip()
        raise ConsolidationError(
            "nao foi possivel criar a juncao temporaria para "
            f"@oai/artifact-tool: {detail}"
        )


def relationship_owner(relationship_path: str) -> str | None:
    if relationship_path == "_rels/.rels":
        return None
    path = Path(relationship_path)
    if path.parent.name != "_rels" or not path.name.endswith(".rels"):
        return None
    return (path.parent.parent / path.name.removesuffix(".rels")).as_posix()


def normalize_workbook_package(path: Path) -> None:
    normalized_path = path.with_suffix(".normalized.xlsx")
    with zipfile.ZipFile(path, "r") as source:
        contents = {name: source.read(name) for name in source.namelist()}

    for name in sorted(contents):
        if not name.endswith(".rels"):
            continue
        relationship_ids = re.findall(rb'\bId="([^"]+)"', contents[name])
        owner = relationship_owner(name)
        for index, relationship_id in enumerate(relationship_ids, start=1):
            stable_id = f"rId{index}".encode("ascii")
            contents[name] = contents[name].replace(
                relationship_id, stable_id
            )
            if owner is not None and owner in contents:
                contents[owner] = contents[owner].replace(
                    relationship_id, stable_id
                )

    with zipfile.ZipFile(
        normalized_path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as output:
        for name in sorted(contents):
            information = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            information.compress_type = zipfile.ZIP_DEFLATED
            information.create_system = 3
            information.external_attr = 0o600 << 16
            output.writestr(information, contents[name])
    os.replace(normalized_path, path)


def run_artifact_tool_script(
    node: str,
    script: str,
    temporary_path: Path,
    environment: dict[str, str],
    action: str,
) -> None:
    command = subprocess.run(
        [node, "--input-type=module", "-e", script],
        cwd=temporary_path,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=120,
    )
    if command.returncode != 0:
        detail = command.stderr.strip() or command.stdout.strip()
        raise ConsolidationError(f"falha ao {action}: {detail}")


def write_workbook() -> None:
    node = shutil.which("node")
    if node is None:
        raise ConsolidationError(
            "Node.js nao encontrado para gerar c1-planilha-experimentos.xlsx"
        )

    definitions = [
        workbook_sheet_definition(
            OUTPUT_DIRECTORY / filename,
            sheet_name,
            table_name,
            header_color,
        )
        for filename, sheet_name, table_name, header_color in WORKBOOK_SHEETS
    ]
    node_modules = artifact_tool_node_modules()

    try:
        with tempfile.TemporaryDirectory(prefix="c1-workbook-") as temporary:
            temporary_path = Path(temporary)
            create_node_modules_link(
                temporary_path / "node_modules", node_modules
            )
            specification_path = temporary_path / "workbook-specification.json"
            temporary_output = temporary_path / WORKBOOK_PATH.name
            specification_path.write_text(
                json.dumps(
                    definitions,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment["C1_WORKBOOK_SPECIFICATION"] = str(specification_path)
            environment["C1_WORKBOOK_OUTPUT"] = str(temporary_output)
            run_artifact_tool_script(
                node,
                WORKBOOK_BUILDER_JS,
                temporary_path,
                environment,
                f"gerar {relative_path(WORKBOOK_PATH)}",
            )
            if not temporary_output.is_file():
                raise ConsolidationError(
                    f"gerador nao criou {relative_path(WORKBOOK_PATH)}"
                )
            normalize_workbook_package(temporary_output)
            run_artifact_tool_script(
                node,
                WORKBOOK_VALIDATOR_JS,
                temporary_path,
                environment,
                f"validar {relative_path(WORKBOOK_PATH)}",
            )
            os.replace(temporary_output, WORKBOOK_PATH)
    except subprocess.TimeoutExpired as exc:
        raise ConsolidationError(
            f"tempo excedido ao gerar {relative_path(WORKBOOK_PATH)}"
        ) from exc


def consolidate() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    runs = discover_runs()
    validate_required_files(runs)
    loaded_runs = [(run, load_run_artifacts(run)) for run in runs]
    for run, artifacts in loaded_runs:
        validate_run(run, artifacts)
    validate_images(loaded_runs)

    summary_rows = [
        build_summary_row(run, artifacts) for run, artifacts in loaded_runs
    ]
    stage_rows = [
        row
        for run, artifacts in loaded_runs
        for row in build_stage_rows(run, artifacts)
    ]
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
                    f"({stage_total}) diverge do resumo geral "
                    f"({summary_row[metric]})"
                )
    aggregate_rows = build_aggregate_rows(summary_rows)

    OUTPUT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    write_csv(OUTPUT_DIRECTORY / "c1-summary.csv", SUMMARY_FIELDS, summary_rows)
    write_csv(
        OUTPUT_DIRECTORY / "c1-stage-summary.csv", STAGE_FIELDS, stage_rows
    )
    write_csv(
        OUTPUT_DIRECTORY / "c1-aggregate-summary.csv",
        ("metric", "mean", "median", "sample_stddev", "minimum", "maximum"),
        aggregate_rows,
    )
    write_workbook()
    return summary_rows, stage_rows


def main() -> int:
    try:
        summary_rows, stage_rows = consolidate()
    except ConsolidationError as exc:
        print(f"Erro de consolidacao do C1: {exc}", file=sys.stderr)
        return 1

    print(
        f"C1 consolidado: {len(summary_rows)} execucoes e "
        f"{len(stage_rows)} estagios."
    )
    for filename in (
        "c1-summary.csv",
        "c1-stage-summary.csv",
        "c1-aggregate-summary.csv",
        WORKBOOK_PATH.name,
    ):
        print(relative_path(OUTPUT_DIRECTORY / filename))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
