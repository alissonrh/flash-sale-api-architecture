#!/usr/bin/env python3
"""Consolidate the four official C3 attempts into reproducible artifacts.

Only c3-run-1 and c3-run-2 contribute to descriptive performance aggregates.
The two invalid attempts remain visible as evidence about architectural
stability and repeatability. Raw experiment artifacts are read-only inputs.
"""

from __future__ import annotations

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
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
OFFICIAL_RUNS_DIRECTORY = REPOSITORY_ROOT / "results/experiments/c3/official"
OUTPUT_DIRECTORY = REPOSITORY_ROOT / "results/consolidated"
WORKBOOK_PATH = OUTPUT_DIRECTORY / "c3-planilha-experimentos.xlsx"

EXPECTED_RUN_NAMES = (
    "c3-run-1",
    "c3-run-2",
    "c3-run-3",
    "c3-run-3-retry-1",
)
VALID_AGGREGATE_RUNS = ("c3-run-1", "c3-run-2")
EXPECTED_RUN_STATUS = {
    "c3-run-1": ("VALID", "STABLE"),
    "c3-run-2": ("VALID", "STABLE"),
    "c3-run-3": ("INVALID", "UNSTABLE"),
    "c3-run-3-retry-1": ("INVALID", "UNSTABLE"),
}
EXCLUSION_REASONS = {
    "c3-run-3": (
        "Reinicializacao da API durante a carga e 19 falhas inesperadas "
        "(19 respostas 5xx)."
    ),
    "c3-run-3-retry-1": (
        "Repeticao da reinicializacao da API e 59 falhas inesperadas "
        "(56 respostas 5xx e 3 erros de conexao)."
    ),
}
EXPECTED_STAGE_NAMES = ("stage_1", "stage_2", "stage_3", "stage_4")
EXPECTED_STAGE_TARGETS = (20, 22, 22, 0)
EXPECTED_STAGE_DURATIONS = ("20s", "30s", "30s", "10s")
EXPECTED_LOAD_PROFILE = {
    "executor": "ramping-arrival-rate",
    "base_url": "http://gateway:8000",
    "scenario": "c3",
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

# Common files preserved by every official C3 attempt.
REQUIRED_FILES = (
    "run-metadata.json",
    "validity-checklist.md",
    "k6/k6-summary.json",
    "k6/k6-stage-summary.json",
    "k6/k6-timeseries.jsonl",
    "k6/k6.log",
    "metrics/collection-summary.json",
    "metrics/collector.log",
    "metrics/collector-errors.jsonl",
    "metrics/hpa-samples.csv",
    "metrics/kubernetes-resources.csv",
    "metrics/kubernetes-pods.csv",
    "metrics/prometheus.csv",
    "logs/api.log",
    "logs/gateway.log",
    "logs/worker.log",
    "traces/jaeger-traces.json",
    "kubernetes/before.json",
    "kubernetes/after.json",
    "kubernetes/events.txt",
    "kubernetes/k6-job.yaml",
    "kubernetes/manifests/gateway-configmap.yaml",
    "kubernetes/manifests/gateway-deployment.yaml",
    "kubernetes/manifests/gateway-service.yaml",
    "rabbitmq/queue.csv",
    "rabbitmq/drain-summary.json",
    "database/db-summary.json",
    "gateway/effective-config.yaml",
    "gateway/effective-kubernetes-resources.yaml",
    "gateway/protection-summary.json",
    "gateway/rate-limit-config.json",
    "gateway/rate-limit-headers-preflight.json",
)

SUMMARY_FIELDS = (
    "run_id",
    "validity",
    "stability",
    "included_in_valid_aggregate",
    "exclusion_reason",
    "requests_started",
    "classified_http_requests",
    "responses_2xx",
    "responses_429",
    "responses_429_percent",
    "responses_5xx",
    "connection_errors",
    "unexpected_statuses",
    "unexpected_failures",
    "unexpected_failures_percent",
    "dropped_iterations",
    "dropped_iterations_percent",
    "throughput_rps",
    "accepted_throughput_rps",
    "rejected_throughput_rps",
    "latency_p95_ms",
    "latency_p99_ms",
    "response_2xx_p95_ms",
    "response_2xx_p99_ms",
    "response_429_p95_ms",
    "response_429_p99_ms",
    "orders_created",
    "orders_completed",
    "orders_pending",
    "orders_processing",
    "orders_failed",
    "completion_rate_percent",
    "order_processing_p95_ms",
    "order_processing_p99_ms",
    "drain_duration_seconds",
    "drain_completed",
    "drain_objective_met",
    "api_restarts",
    "gateway_restarts",
    "worker_restarts",
    "total_restarts",
    "api_peak_cpu_cores",
    "api_peak_memory_mib",
    "gateway_peak_cpu_cores",
    "gateway_peak_memory_mib",
    "max_backlog",
)

STAGE_FIELDS = (
    "run_id",
    "validity",
    "included_in_valid_aggregate",
    "stage",
    "target_rps",
    "duration_seconds",
    "requests_started",
    "classified_http_requests",
    "responses_2xx",
    "responses_429",
    "responses_5xx",
    "connection_errors",
    "unexpected_statuses",
    "unexpected_failures",
    "dropped_iterations",
    "throughput_rps",
    "accepted_throughput_rps",
    "rejected_throughput_rps",
    "latency_p95_ms",
    "latency_p99_ms",
    "response_2xx_p95_ms",
    "response_2xx_p99_ms",
    "response_429_p95_ms",
    "response_429_p99_ms",
    "api_replicas_observed_min",
    "api_replicas_observed_max",
    "gateway_replicas_observed_min",
    "gateway_replicas_observed_max",
    "worker_replicas_observed_min",
    "worker_replicas_observed_max",
    "api_restart_delta",
    "gateway_restart_delta",
    "worker_restart_delta",
)

AGGREGATE_METRICS = tuple(
    field
    for field in SUMMARY_FIELDS
    if field
    not in {
        "run_id",
        "validity",
        "stability",
        "included_in_valid_aggregate",
        "exclusion_reason",
        "drain_completed",
        "drain_objective_met",
    }
)
AGGREGATE_FIELDS = (
    "metric",
    "sample_size",
    "mean",
    "median",
    "sample_standard_deviation",
    "min",
    "max",
    "interpretation",
)

RATE_LIMIT_FIELDS = (
    "run_id",
    "total_received",
    "classified_http_requests",
    "total_accepted",
    "total_rejected_429",
    "accepted_percent",
    "rejected_percent",
    "accepted_throughput_rps",
    "rejected_throughput_rps",
    "unexpected_failures",
    "api_restarts",
    "gateway_restarts",
    "validity",
    "rate_limiting_worked",
    "architectural_stability",
)

OUTPUT_FILENAMES = (
    "c3-summary.csv",
    "c3-stage-summary.csv",
    "c3-aggregate-summary.csv",
    "c3-rate-limit-summary.csv",
    "c3-planilha-experimentos.xlsx",
)

WORKBOOK_SHEETS = (
    "Resumo das execuções",
    "Resultados por estágio",
    "Agregados válidos",
    "Rate limiting",
    "Tentativas inválidas",
    "Metodologia",
)

WORKBOOK_BUILDER_JS = r"""
import fs from "node:fs/promises";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const specification = JSON.parse(
  await fs.readFile(process.env.C3_WORKBOOK_SPECIFICATION, "utf8"),
);
const outputPath = process.env.C3_WORKBOOK_OUTPUT;
const workbook = Workbook.create();
const COLORS = {
  navy: "#17365D",
  blue: "#2F75B5",
  lightBlue: "#D9EAF7",
  paleBlue: "#EAF3F8",
  white: "#FFFFFF",
  text: "#404040",
  border: "#B4C7E7",
  amber: "#FFF2CC",
  amberStrong: "#F4B183",
  redText: "#9C0006",
  green: "#70AD47",
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

function tableAddress(startRow, startColumn, rowCount, columnCount) {
  return `${columnLetter(startColumn)}${startRow}:` +
    `${columnLetter(startColumn + columnCount - 1)}${startRow + rowCount - 1}`;
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
  const address = tableAddress(
    definition.startRow,
    definition.startColumn,
    rows.length,
    definition.headers.length,
  );
  const range = sheet.getRange(address);
  range.values = rows;
  range.format.font = { name: FONT, size: 10, color: COLORS.text };
  range.format.verticalAlignment = "center";
  const table = sheet.tables.add(address, true, definition.tableName);
  table.style = "TableStyleMedium2";
  table.showHeaders = true;
  table.showTotals = false;
  table.showBandedColumns = false;
  table.showFilterButton = true;

  const firstColumn = columnLetter(definition.startColumn);
  const lastColumn = columnLetter(
    definition.startColumn + definition.headers.length - 1,
  );
  const header = sheet.getRange(
    `${firstColumn}${definition.startRow}:${lastColumn}${definition.startRow}`,
  );
  header.format = {
    fill: COLORS.navy,
    font: { name: FONT, size: 10, bold: true, color: COLORS.white },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    wrapText: true,
    borders: { preset: "all", style: "thin", color: COLORS.white },
  };
  header.format.rowHeight = 48;
  if (definition.rows.length > 0) {
    const body = sheet.getRange(
      `${firstColumn}${definition.startRow + 1}:` +
      `${lastColumn}${definition.startRow + definition.rows.length}`,
    );
    body.format.rowHeight = 20;
    body.format.wrapText = false;
    body.format.numberFormat = definition.numberFormats;
  }
  for (let index = 0; index < definition.widths.length; index += 1) {
    const column = columnLetter(definition.startColumn + index);
    sheet.getRange(
      `${column}${definition.startRow}:` +
      `${column}${definition.startRow + definition.rows.length}`,
    ).format.columnWidth = definition.widths[index];
  }
  definition.address = address;
  return address;
}

for (const name of specification.sheetNames) {
  applyBase(workbook.worksheets.add(name));
}

const summarySheet = workbook.worksheets.getItem("Resumo das execuções");
applyTitle(
  summarySheet,
  "Resumo das tentativas oficiais do C3",
  "Duas execuções válidas compõem a síntese descritiva (n=2); duas tentativas inválidas permanecem visíveis.",
);
applySection(summarySheet, "A5:H5", "Resultados das quatro tentativas oficiais");
addTable(summarySheet, specification.summary);
const summaryLastColumn = columnLetter(specification.summary.headers.length);
for (const rowNumber of specification.invalidSummaryRows) {
  summarySheet.getRange(`A${rowNumber}:${summaryLastColumn}${rowNumber}`)
    .format.fill = COLORS.amber;
}
summarySheet.freezePanes.freezeRows(6);

const stageSheet = workbook.worksheets.getItem("Resultados por estágio");
applyTitle(
  stageSheet,
  "Resultados do C3 por estágio de carga",
  "As dezesseis linhas preservam os quatro estágios de cada tentativa, inclusive as inválidas.",
);
applySection(stageSheet, "A5:H5", "Métricas por execução e estágio");
addTable(stageSheet, specification.stages);
stageSheet.freezePanes.freezeRows(6);

const aggregateSheet = workbook.worksheets.getItem("Agregados válidos");
applyTitle(
  aggregateSheet,
  "Agregados descritivos das execuções válidas",
  "Média, mediana, desvio-padrão amostral, mínimo e máximo usam somente c3-run-1 e c3-run-2 (n=2).",
);
applySection(aggregateSheet, "A5:H5", "Síntese descritiva com amostra reduzida");
addTable(aggregateSheet, specification.aggregates);
aggregateSheet.freezePanes.freezeRows(6);

const rateSheet = workbook.worksheets.getItem("Rate limiting");
applyTitle(
  rateSheet,
  "Eficácia da proteção de entrada do Kong",
  "Respostas HTTP 429 demonstram a atuação do limite; a estabilidade da API é avaliada separadamente.",
);
applySection(rateSheet, "A5:H5", "Proteção observada por tentativa");
addTable(rateSheet, specification.rateLimit);
for (const rowNumber of specification.invalidRateRows) {
  rateSheet.getRange(`A${rowNumber}:O${rowNumber}`).format.fill = COLORS.amber;
}
const chart = rateSheet.charts.add("bar", [
  rateSheet.getRange("A6:A10"),
  rateSheet.getRange("D6:D10"),
  rateSheet.getRange("E6:E10"),
]);
chart.title = "Requisições aceitas e rejeitadas com 429";
chart.titleTextStyle.fontSize = 12;
chart.titleTextStyle.typeface = FONT;
chart.legend = { position: "top", textStyle: { typeface: FONT } };
chart.xAxis = { axisType: "textAxis", textStyle: { typeface: FONT, fontSize: 9 } };
chart.yAxis = {
  numberFormatCode: "#,##0",
  numberFormatSourceLinked: false,
  textStyle: { typeface: FONT, fontSize: 9 },
};
chart.setPosition("A13", "J30");
if (chart.series.items.length >= 2) {
  chart.series.items[0].fill = COLORS.blue;
  chart.series.items[1].fill = COLORS.green;
}
rateSheet.freezePanes.freezeRows(6);

const invalidSheet = workbook.worksheets.getItem("Tentativas inválidas");
applyTitle(
  invalidSheet,
  "Tentativas inválidas preservadas",
  "As tentativas não entram nas médias de desempenho e documentam repetibilidade e robustez insuficientes.",
);
applySection(invalidSheet, "A5:H5", "Falhas e reinicializações observadas");
addTable(invalidSheet, specification.invalidAttempts);
invalidSheet.getRange("A7:M8").format.fill = COLORS.amber;
invalidSheet.getRange("B7:C8").format = {
  fill: COLORS.amberStrong,
  font: { name: FONT, size: 10, bold: true, color: COLORS.redText },
};
invalidSheet.getRange("F7:F8").format.wrapText = true;
invalidSheet.getRange("A7:M8").format.rowHeight = 44;
invalidSheet.freezePanes.freezeRows(6);

const methodologySheet = workbook.worksheets.getItem("Metodologia");
applyTitle(
  methodologySheet,
  "Metodologia da consolidação do C3",
  "Registro das inclusões, exclusões e limites de interpretação dos resultados.",
);
methodologySheet.getRange("A5:B5").values = [["Tópico", "Registro metodológico"]];
methodologySheet.getRange("A6:B15").values = specification.methodologyRows;
methodologySheet.getRange("A5:B15").format.font = {
  name: FONT,
  size: 10,
  color: COLORS.text,
};
methodologySheet.getRange("A5:B5").format = {
  fill: COLORS.navy,
  font: { name: FONT, size: 10, bold: true, color: COLORS.white },
  horizontalAlignment: "center",
  verticalAlignment: "center",
  wrapText: true,
  borders: { preset: "all", style: "thin", color: COLORS.white },
};
methodologySheet.getRange("A5:B5").format.rowHeight = 38;
methodologySheet.getRange("A6:A15").format = {
  fill: COLORS.lightBlue,
  font: { name: FONT, size: 10, bold: true, color: COLORS.navy },
  verticalAlignment: "center",
  wrapText: true,
  borders: { preset: "inside", style: "thin", color: COLORS.border },
};
methodologySheet.getRange("B6:B15").format = {
  font: { name: FONT, size: 10, color: COLORS.text },
  verticalAlignment: "center",
  wrapText: true,
  borders: { preset: "inside", style: "thin", color: COLORS.border },
};
methodologySheet.getRange("A6:B15").format.rowHeight = 48;
methodologySheet.getRange("A5:A15").format.columnWidth = 30;
methodologySheet.getRange("B5:B15").format.columnWidth = 110;

workbook.recalculate();
const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
  options: { useRegex: true, maxResults: 100 },
  summary: "formula error scan before export",
});
const tokens = [
  "#REF!", "#DIV/0!", "#VALUE!", "#NAME?", "#N/A",
  "#NUM!", "#NULL!", "#SPILL!", "#CALC!",
];
if (tokens.some((token) => errors.ndjson.includes(token))) {
  throw new Error(`erros de formula: ${errors.ndjson}`);
}
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
"""

WORKBOOK_VALIDATOR_JS = r"""
import fs from "node:fs/promises";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const specification = JSON.parse(
  await fs.readFile(process.env.C3_WORKBOOK_SPECIFICATION, "utf8"),
);
const workbook = await SpreadsheetFile.importXlsx(
  await FileBlob.load(process.env.C3_WORKBOOK_OUTPUT),
);
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
      for (
        let column = 0;
        column < Math.max(actualRow.length, expectedRow.length);
        column += 1
      ) {
        if (JSON.stringify(actualRow[column]) !== JSON.stringify(expectedRow[column])) {
          throw new Error(
            `valor divergente em ${definition.tableName} ` +
            `linha ${row + 1}, coluna ${column + 1}: ` +
            `esperado=${JSON.stringify(expectedRow[column])}, ` +
            `obtido=${JSON.stringify(actualRow[column])}`,
          );
        }
      }
    }
    throw new Error(`dimensoes divergentes na tabela ${definition.tableName}`);
  }
}
const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
  options: { useRegex: true, maxResults: 100 },
  summary: "final formula error scan",
});
const tokens = [
  "#REF!", "#DIV/0!", "#VALUE!", "#NAME?", "#N/A",
  "#NUM!", "#NULL!", "#SPILL!", "#CALC!",
];
if (tokens.some((token) => errors.ndjson.includes(token))) {
  throw new Error(`erros de formula apos exportacao: ${errors.ndjson}`);
}
for (const sheet of workbook.worksheets.items) {
  const preview = await workbook.render({
    sheetName: sheet.name,
    autoCrop: "all",
    scale: 1,
    format: "png",
  });
  const bytes = new Uint8Array(await preview.arrayBuffer());
  if (bytes.length === 0) {
    throw new Error(`renderizacao vazia: ${sheet.name}`);
  }
}
"""


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
            f"JSON deve conter objeto em {relative_path(path)}"
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
            f"campo '{path}' deve ser objeto em {relative_path(source)}"
        )
    return value


def sequence(data: Any, path: str, source: Path) -> list[Any]:
    value = field(data, path, source)
    if not isinstance(value, list):
        raise ConsolidationError(
            f"campo '{path}' deve ser lista em {relative_path(source)}"
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


def percentage(numerator: int | float, denominator: int | float) -> float:
    return round(numerator / denominator * 100, 3) if denominator else 0.0


def rounded(value: int | float, digits: int = 6) -> float:
    return round(float(value), digits)


def discover_runs() -> list[Path]:
    if not OFFICIAL_RUNS_DIRECTORY.is_dir():
        raise ConsolidationError(
            "diretorio de execucoes oficiais ausente: "
            f"{relative_path(OFFICIAL_RUNS_DIRECTORY)}"
        )
    runs = sorted(
        (entry for entry in OFFICIAL_RUNS_DIRECTORY.iterdir() if entry.is_dir()),
        key=lambda entry: EXPECTED_RUN_NAMES.index(entry.name)
        if entry.name in EXPECTED_RUN_NAMES
        else len(EXPECTED_RUN_NAMES),
    )
    discovered = tuple(run.name for run in runs)
    if discovered != EXPECTED_RUN_NAMES:
        raise ConsolidationError(
            "devem existir exatamente as quatro tentativas oficiais "
            f"{', '.join(EXPECTED_RUN_NAMES)}; encontradas: "
            f"{', '.join(discovered) if discovered else 'nenhuma'}"
        )
    return runs


def validate_required_files(runs: list[Path]) -> None:
    missing = [
        f"{run.name}/{required}"
        for run in runs
        for required in REQUIRED_FILES
        if not (run / required).is_file()
    ]
    if missing:
        raise ConsolidationError(
            "artefatos obrigatorios ausentes: " + ", ".join(missing)
        )


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
    }
    return {
        "paths": paths,
        **{name: load_json(path) for name, path in paths.items()},
    }


def metric_counts(artifacts: dict[str, Any]) -> dict[str, int]:
    metrics = mapping(
        artifacts["k6"], "metrics", artifacts["paths"]["k6"]
    )
    source = artifacts["paths"]["k6"]
    return {
        "requests_started": optional_metric_count(
            metrics, "requests_started", source
        ),
        "classified_http_requests": optional_metric_count(
            metrics, "http_reqs", source
        ),
        "responses_2xx": optional_metric_count(metrics, "responses_2xx", source),
        "responses_429": optional_metric_count(metrics, "responses_429", source),
        "responses_5xx": optional_metric_count(metrics, "responses_5xx", source),
        "connection_errors": optional_metric_count(
            metrics, "connection_errors", source
        ),
        "unexpected_statuses": optional_metric_count(
            metrics, "unexpected_statuses", source
        ),
        "unexpected_failures": optional_metric_count(
            metrics, "unexpected_failures", source
        ),
        "dropped_iterations": optional_metric_count(
            metrics, "dropped_iterations", source
        ),
    }


def restart_counts(artifacts: dict[str, Any]) -> dict[str, int]:
    collection = artifacts["collection"]
    source = artifacts["paths"]["collection"]
    counts = {"api": 0, "gateway": 0, "worker": 0}
    for item in sequence(collection, "restart_deltas", source):
        app = text_value(item, "app", source)
        delta = number_value(item, "restart_delta", source, integer=True)
        if app in counts:
            counts[app] += delta
    total = number_value(
        collection, "restart_delta_total", source, integer=True
    )
    if sum(counts.values()) != total:
        raise ConsolidationError(
            f"{source.parent.parent.name}: deltas de reinicio por aplicacao "
            f"somam {sum(counts.values())}, mas restart_delta_total={total}"
        )
    gateway_summary = number_value(
        collection, "gateway_restart_delta", source, integer=True
    )
    if counts["gateway"] != gateway_summary:
        raise ConsolidationError(
            f"{source.parent.parent.name}: reinicios do gateway inconsistentes"
        )
    return counts


def validate_stage_profile(run: Path, artifacts: dict[str, Any]) -> None:
    metadata = artifacts["metadata"]
    stage_summary = artifacts["stages"]
    metadata_path = artifacts["paths"]["metadata"]
    stage_path = artifacts["paths"]["stages"]
    metadata_stages = sequence(metadata, "load.stages", metadata_path)
    stages = sequence(stage_summary, "stages", stage_path)
    if len(metadata_stages) != 4 or len(stages) != 4:
        raise ConsolidationError(
            f"{run.name}: perfil e resumo devem possuir quatro estagios"
        )
    for index, (metadata_stage, stage) in enumerate(
        zip(metadata_stages, stages)
    ):
        name = text_value(stage, "name", stage_path)
        target = number_value(stage, "target", stage_path, integer=True)
        duration = text_value(stage, "duration", stage_path)
        if name != EXPECTED_STAGE_NAMES[index]:
            raise ConsolidationError(
                f"{run.name}: nome inesperado no estagio {index + 1}: {name}"
            )
        if target != EXPECTED_STAGE_TARGETS[index]:
            raise ConsolidationError(
                f"{run.name}/{name}: target deve ser "
                f"{EXPECTED_STAGE_TARGETS[index]}"
            )
        if duration != EXPECTED_STAGE_DURATIONS[index]:
            raise ConsolidationError(
                f"{run.name}/{name}: duracao deve ser "
                f"{EXPECTED_STAGE_DURATIONS[index]}"
            )
        if number_value(
            metadata_stage, "target", metadata_path, integer=True
        ) != target or text_value(
            metadata_stage, "duration", metadata_path
        ) != duration:
            raise ConsolidationError(
                f"{run.name}/{name}: resumo diverge dos metadados da carga"
            )


def validate_gateway_evidence(run: Path, artifacts: dict[str, Any]) -> None:
    config = artifacts["rate_config"]
    config_path = artifacts["paths"]["rate_config"]
    expected_config = {
        "treatment": "edge protection by gateway rate limiting",
        "gateway": "Kong DB-less",
        "image": "kong:3.9.1-ubuntu",
        "upstream": "http://api:8000",
        "route": "/checkout",
        "limit": 20,
        "window": "1s",
        "limit_by": "service",
        "policy": "local",
        "redis": False,
        "replicas": 1,
        "client_headers_exposed": True,
    }
    if config != expected_config:
        raise ConsolidationError(
            f"{run.name}: configuracao efetiva de rate limiting divergente em "
            f"{relative_path(config_path)}"
        )
    headers = artifacts["headers"]
    headers_path = artifacts["paths"]["headers"]
    if number_value(headers, "status", headers_path, integer=True) != 200:
        raise ConsolidationError(f"{run.name}: preflight /health nao retornou 200")
    observed = mapping(headers, "rate_limit_headers", headers_path)
    standard = {"ratelimit-limit", "ratelimit-remaining", "ratelimit-reset"}
    legacy = {"x-ratelimit-limit-second", "x-ratelimit-remaining-second"}
    if not (standard.issubset(observed) or legacy.issubset(observed)):
        raise ConsolidationError(
            f"{run.name}: cabecalhos de rate limiting ausentes"
        )
    for name in ("ratelimit-limit", "x-ratelimit-limit-second"):
        if name in observed and str(observed[name]).strip() != "20":
            raise ConsolidationError(
                f"{run.name}: {name} anuncia limite {observed[name]!r}, nao 20"
            )


def validate_classification(run: Path, artifacts: dict[str, Any]) -> None:
    counts = metric_counts(artifacts)
    metrics = mapping(
        artifacts["k6"], "metrics", artifacts["paths"]["k6"]
    )
    k6_path = artifacts["paths"]["k6"]
    classified = (
        counts["responses_2xx"]
        + counts["responses_429"]
        + counts["responses_5xx"]
        + counts["connection_errors"]
        + counts["unexpected_statuses"]
    )
    if classified != counts["classified_http_requests"]:
        raise ConsolidationError(
            f"{run.name}: classes HTTP somam {classified}, mas http_reqs="
            f"{counts['classified_http_requests']}"
        )
    calculated_unexpected = (
        counts["responses_5xx"]
        + counts["connection_errors"]
        + counts["unexpected_statuses"]
    )
    if counts["unexpected_failures"] != calculated_unexpected:
        raise ConsolidationError(
            f"{run.name}: unexpected_failures nao reconcilia suas classes"
        )
    http_failed = number_value(
        metrics, "http_req_failed.passes", k6_path, integer=True
    )
    if http_failed != calculated_unexpected:
        raise ConsolidationError(
            f"{run.name}: http_req_failed.passes diverge das falhas inesperadas"
        )
    expected_rate = (
        calculated_unexpected / counts["classified_http_requests"]
        if counts["classified_http_requests"]
        else 0.0
    )
    observed_rate = number_value(metrics, "http_req_failed.value", k6_path)
    if not math.isclose(observed_rate, expected_rate, rel_tol=1e-12, abs_tol=1e-12):
        raise ConsolidationError(
            f"{run.name}: taxa http_req_failed inconsistente"
        )

    protection = artifacts["protection"]
    protection_path = artifacts["paths"]["protection"]
    protection_counts = mapping(protection, "classification", protection_path)
    expected_protection = {
        "http_reqs": counts["classified_http_requests"],
        "requests_started": counts["requests_started"],
        "responses_2xx": counts["responses_2xx"],
        "responses_429": counts["responses_429"],
        "responses_5xx": counts["responses_5xx"],
        "connection_errors": counts["connection_errors"],
        "unexpected_statuses": counts["unexpected_statuses"],
        "unexpected_failures": counts["unexpected_failures"],
    }
    if protection_counts != expected_protection:
        raise ConsolidationError(
            f"{run.name}: protection-summary diverge do resumo do k6"
        )
    invariant = counts["requests_started"] == classified
    if boolean_value(
        protection, "classification_invariant_ok", protection_path
    ) != invariant:
        raise ConsolidationError(
            f"{run.name}: classification_invariant_ok inconsistente"
        )
    if not boolean_value(protection, "protection_working", protection_path):
        raise ConsolidationError(
            f"{run.name}: rate limiting nao foi registrado como atuante"
        )
    if counts["responses_429"] <= 0:
        raise ConsolidationError(f"{run.name}: nenhuma resposta 429 observada")


def validate_database(run: Path, artifacts: dict[str, Any]) -> None:
    database = artifacts["database"]
    database_path = artifacts["paths"]["database"]
    protection = artifacts["protection"]
    protection_path = artifacts["paths"]["protection"]
    counts = metric_counts(artifacts)
    total = number_value(database, "total_orders", database_path, integer=True)
    by_status = mapping(database, "orders_by_status", database_path)
    statuses = {
        status: number_value(by_status, status, database_path, integer=True)
        for status in ("COMPLETED", "PENDING", "PROCESSING", "FAILED")
    }
    if sum(statuses.values()) != total:
        raise ConsolidationError(
            f"{run.name}: estados de pedidos nao reconciliam total_orders"
        )
    expected_completion = percentage(statuses["COMPLETED"], total)
    completion = number_value(
        database, "completion_rate_percent", database_path
    )
    if not math.isclose(completion, expected_completion, abs_tol=0.001):
        raise ConsolidationError(
            f"{run.name}: completion_rate_percent inconsistente"
        )
    side_effect = mapping(protection, "side_effect_check", protection_path)
    authoritative = (
        total == counts["responses_2xx"]
        and sum(statuses.values()) == total
        and statuses["COMPLETED"] == counts["responses_2xx"]
        and statuses["PENDING"] == 0
        and statuses["PROCESSING"] == 0
        and statuses["FAILED"] == 0
    )
    if field(side_effect, "database_authoritative_check_ok", protection_path) != authoritative:
        raise ConsolidationError(
            f"{run.name}: verificacao autoritativa do banco inconsistente"
        )
    expected_valid = run.name in VALID_AGGREGATE_RUNS
    if authoritative != expected_valid:
        raise ConsolidationError(
            f"{run.name}: reconciliacao do banco diverge do status metodologico"
        )
    number_value(
        side_effect, "rabbitmq_publish_delta", protection_path, integer=True
    )
    boolean_value(
        side_effect, "rabbitmq_publish_counter_matches", protection_path
    )


def validate_run(run: Path, artifacts: dict[str, Any]) -> None:
    metadata = artifacts["metadata"]
    metadata_path = artifacts["paths"]["metadata"]
    expected_validity, expected_stability = EXPECTED_RUN_STATUS[run.name]
    observed_validity = text_value(
        metadata, "validity.status", metadata_path
    ).upper()
    observed_stability = text_value(
        metadata, "stability.status", metadata_path
    ).upper()
    if text_value(metadata, "type", metadata_path).lower() != "official":
        raise ConsolidationError(f"{run.name}: tipo deve ser official")
    if text_value(metadata, "id", metadata_path) != run.name:
        raise ConsolidationError(f"{run.name}: id diverge do diretorio")
    if text_value(metadata, "scenario", metadata_path).lower() != "c3":
        raise ConsolidationError(f"{run.name}: scenario deve ser c3")
    if mapping(metadata, "load", metadata_path) != EXPECTED_LOAD_PROFILE:
        raise ConsolidationError(
            f"{run.name}: perfil de carga diverge do protocolo oficial do C3"
        )
    if (observed_validity, observed_stability) != (
        expected_validity,
        expected_stability,
    ):
        raise ConsolidationError(
            f"{run.name}: esperado {expected_validity}/{expected_stability}, "
            f"observado {observed_validity}/{observed_stability}"
        )
    execution_status = text_value(
        metadata, "execution_status", metadata_path
    ).upper()
    if execution_status != expected_validity:
        raise ConsolidationError(
            f"{run.name}: execution_status diverge de validity.status"
        )
    invalid_reasons = sequence(metadata, "invalid_reasons", metadata_path)
    if (expected_validity == "VALID" and invalid_reasons) or (
        expected_validity == "INVALID" and not invalid_reasons
    ):
        raise ConsolidationError(
            f"{run.name}: motivos de invalidade inconsistentes com o status"
        )
    if text_value(metadata, "images.gateway", metadata_path) != "kong:3.9.1-ubuntu":
        raise ConsolidationError(f"{run.name}: imagem do gateway inesperada")
    rate = mapping(metadata, "rate_limiting", metadata_path)
    expected_rate_values = {
        "enabled": True,
        "gateway": "Kong DB-less",
        "limit": 20,
        "window": "1s",
        "limit_by": "service",
        "policy": "local",
        "client_headers_exposed": True,
        "observed_replicas_min": 1,
        "observed_replicas_max": 1,
    }
    for key, expected in expected_rate_values.items():
        if rate.get(key) != expected:
            raise ConsolidationError(
                f"{run.name}: rate_limiting.{key}={rate.get(key)!r}; "
                f"esperado {expected!r}"
            )
    validate_stage_profile(run, artifacts)
    validate_gateway_evidence(run, artifacts)
    validate_classification(run, artifacts)
    validate_database(run, artifacts)
    restarts = restart_counts(artifacts)
    if run.name in VALID_AGGREGATE_RUNS and any(restarts.values()):
        raise ConsolidationError(f"{run.name}: execucao valida possui reinicio")
    if run.name not in VALID_AGGREGATE_RUNS and restarts != {
        "api": 1,
        "gateway": 0,
        "worker": 0,
    }:
        raise ConsolidationError(
            f"{run.name}: tentativa invalida deve preservar o reinicio da API"
        )


def validate_run_consistency(
    loaded_runs: list[tuple[Path, dict[str, Any]]]
) -> None:
    for role in ("api", "worker", "gateway"):
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
                f"as quatro tentativas devem usar a mesma imagem de {role}"
            )


def build_summary_row(run: Path, artifacts: dict[str, Any]) -> dict[str, Any]:
    metadata = artifacts["metadata"]
    metrics = mapping(artifacts["k6"], "metrics", artifacts["paths"]["k6"])
    database = artifacts["database"]
    drain = artifacts["drain"]
    collection = artifacts["collection"]
    paths = artifacts["paths"]
    counts = metric_counts(artifacts)
    restarts = restart_counts(artifacts)
    statuses = mapping(database, "orders_by_status", paths["database"])
    included = run.name in VALID_AGGREGATE_RUNS
    return {
        "run_id": run.name,
        "validity": text_value(metadata, "validity.status", paths["metadata"]).upper(),
        "stability": text_value(metadata, "stability.status", paths["metadata"]).upper(),
        "included_in_valid_aggregate": included,
        "exclusion_reason": "" if included else EXCLUSION_REASONS[run.name],
        **counts,
        "responses_429_percent": percentage(
            counts["responses_429"], counts["requests_started"]
        ),
        "unexpected_failures_percent": percentage(
            counts["unexpected_failures"], counts["classified_http_requests"]
        ),
        "dropped_iterations_percent": percentage(
            counts["dropped_iterations"],
            counts["requests_started"] + counts["dropped_iterations"],
        ),
        "throughput_rps": rounded(
            number_value(metrics, "throughput_total.rate", paths["k6"])
        ),
        "accepted_throughput_rps": rounded(
            number_value(metrics, "throughput_accepted_2xx.rate", paths["k6"])
        ),
        "rejected_throughput_rps": rounded(
            number_value(metrics, "throughput_rejected_429.rate", paths["k6"])
        ),
        "latency_p95_ms": rounded(
            number_value(metrics, "http_req_duration.p(95)", paths["k6"]), 3
        ),
        "latency_p99_ms": rounded(
            number_value(metrics, "http_req_duration.p(99)", paths["k6"]), 3
        ),
        "response_2xx_p95_ms": rounded(
            number_value(metrics, "response_duration_2xx.p(95)", paths["k6"]), 3
        ),
        "response_2xx_p99_ms": rounded(
            number_value(metrics, "response_duration_2xx.p(99)", paths["k6"]), 3
        ),
        "response_429_p95_ms": rounded(
            number_value(metrics, "response_duration_429.p(95)", paths["k6"]), 3
        ),
        "response_429_p99_ms": rounded(
            number_value(metrics, "response_duration_429.p(99)", paths["k6"]), 3
        ),
        "orders_created": number_value(
            database, "total_orders", paths["database"], integer=True
        ),
        "orders_completed": number_value(
            statuses, "COMPLETED", paths["database"], integer=True
        ),
        "orders_pending": number_value(
            statuses, "PENDING", paths["database"], integer=True
        ),
        "orders_processing": number_value(
            statuses, "PROCESSING", paths["database"], integer=True
        ),
        "orders_failed": number_value(
            statuses, "FAILED", paths["database"], integer=True
        ),
        "completion_rate_percent": rounded(
            number_value(database, "completion_rate_percent", paths["database"]), 3
        ),
        "order_processing_p95_ms": rounded(
            number_value(database, "processing_time_ms.p95", paths["database"]), 3
        ),
        "order_processing_p99_ms": rounded(
            number_value(database, "processing_time_ms.p99", paths["database"]), 3
        ),
        "drain_duration_seconds": number_value(
            drain, "drain_duration_seconds", paths["drain"], integer=True
        ),
        "drain_completed": boolean_value(drain, "completed", paths["drain"]),
        "drain_objective_met": boolean_value(
            drain, "objective_met", paths["drain"]
        ),
        "api_restarts": restarts["api"],
        "gateway_restarts": restarts["gateway"],
        "worker_restarts": restarts["worker"],
        "total_restarts": number_value(
            collection, "restart_delta_total", paths["collection"], integer=True
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
        "gateway_peak_cpu_cores": rounded(
            number_value(
                collection,
                "resource_peaks.gateway.max_cpu_cores",
                paths["collection"],
            ),
            9,
        ),
        "gateway_peak_memory_mib": rounded(
            number_value(
                collection,
                "resource_peaks.gateway.max_memory_mib",
                paths["collection"],
            ),
            6,
        ),
        "max_backlog": number_value(
            collection, "queue.max_messages", paths["collection"], integer=True
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
    metadata = artifacts["metadata"]
    collection = artifacts["collection"]
    stage_summary = artifacts["stages"]
    paths = artifacts["paths"]
    stage_path = paths["stages"]
    restarts = restart_counts(artifacts)
    validity = text_value(metadata, "validity.status", paths["metadata"]).upper()
    included = run.name in VALID_AGGREGATE_RUNS
    rows = []
    for stage in sequence(stage_summary, "stages", stage_path):
        metrics = mapping(stage, "metrics", stage_path)
        requests_started = number_value(
            metrics, "requests_started", stage_path, integer=True
        )
        classified = number_value(metrics, "requests", stage_path, integer=True)
        responses = {
            name: number_value(metrics, name, stage_path, integer=True)
            for name in (
                "responses_2xx",
                "responses_429",
                "responses_5xx",
                "connection_errors",
                "unexpected_statuses",
                "unexpected_failures",
                "dropped_iterations",
            )
        }
        calculated_classified = (
            responses["responses_2xx"]
            + responses["responses_429"]
            + responses["responses_5xx"]
            + responses["connection_errors"]
            + responses["unexpected_statuses"]
        )
        if calculated_classified != classified:
            raise ConsolidationError(
                f"{run.name}/{stage.get('name', 'estagio')}: classes HTTP "
                f"somam {calculated_classified}, mas requests={classified}"
            )
        if responses["unexpected_failures"] != (
            responses["responses_5xx"]
            + responses["connection_errors"]
            + responses["unexpected_statuses"]
        ):
            raise ConsolidationError(
                f"{run.name}/{stage.get('name', 'estagio')}: falhas inesperadas "
                "nao reconciliam"
            )
        throughput = mapping(metrics, "throughput_per_second", stage_path)
        latency = mapping(metrics, "latency_ms", stage_path)
        latency_all = mapping(latency, "all", stage_path)
        latency_2xx = mapping(latency, "responses_2xx", stage_path)
        latency_429 = mapping(latency, "responses_429", stage_path)
        rows.append(
            {
                "run_id": run.name,
                "validity": validity,
                "included_in_valid_aggregate": included,
                "stage": text_value(stage, "name", stage_path),
                "target_rps": number_value(
                    stage, "target", stage_path, integer=True
                ),
                "duration_seconds": rounded(
                    number_value(stage, "duration_seconds", stage_path), 3
                ),
                "requests_started": requests_started,
                "classified_http_requests": classified,
                **responses,
                "throughput_rps": rounded(
                    number_value(throughput, "total", stage_path), 6
                ),
                "accepted_throughput_rps": rounded(
                    number_value(throughput, "accepted_2xx", stage_path), 6
                ),
                "rejected_throughput_rps": rounded(
                    number_value(throughput, "rejected_429", stage_path), 6
                ),
                "latency_p95_ms": nullable_number(
                    latency_all, "p95", stage_path
                ),
                "latency_p99_ms": nullable_number(
                    latency_all, "p99", stage_path
                ),
                "response_2xx_p95_ms": nullable_number(
                    latency_2xx, "p95", stage_path
                ),
                "response_2xx_p99_ms": nullable_number(
                    latency_2xx, "p99", stage_path
                ),
                "response_429_p95_ms": nullable_number(
                    latency_429, "p95", stage_path
                ),
                "response_429_p99_ms": nullable_number(
                    latency_429, "p99", stage_path
                ),
                "api_replicas_observed_min": number_value(
                    collection, "api_pod_count_min", paths["collection"], integer=True
                ),
                "api_replicas_observed_max": number_value(
                    collection, "api_pod_count_max", paths["collection"], integer=True
                ),
                "gateway_replicas_observed_min": number_value(
                    collection,
                    "gateway_pod_count_min",
                    paths["collection"],
                    integer=True,
                ),
                "gateway_replicas_observed_max": number_value(
                    collection,
                    "gateway_pod_count_max",
                    paths["collection"],
                    integer=True,
                ),
                "worker_replicas_observed_min": number_value(
                    collection,
                    "worker_pod_count_min",
                    paths["collection"],
                    integer=True,
                ),
                "worker_replicas_observed_max": number_value(
                    collection,
                    "worker_pod_count_max",
                    paths["collection"],
                    integer=True,
                ),
                "api_restart_delta": restarts["api"],
                "gateway_restart_delta": restarts["gateway"],
                "worker_restart_delta": restarts["worker"],
            }
        )
    return rows


def validate_stage_totals(
    summary_rows: list[dict[str, Any]], stage_rows: list[dict[str, Any]]
) -> None:
    metrics = (
        "requests_started",
        "classified_http_requests",
        "responses_2xx",
        "responses_429",
        "responses_5xx",
        "connection_errors",
        "unexpected_statuses",
        "unexpected_failures",
        "dropped_iterations",
    )
    for summary in summary_rows:
        matching = [
            row for row in stage_rows if row["run_id"] == summary["run_id"]
        ]
        if len(matching) != 4:
            raise ConsolidationError(
                f"{summary['run_id']}: esperado quatro estagios"
            )
        for metric in metrics:
            total = sum(row[metric] for row in matching)
            if total != summary[metric]:
                raise ConsolidationError(
                    f"{summary['run_id']}: soma por estagio de {metric}="
                    f"{total}, resumo geral={summary[metric]}"
                )


def build_aggregate_rows(
    summary_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    included = [
        row for row in summary_rows if row["included_in_valid_aggregate"]
    ]
    if tuple(row["run_id"] for row in included) != VALID_AGGREGATE_RUNS:
        raise ConsolidationError(
            "agregado deve conter exclusivamente c3-run-1 e c3-run-2"
        )
    rows = []
    for metric in AGGREGATE_METRICS:
        values = [float(row[metric]) for row in included]
        rows.append(
            {
                "metric": metric,
                "sample_size": 2,
                "mean": rounded(statistics.mean(values)),
                "median": rounded(statistics.median(values)),
                "sample_standard_deviation": rounded(statistics.stdev(values)),
                "min": rounded(min(values)),
                "max": rounded(max(values)),
                "interpretation": (
                    "descriptive synthesis of valid runs only (n=2); "
                    "not a strong statistical estimate"
                ),
            }
        )
    return rows


def build_rate_limit_rows(
    summary_rows: list[dict[str, Any]],
    loaded_runs: list[tuple[Path, dict[str, Any]]],
) -> list[dict[str, Any]]:
    by_run = {run.name: artifacts for run, artifacts in loaded_runs}
    rows = []
    for summary in summary_rows:
        artifacts = by_run[summary["run_id"]]
        protection = artifacts["protection"]
        protection_path = artifacts["paths"]["protection"]
        rows.append(
            {
                "run_id": summary["run_id"],
                "total_received": summary["requests_started"],
                "classified_http_requests": summary["classified_http_requests"],
                "total_accepted": summary["responses_2xx"],
                "total_rejected_429": summary["responses_429"],
                "accepted_percent": percentage(
                    summary["responses_2xx"], summary["requests_started"]
                ),
                "rejected_percent": summary["responses_429_percent"],
                "accepted_throughput_rps": summary["accepted_throughput_rps"],
                "rejected_throughput_rps": summary["rejected_throughput_rps"],
                "unexpected_failures": summary["unexpected_failures"],
                "api_restarts": summary["api_restarts"],
                "gateway_restarts": summary["gateway_restarts"],
                "validity": summary["validity"],
                "rate_limiting_worked": boolean_value(
                    protection, "protection_working", protection_path
                ),
                "architectural_stability": summary["stability"],
            }
        )
    return rows


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
                {name: csv_value(row[name]) for name in fieldnames}
            )


def spreadsheet_value(value: Any) -> Any:
    if value == "":
        return None
    return value


def spreadsheet_number_format(header: str, value: Any) -> str:
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
    if header.endswith("_ms"):
        return "#,##0.000"
    if isinstance(value, int):
        return "#,##0"
    return "0.000000"


def excel_column_name(column_number: int) -> str:
    result = ""
    while column_number:
        column_number, remainder = divmod(column_number - 1, 26)
        result = chr(65 + remainder) + result
    return result


def table_definition(
    headers: tuple[str, ...],
    rows: list[dict[str, Any]],
    *,
    sheet_name: str,
    table_name: str,
    start_row: int = 6,
    start_column: int = 1,
) -> dict[str, Any]:
    values = [[spreadsheet_value(row[name]) for name in headers] for row in rows]
    widths = []
    for index, header in enumerate(headers):
        column_values = ["" if row[index] is None else str(row[index]) for row in values]
        maximum = max([len(header), *(len(value) for value in column_values)])
        if header in {"exclusion_reason", "interpretation"}:
            widths.append(min(max(maximum + 2, 32), 68))
        elif header == "metric":
            widths.append(min(max(maximum + 2, 22), 48))
        else:
            widths.append(min(max(maximum + 2, 12), 28))
    number_formats = [
        [spreadsheet_number_format(header, value) for header, value in zip(headers, row)]
        for row in values
    ]
    address = (
        f"{excel_column_name(start_column)}{start_row}:"
        f"{excel_column_name(start_column + len(headers) - 1)}"
        f"{start_row + len(values)}"
    )
    return {
        "sheetName": sheet_name,
        "tableName": table_name,
        "headers": list(headers),
        "rows": values,
        "widths": widths,
        "numberFormats": number_formats,
        "startRow": start_row,
        "startColumn": start_column,
        "address": address,
    }


def invalid_attempt_rows(summary_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fields = (
        "run_id",
        "validity",
        "stability",
        "requests_started",
        "classified_http_requests",
        "exclusion_reason",
        "responses_2xx",
        "responses_429",
        "responses_5xx",
        "connection_errors",
        "unexpected_failures",
        "dropped_iterations",
        "api_restarts",
        "gateway_restarts",
        "worker_restarts",
    )
    return [
        {field: row[field] for field in fields}
        for row in summary_rows
        if not row["included_in_valid_aggregate"]
    ]


def methodology_rows() -> list[list[str]]:
    return [
        [
            "Objetivo",
            "Avaliar a proteção de entrada do Kong com limite global de 20 requisições por segundo, mantendo API, worker e gateway com uma réplica.",
        ],
        [
            "Tentativas oficiais",
            "Quatro tentativas foram preservadas: c3-run-1 e c3-run-2 são VALID / STABLE; c3-run-3 e c3-run-3-retry-1 são INVALID / UNSTABLE.",
        ],
        [
            "Agregado válido",
            "Somente c3-run-1 e c3-run-2 compõem médias, medianas, desvios-padrão amostrais, mínimos e máximos. Todas as estatísticas declaram sample_size=2.",
        ],
        [
            "Amostra reduzida",
            "Os agregados são uma síntese descritiva com n=2 e não devem ser interpretados como estimativas estatísticas fortes.",
        ],
        [
            "Tentativas inválidas",
            "As duas tentativas inválidas foram mantidas fora das médias de desempenho e aparecem separadamente como evidência de repetibilidade e robustez insuficientes.",
        ],
        [
            "Encerramento",
            "Não foram realizadas novas repetições para evitar seleção oportunista de resultados.",
        ],
        [
            "Rate limiting",
            "As respostas HTTP 429 demonstram que o Kong protegeu a entrada. Essa eficácia é separada da estabilidade arquitetural.",
        ],
        [
            "Estabilidade",
            "O Kong apresentou desempenho estável em duas execuções válidas, mas não impediu reinicializações repetidas da API nas tentativas subsequentes.",
        ],
        [
            "Percentis",
            "Os p95 e p99 agregados resumem percentis calculados em cada execução; amostras brutas não foram combinadas.",
        ],
        [
            "Ligação com C4",
            "O achado fundamenta a avaliação posterior de uma estratégia combinada no C4, sem alterar retroativamente o protocolo do C3.",
        ],
    ]


def build_workbook_specification(
    summary_rows: list[dict[str, Any]],
    stage_rows: list[dict[str, Any]],
    aggregate_rows: list[dict[str, Any]],
    rate_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    invalid_rows = invalid_attempt_rows(summary_rows)
    invalid_fields = tuple(invalid_rows[0])
    summary = table_definition(
        SUMMARY_FIELDS,
        summary_rows,
        sheet_name=WORKBOOK_SHEETS[0],
        table_name="C3RunsTable",
    )
    stages = table_definition(
        STAGE_FIELDS,
        stage_rows,
        sheet_name=WORKBOOK_SHEETS[1],
        table_name="C3StageTable",
    )
    aggregates = table_definition(
        AGGREGATE_FIELDS,
        aggregate_rows,
        sheet_name=WORKBOOK_SHEETS[2],
        table_name="C3AggregateTable",
    )
    rate_limit = table_definition(
        RATE_LIMIT_FIELDS,
        rate_rows,
        sheet_name=WORKBOOK_SHEETS[3],
        table_name="C3RateLimitTable",
    )
    invalid_attempts = table_definition(
        invalid_fields,
        invalid_rows,
        sheet_name=WORKBOOK_SHEETS[4],
        table_name="C3InvalidAttemptsTable",
    )
    return {
        "sheetNames": list(WORKBOOK_SHEETS),
        "summary": summary,
        "stages": stages,
        "aggregates": aggregates,
        "rateLimit": rate_limit,
        "invalidAttempts": invalid_attempts,
        "methodologyRows": methodology_rows(),
        "invalidSummaryRows": [9, 10],
        "invalidRateRows": [9, 10],
        "tables": [summary, stages, aggregates, rate_limit, invalid_attempts],
    }


def artifact_tool_node_modules() -> Path:
    configured = os.environ.get("C3_ARTIFACT_TOOL_NODE_MODULES")
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
        "C3_ARTIFACT_TOOL_NODE_MODULES"
    )


def create_node_modules_link(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
        return
    except OSError as symlink_error:
        if os.name != "nt":
            raise ConsolidationError(
                f"nao foi possivel criar link para artifact-tool: {symlink_error}"
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
            f"nao foi possivel criar juncao para artifact-tool: {detail}"
        )


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
                contents[owner] = contents[owner].replace(
                    relationship_id, stable_id
                )
    with zipfile.ZipFile(
        normalized,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as output:
        for name in sorted(contents):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o600 << 16
            output.writestr(info, contents[name])
    os.replace(normalized, path)


def run_artifact_tool_script(
    node: str,
    script: str,
    working_directory: Path,
    environment: dict[str, str],
    action: str,
) -> None:
    command = subprocess.run(
        [node, "--input-type=module", "-e", script],
        cwd=working_directory,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=180,
    )
    if command.returncode != 0:
        detail = command.stderr.strip() or command.stdout.strip()
        raise ConsolidationError(f"falha ao {action}: {detail}")


def write_workbook(
    path: Path,
    specification: dict[str, Any],
    temporary_directory: Path,
) -> None:
    node = shutil.which("node")
    if node is None:
        raise ConsolidationError("Node.js nao encontrado para gerar a planilha")
    create_node_modules_link(
        temporary_directory / "node_modules", artifact_tool_node_modules()
    )
    specification_path = temporary_directory / "workbook-specification.json"
    specification_path.write_text(
        json.dumps(
            specification,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment["C3_WORKBOOK_SPECIFICATION"] = str(specification_path)
    environment["C3_WORKBOOK_OUTPUT"] = str(path)
    run_artifact_tool_script(
        node,
        WORKBOOK_BUILDER_JS,
        temporary_directory,
        environment,
        "gerar c3-planilha-experimentos.xlsx",
    )
    normalize_workbook_package(path)
    run_artifact_tool_script(
        node,
        WORKBOOK_VALIDATOR_JS,
        temporary_directory,
        environment,
        "validar valores, formulas e renderizacao da planilha do C3",
    )


def consolidate() -> tuple[
    list[dict[str, Any]],
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
    validate_stage_totals(summary_rows, stage_rows)
    aggregate_rows = build_aggregate_rows(summary_rows)
    rate_rows = build_rate_limit_rows(summary_rows, loaded_runs)
    specification = build_workbook_specification(
        summary_rows, stage_rows, aggregate_rows, rate_rows
    )

    OUTPUT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="c3-consolidation-") as temporary:
            temporary_path = Path(temporary)
            write_csv(
                temporary_path / OUTPUT_FILENAMES[0],
                SUMMARY_FIELDS,
                summary_rows,
            )
            write_csv(
                temporary_path / OUTPUT_FILENAMES[1],
                STAGE_FIELDS,
                stage_rows,
            )
            write_csv(
                temporary_path / OUTPUT_FILENAMES[2],
                AGGREGATE_FIELDS,
                aggregate_rows,
            )
            write_csv(
                temporary_path / OUTPUT_FILENAMES[3],
                RATE_LIMIT_FIELDS,
                rate_rows,
            )
            write_workbook(
                temporary_path / OUTPUT_FILENAMES[4],
                specification,
                temporary_path,
            )
            for filename in OUTPUT_FILENAMES:
                os.replace(
                    temporary_path / filename,
                    OUTPUT_DIRECTORY / filename,
                )
    except subprocess.TimeoutExpired as exc:
        raise ConsolidationError("tempo excedido ao gerar a planilha do C3") from exc
    return summary_rows, stage_rows, aggregate_rows, rate_rows


def main() -> int:
    try:
        summary_rows, stage_rows, aggregate_rows, rate_rows = consolidate()
    except ConsolidationError as exc:
        print(f"Erro de consolidacao do C3: {exc}", file=sys.stderr)
        return 1
    print(
        f"C3 consolidado: {len(summary_rows)} tentativas, "
        f"{len(stage_rows)} estagios, {len(aggregate_rows)} agregados "
        f"descritivos com n=2 e {len(rate_rows)} resumos de rate limiting."
    )
    for filename in OUTPUT_FILENAMES:
        print(relative_path(OUTPUT_DIRECTORY / filename))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
