#!/usr/bin/env python3
"""Generate and validate the reproducible C2 experiment workbook."""

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
from datetime import datetime
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CONSOLIDATED_DIRECTORY = REPOSITORY_ROOT / "results/consolidated"
OUTPUT_PATH = CONSOLIDATED_DIRECTORY / "c2-planilha-experimentos.xlsx"
C1_WORKBOOK_PATH = CONSOLIDATED_DIRECTORY / "c1-planilha-experimentos.xlsx"

C1_SUMMARY_PATH = CONSOLIDATED_DIRECTORY / "c1-summary.csv"
C1_AGGREGATE_PATH = CONSOLIDATED_DIRECTORY / "c1-aggregate-summary.csv"
C2_SUMMARY_PATH = CONSOLIDATED_DIRECTORY / "c2-summary.csv"
C2_STAGE_PATH = CONSOLIDATED_DIRECTORY / "c2-stage-summary.csv"
C2_HPA_PATH = CONSOLIDATED_DIRECTORY / "c2-hpa-summary.csv"
C2_AGGREGATE_PATH = CONSOLIDATED_DIRECTORY / "c2-aggregate-summary.csv"

C1_OFFICIAL_DIRECTORY = REPOSITORY_ROOT / "results/experiments/c1/official"
C2_OFFICIAL_DIRECTORY = REPOSITORY_ROOT / "results/experiments/c2/official"
EXPECTED_C1_RUNS = ("c1-run-1", "c1-run-2", "c1-run-3")
EXPECTED_C2_RUNS = ("c2-run-1", "c2-run-2", "c2-run-3")
EXPECTED_SHEETS = (
    "Resumo C2",
    "Resultados por estágio",
    "Comportamento HPA",
    "Comparação C1 x C2",
    "Metodologia",
)

COMPARISON_METRICS = (
    {
        "category": "Capacidade",
        "metric": "throughput_rps",
        "label": "Throughput total",
        "unit": "req/s",
        "number_format": "0.000",
        "reading": "Maior indica mais requisições iniciadas por segundo",
    },
    {
        "category": "Capacidade",
        "metric": "accepted_throughput_rps",
        "label": "Throughput aceito",
        "unit": "req/s",
        "number_format": "0.000",
        "reading": "Maior indica mais respostas 2xx por segundo",
    },
    {
        "category": "Falhas",
        "metric": "unexpected_failures",
        "label": "Falhas inesperadas",
        "unit": "ocorrências",
        "number_format": "#,##0.0",
        "reading": "Menor é melhor",
    },
    {
        "category": "Falhas",
        "metric": "unexpected_failures_percent",
        "label": "Falhas inesperadas",
        "unit": "% das requisições",
        "number_format": "0.000",
        "reading": "Menor é melhor",
    },
    {
        "category": "Latência geral",
        "metric": "latency_p95_ms",
        "label": "Latência geral p95",
        "unit": "ms",
        "number_format": "#,##0.0",
        "reading": "Menor é melhor",
    },
    {
        "category": "Latência geral",
        "metric": "latency_p99_ms",
        "label": "Latência geral p99",
        "unit": "ms",
        "number_format": "#,##0.0",
        "reading": "Menor é melhor",
    },
    {
        "category": "Latência 2xx",
        "metric": "response_2xx_p95_ms",
        "label": "Latência das respostas 2xx p95",
        "unit": "ms",
        "number_format": "#,##0.0",
        "reading": "Menor é melhor",
    },
    {
        "category": "Latência 2xx",
        "metric": "response_2xx_p99_ms",
        "label": "Latência das respostas 2xx p99",
        "unit": "ms",
        "number_format": "#,##0.0",
        "reading": "Menor é melhor",
    },
    {
        "category": "Processamento",
        "metric": "orders_completed",
        "label": "Pedidos concluídos",
        "unit": "pedidos",
        "number_format": "#,##0.0",
        "reading": "Maior é melhor",
    },
    {
        "category": "Processamento",
        "metric": "order_processing_p95_ms",
        "label": "Processamento dos pedidos p95",
        "unit": "ms",
        "number_format": "#,##0.0",
        "reading": "Menor é melhor",
    },
    {
        "category": "Processamento",
        "metric": "order_processing_p99_ms",
        "label": "Processamento dos pedidos p99",
        "unit": "ms",
        "number_format": "#,##0.0",
        "reading": "Menor é melhor",
    },
    {
        "category": "Recuperação",
        "metric": "drain_duration_seconds",
        "label": "Drenagem",
        "unit": "s",
        "number_format": "#,##0.0",
        "reading": "Menor é melhor",
    },
    {
        "category": "Estabilidade",
        "metric": "restarts",
        "label": "Reinícios",
        "unit": "reinícios",
        "number_format": "0.000",
        "reading": "Menor é melhor",
    },
    {
        "category": "Custo normalizado",
        "metric": "app_cpu_seconds_per_1000_completed_orders",
        "label": "CPU por mil pedidos",
        "unit": "CPU-s/1.000 pedidos",
        "number_format": "0.000",
        "reading": "Menor consumo é melhor",
    },
    {
        "category": "Custo normalizado",
        "metric": "app_memory_mib_minutes_per_1000_completed_orders",
        "label": "Memória por mil pedidos",
        "unit": "MiB-min/1.000 pedidos",
        "number_format": "#,##0.0",
        "reading": "Menor consumo é melhor",
    },
)


COMPARISON_CHART_METRICS = {
    "throughput_rps": "Throughput",
    "accepted_throughput_rps": "Throughput 2xx",
    "unexpected_failures": "Falhas",
    "latency_p95_ms": "Latência p95",
    "response_2xx_p95_ms": "Latência 2xx p95",
    "orders_completed": "Pedidos",
    "drain_duration_seconds": "Drenagem",
    "app_cpu_seconds_per_1000_completed_orders": "CPU/1.000",
    "app_memory_mib_minutes_per_1000_completed_orders": "Memória/1.000",
}


WORKBOOK_BUILDER_JS = r"""
import fs from "node:fs/promises";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const specification = JSON.parse(
  await fs.readFile(process.env.C2_WORKBOOK_SPECIFICATION, "utf8"),
);
const outputPath = process.env.C2_WORKBOOK_OUTPUT;
const workbook = Workbook.create();

const COLORS = {
  navy: "#17365D",
  blue: "#2F75B5",
  lightBlue: "#D9EAF7",
  paleBlue: "#EAF3F8",
  text: "#404040",
  white: "#FFFFFF",
  border: "#B4C7E7",
  amber: "#FFF2CC",
  amberStrong: "#F4B183",
  redText: "#9C0006",
  green: "#70AD47",
  orange: "#ED7D31",
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

function tableAddress(startRow, startColumn, rowCount, columnCount) {
  return `${columnLetter(startColumn)}${startRow}:` +
    `${columnLetter(startColumn + columnCount - 1)}${startRow + rowCount - 1}`;
}

function addTable(sheet, definition, startRow, startColumn, tableName) {
  const rows = [definition.headers, ...definition.rows];
  const address = tableAddress(
    startRow,
    startColumn,
    rows.length,
    definition.headers.length,
  );
  const range = sheet.getRange(address);
  range.values = rows;
  range.format.font = { name: FONT, size: 10, color: COLORS.text };
  range.format.verticalAlignment = "center";

  const table = sheet.tables.add(address, true, tableName);
  table.style = "TableStyleMedium2";
  table.showHeaders = true;
  table.showTotals = false;
  table.showBandedColumns = false;
  table.showFilterButton = true;

  const lastColumn = columnLetter(
    startColumn + definition.headers.length - 1,
  );
  const header = sheet.getRange(
    `${columnLetter(startColumn)}${startRow}:${lastColumn}${startRow}`,
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
      `${columnLetter(startColumn)}${startRow + 1}:` +
      `${lastColumn}${startRow + definition.rows.length}`,
    );
    body.format.rowHeight = 20;
    body.format.wrapText = false;
    if (definition.numberFormats) {
      body.format.numberFormat = definition.numberFormats;
    }
  }

  for (let index = 0; index < definition.widths.length; index += 1) {
    const column = columnLetter(startColumn + index);
    sheet.getRange(
      `${column}${startRow}:${column}${startRow + definition.rows.length}`,
    ).format.columnWidth = definition.widths[index];
  }
  return { address, table };
}

for (const name of specification.sheetNames) {
  applyBase(workbook.worksheets.add(name));
}

const summarySheet = workbook.worksheets.getItem("Resumo C2");
applyTitle(
  summarySheet,
  "Resumo dos ensaios oficiais do C2",
  "Três repetições com HPA ativo. Os resultados completos e as estatísticas são preservados sem excluir outliers.",
);
summarySheet.mergeCells("A5:H6");
summarySheet.getRange("A5:H6").values = [[
  "c2-run-2: VALID / UNSTABLE. A execução permaneceu válida para comparação e registrou uma reinicialização da API sob carga.",
]];
summarySheet.getRange("A5:H6").format = {
  fill: COLORS.amber,
  font: { name: FONT, size: 10, bold: true, color: COLORS.redText },
  verticalAlignment: "center",
  wrapText: true,
  borders: { preset: "outside", style: "thin", color: COLORS.amberStrong },
};
summarySheet.getRange("A5:H6").format.rowHeight = 25;
applySection(summarySheet, "A8:H8", "Resultados das três execuções oficiais");
const summaryTable = addTable(
  summarySheet,
  specification.c2Summary,
  9,
  1,
  "C2RunsTable",
);
const summaryLastColumn = columnLetter(specification.c2Summary.headers.length);
summarySheet.getRange(`A11:${summaryLastColumn}11`).format.fill = COLORS.amber;
summarySheet.getRange("C11").format = {
  fill: COLORS.amberStrong,
  font: { name: FONT, size: 10, bold: true, color: COLORS.redText },
};
applySection(summarySheet, "A15:G15", "Estatísticas consolidadas");
addTable(
  summarySheet,
  specification.c2Aggregate,
  16,
  1,
  "C2AggregateTable",
);
summarySheet.freezePanes.freezeRows(9);

const stageSheet = workbook.worksheets.getItem("Resultados por estágio");
applyTitle(
  stageSheet,
  "Resultados do C2 por estágio de carga",
  "As doze linhas correspondem aos quatro estágios de cada uma das três execuções oficiais.",
);
applySection(stageSheet, "A5:H5", "Métricas por estágio");
addTable(
  stageSheet,
  specification.c2Stages,
  6,
  1,
  "C2StageTable",
);
stageSheet.freezePanes.freezeRows(6);

const hpaSheet = workbook.worksheets.getItem("Comportamento HPA");
applyTitle(
  hpaSheet,
  "Comportamento do HPA nas execuções oficiais",
  "O eixo temporal é relativo ao início da carga. Valores negativos representam a coleta ociosa anterior ao k6.",
);
applySection(hpaSheet, "A5:K5", "Síntese por execução");
addTable(
  hpaSheet,
  specification.hpaDisplay,
  6,
  1,
  "C2HpaRunsTable",
);
applySection(hpaSheet, "A12:B12", "Configuração comum");
addTable(
  hpaSheet,
  specification.hpaConfiguration,
  13,
  1,
  "C2HpaConfigurationTable",
);
applySection(hpaSheet, "P3:V3", "Série temporal usada no gráfico");
addTable(
  hpaSheet,
  specification.hpaSeries,
  4,
  16,
  "C2HpaSeriesTable",
);

const hpaChartRange = hpaSheet.getRange(
  `P4:V${4 + specification.hpaSeries.rows.length}`,
);
const hpaChart = hpaSheet.charts.add("line", hpaChartRange);
hpaChart.title = "Réplicas da API e réplicas desejadas pelo HPA";
hpaChart.titleTextStyle.fontSize = 12;
hpaChart.titleTextStyle.typeface = FONT;
hpaChart.legend = { position: "top", textStyle: { typeface: FONT } };
hpaChart.xAxis = {
  axisType: "textAxis",
  textStyle: { typeface: FONT, fontSize: 9 },
};
hpaChart.yAxis = {
  numberFormatCode: "0",
  numberFormatSourceLinked: false,
  textStyle: { typeface: FONT, fontSize: 9 },
};
hpaChart.xAxis.title.text = "Segundos desde o início da carga";
hpaChart.yAxis.title.text = "Réplicas";
hpaChart.setPosition("D12", "N34");
const seriesStyles = [
  { color: "#5B9BD5", style: "solid", width: 2 },
  { color: "#2F5597", style: "dashed", width: 1.5 },
  { color: COLORS.orange, style: "solid", width: 2 },
  { color: "#C55A11", style: "dashed", width: 1.5 },
  { color: COLORS.green, style: "solid", width: 2 },
  { color: "#548235", style: "dashed", width: 1.5 },
];
for (let index = 0; index < hpaChart.series.items.length; index += 1) {
  const style = seriesStyles[index];
  hpaChart.series.items[index].line = {
    fill: style.color,
    style: style.style,
    width: style.width,
  };
}
hpaSheet.freezePanes.freezeRows(6);

const comparisonSheet = workbook.worksheets.getItem("Comparação C1 x C2");
applyTitle(
  comparisonSheet,
  "Comparação dos cenários C1 e C2",
  "Somente métricas metodologicamente equivalentes. Os três ensaios de cada cenário e o c2-run-2 permanecem na análise.",
);
applySection(comparisonSheet, "A5:J5", "Métricas comparáveis");
const comparisonStartRow = 6;
addTable(
  comparisonSheet,
  specification.comparison,
  comparisonStartRow,
  1,
  "C1C2ComparisonTable",
);
const firstComparisonDataRow = comparisonStartRow + 1;
const lastComparisonDataRow =
  comparisonStartRow + specification.comparison.rows.length;
comparisonSheet.getRange(
  `F${firstComparisonDataRow}:F${lastComparisonDataRow}`,
).formulas = specification.comparison.meanVariationFormulas;
comparisonSheet.getRange(
  `I${firstComparisonDataRow}:I${lastComparisonDataRow}`,
).formulas = specification.comparison.medianVariationFormulas;
comparisonSheet.getRange(
  `F${firstComparisonDataRow}:F${lastComparisonDataRow}`,
).format.numberFormat = "+0.0%;-0.0%;0.0%";
comparisonSheet.getRange(
  `I${firstComparisonDataRow}:I${lastComparisonDataRow}`,
).format.numberFormat = "+0.0%;-0.0%;0.0%";
comparisonSheet.getRange(`A${lastComparisonDataRow}:J${lastComparisonDataRow}`)
  .format.fill = COLORS.paleBlue;

addTable(
  comparisonSheet,
  specification.comparison.chartData,
  34,
  12,
  "C1C2ChartDataTable",
);
const comparisonChartLastRow =
  34 + specification.comparison.chartData.rows.length;
comparisonSheet.getRange(
  `M35:M${comparisonChartLastRow}`,
).formulas = specification.comparison.chartData.formulas;
comparisonSheet.getRange(
  `M35:M${comparisonChartLastRow}`,
).format.numberFormat = "+0.0%;-0.0%;0.0%";

const comparisonChart = comparisonSheet.charts.add(
  "bar",
  comparisonSheet.getRange(`L34:M${comparisonChartLastRow}`),
);
comparisonChart.title = "Variação da média do C2 em relação ao C1";
comparisonChart.titleTextStyle.fontSize = 12;
comparisonChart.titleTextStyle.typeface = FONT;
comparisonChart.hasLegend = false;
comparisonChart.xAxis = {
  axisType: "textAxis",
  textStyle: { typeface: FONT, fontSize: 9 },
};
comparisonChart.yAxis = {
  numberFormatCode: "0%",
  numberFormatSourceLinked: false,
  textStyle: { typeface: FONT, fontSize: 9 },
};
comparisonChart.setPosition("L6", "V31");
if (comparisonChart.series.items.length > 0) {
  comparisonChart.series.items[0].fill = COLORS.blue;
}
comparisonSheet.getRange("A25:J27").values = [[
  "Leitura da variação",
  "A variação é (C2 ÷ C1) − 1. Valores positivos indicam aumento; a coluna ‘Leitura’ informa se aumentar ou reduzir é desejável para cada métrica.",
  null,
  null,
  null,
  null,
  null,
  null,
  null,
  null,
], [
  "Percentis",
  "As médias e medianas de p95/p99 resumem os percentis das três execuções. As amostras brutas não foram combinadas.",
  null,
  null,
  null,
  null,
  null,
  null,
  null,
  null,
], [
  "Outliers",
  "Nenhuma execução foi removida. O c2-run-2 permanece nos valores médios, medianos, extremos e desvios.",
  null,
  null,
  null,
  null,
  null,
  null,
  null,
  null,
]];
comparisonSheet.getRange("A25:A27").format = {
  fill: COLORS.lightBlue,
  font: { name: FONT, size: 10, bold: true, color: COLORS.navy },
};
comparisonSheet.getRange("B25:J27").merge(true);
comparisonSheet.getRange("B25:J27").format = {
  font: { name: FONT, size: 10, color: COLORS.text },
  wrapText: true,
  verticalAlignment: "center",
};
comparisonSheet.getRange("A25:J27").format.rowHeight = 32;
comparisonSheet.freezePanes.freezeRows(6);

const methodologySheet = workbook.worksheets.getItem("Metodologia");
applyTitle(
  methodologySheet,
  "Metodologia da consolidação e da comparação",
  "Registro do desenho experimental, das estatísticas e dos limites de interpretação do C2.",
);
methodologySheet.getRange("A5:B5").values = [["Tópico", "Registro metodológico"]];
methodologySheet.getRange("A6:B17").values = specification.methodologyRows;
methodologySheet.getRange("A5:B17").format.font = {
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
methodologySheet.getRange("A6:A17").format = {
  fill: COLORS.lightBlue,
  font: { name: FONT, size: 10, bold: true, color: COLORS.navy },
  verticalAlignment: "center",
  wrapText: true,
  borders: { preset: "inside", style: "thin", color: COLORS.border },
};
methodologySheet.getRange("B6:B17").format = {
  font: { name: FONT, size: 10, color: COLORS.text },
  verticalAlignment: "center",
  wrapText: true,
  borders: { preset: "inside", style: "thin", color: COLORS.border },
};
methodologySheet.getRange("A6:B17").format.rowHeight = 44;
methodologySheet.getRange("A5:A17").format.columnWidth = 28;
methodologySheet.getRange("B5:B17").format.columnWidth = 105;

workbook.recalculate();

const formulaErrors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
  options: { useRegex: true, maxResults: 100 },
  summary: "formula error scan before export",
});
const errorTokens = [
  "#REF!", "#DIV/0!", "#VALUE!", "#NAME?", "#N/A",
  "#NUM!", "#NULL!", "#SPILL!", "#CALC!",
];
if (errorTokens.some((token) => formulaErrors.ndjson.includes(token))) {
  throw new Error(`erros de formula antes da exportacao: ${formulaErrors.ndjson}`);
}

const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
"""


WORKBOOK_VALIDATOR_JS = r"""
import fs from "node:fs/promises";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const specification = JSON.parse(
  await fs.readFile(process.env.C2_WORKBOOK_SPECIFICATION, "utf8"),
);
const workbookPath = process.env.C2_WORKBOOK_OUTPUT;
const previewDirectory = process.env.C2_WORKBOOK_PREVIEW_DIRECTORY;
const workbook = await SpreadsheetFile.importXlsx(
  await FileBlob.load(workbookPath),
);

const actualSheetNames = workbook.worksheets.items.map((sheet) => sheet.name);
if (JSON.stringify(actualSheetNames) !== JSON.stringify(specification.sheetNames)) {
  throw new Error(
    `nomes ou ordem das abas divergentes: ${JSON.stringify(actualSheetNames)}`,
  );
}

const expectedTables = {
  "Resumo C2": ["C2RunsTable", "C2AggregateTable"],
  "Resultados por estágio": ["C2StageTable"],
  "Comportamento HPA": [
    "C2HpaRunsTable",
    "C2HpaConfigurationTable",
    "C2HpaSeriesTable",
  ],
  "Comparação C1 x C2": [
    "C1C2ComparisonTable",
    "C1C2ChartDataTable",
  ],
  "Metodologia": [],
};
for (const [sheetName, tableNames] of Object.entries(expectedTables)) {
  const sheet = workbook.worksheets.getItem(sheetName);
  const actual = sheet.tables.items.map((table) => table.name);
  if (JSON.stringify(actual) !== JSON.stringify(tableNames)) {
    throw new Error(
      `${sheetName}: tabelas divergentes; esperado ${tableNames}, obtido ${actual}`,
    );
  }
  for (const table of sheet.tables.items) {
    if (table.style !== "TableStyleMedium2" || !table.showFilterButton) {
      throw new Error(`${sheetName}/${table.name}: estilo ou filtro invalido`);
    }
  }
}

const summarySheet = workbook.worksheets.getItem("Resumo C2");
const runValues = summarySheet.getRange("A10:C12").values;
const expectedStatuses = [
  ["c2-run-1", "VALID", "STABLE"],
  ["c2-run-2", "VALID", "UNSTABLE"],
  ["c2-run-3", "VALID", "STABLE"],
];
if (JSON.stringify(runValues) !== JSON.stringify(expectedStatuses)) {
  throw new Error(`validade/estabilidade divergentes: ${JSON.stringify(runValues)}`);
}

const comparisonSheet = workbook.worksheets.getItem("Comparação C1 x C2");
const firstRow = 7;
const lastRow = 6 + specification.comparison.rows.length;
const meanFormulas = comparisonSheet.getRange(`F${firstRow}:F${lastRow}`).formulas;
const medianFormulas = comparisonSheet.getRange(`I${firstRow}:I${lastRow}`).formulas;
if (JSON.stringify(meanFormulas) !== JSON.stringify(specification.comparison.meanVariationFormulas)) {
  throw new Error("formulas de variacao das medias divergentes");
}
if (JSON.stringify(medianFormulas) !== JSON.stringify(specification.comparison.medianVariationFormulas)) {
  throw new Error("formulas de variacao das medianas divergentes");
}
const chartLastRow = 34 + specification.comparison.chartData.rows.length;
const chartFormulas = comparisonSheet.getRange(`M35:M${chartLastRow}`).formulas;
if (JSON.stringify(chartFormulas) !== JSON.stringify(specification.comparison.chartData.formulas)) {
  throw new Error("formulas da fonte do grafico comparativo divergentes");
}

const hpaSheet = workbook.worksheets.getItem("Comportamento HPA");
if (hpaSheet.charts.items.length !== 1) {
  throw new Error("Comportamento HPA deve possuir exatamente um grafico");
}
if (hpaSheet.charts.items[0].series.items.length !== 6) {
  throw new Error("grafico do HPA deve possuir seis series");
}
if (comparisonSheet.charts.items.length !== 1) {
  throw new Error("Comparacao C1 x C2 deve possuir exatamente um grafico");
}

const formulaErrors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
  options: { useRegex: true, maxResults: 100 },
  summary: "final formula error scan",
});
const errorTokens = [
  "#REF!", "#DIV/0!", "#VALUE!", "#NAME?", "#N/A",
  "#NUM!", "#NULL!", "#SPILL!", "#CALC!",
];
if (errorTokens.some((token) => formulaErrors.ndjson.includes(token))) {
  throw new Error(`erros de formula apos reabertura: ${formulaErrors.ndjson}`);
}

await fs.mkdir(previewDirectory, { recursive: true });
for (let index = 0; index < workbook.worksheets.items.length; index += 1) {
  const sheet = workbook.worksheets.getItemAt(index);
  const preview = await workbook.render({
    sheetName: sheet.name,
    autoCrop: "all",
    scale: 1,
    format: "png",
  });
  const bytes = new Uint8Array(await preview.arrayBuffer());
  if (bytes.length < 1000) {
    throw new Error(`renderizacao vazia ou invalida da aba ${sheet.name}`);
  }
  await fs.writeFile(
    `${previewDirectory}/sheet-${index + 1}.png`,
    bytes,
  );
}

const compact = await workbook.inspect({
  kind: "workbook,sheet,table,drawing",
  maxChars: 8000,
  tableMaxRows: 3,
  tableMaxCols: 5,
  tableMaxCellChars: 60,
});
console.log(compact.ndjson);
console.log("FORMULA_ERROR_SCAN=0");
console.log(`RENDERED_SHEETS=${workbook.worksheets.items.length}`);
"""


class SpreadsheetGenerationError(Exception):
    """Raised when the C2 workbook cannot be generated safely."""


def relative_path(path: Path) -> str:
    return path.relative_to(REPOSITORY_ROOT).as_posix()


def require_files(paths: list[Path]) -> None:
    missing = [relative_path(path) for path in paths if not path.is_file()]
    if missing:
        raise SpreadsheetGenerationError(
            "arquivos obrigatorios ausentes: " + ", ".join(missing)
        )


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    try:
        with path.open(encoding="utf-8", newline="") as source:
            reader = csv.DictReader(source)
            if not reader.fieldnames:
                raise SpreadsheetGenerationError(
                    f"CSV sem cabecalho: {relative_path(path)}"
                )
            headers = list(reader.fieldnames)
            rows = list(reader)
    except OSError as exc:
        raise SpreadsheetGenerationError(
            f"nao foi possivel ler {relative_path(path)}: {exc}"
        ) from exc
    if not rows:
        raise SpreadsheetGenerationError(
            f"CSV sem linhas de dados: {relative_path(path)}"
        )
    if any(None in row for row in rows):
        raise SpreadsheetGenerationError(
            f"CSV possui colunas excedentes: {relative_path(path)}"
        )
    return headers, rows


def load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as source:
            value = json.load(source)
    except (OSError, json.JSONDecodeError) as exc:
        raise SpreadsheetGenerationError(
            f"JSON invalido ou ilegivel em {relative_path(path)}: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise SpreadsheetGenerationError(
            f"JSON deve conter um objeto: {relative_path(path)}"
        )
    return value


def spreadsheet_value(value: str) -> str | int | float | bool | None:
    if value == "":
        return None
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    try:
        if value.lstrip("-").isdigit():
            return int(value)
        number = float(value)
    except ValueError:
        return value
    return number if math.isfinite(number) else value


def spreadsheet_number_format(value: str) -> str:
    parsed = spreadsheet_value(value)
    if isinstance(parsed, bool) or not isinstance(parsed, (int, float)):
        return "General"
    decimal_places = len(value.split(".", maxsplit=1)[1]) if "." in value else 0
    if decimal_places == 0:
        return "0"
    return "0." + "0" * min(decimal_places, 9)


def column_widths(headers: list[str], rows: list[dict[str, str]]) -> list[int]:
    widths = []
    for header in headers:
        values = [header, *(row[header] for row in rows)]
        maximum = max(len(value) for value in values)
        if header in {"metric", "aggregation_method"}:
            widths.append(min(max(maximum + 2, 20), 54))
        elif header in {"scale_up_policies", "scale_down_policies"}:
            widths.append(28)
        else:
            widths.append(min(max(maximum + 2, 12), 25))
    return widths


def csv_table_specification(
    headers: list[str], rows: list[dict[str, str]]
) -> dict[str, Any]:
    return {
        "headers": headers,
        "rows": [
            [spreadsheet_value(row[header]) for header in headers]
            for row in rows
        ],
        "numberFormats": [
            [spreadsheet_number_format(row[header]) for header in headers]
            for row in rows
        ],
        "widths": column_widths(headers, rows),
    }


def records_by_key(
    rows: list[dict[str, str]], key: str, source: Path
) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for row in rows:
        value = row[key]
        if value in result:
            raise SpreadsheetGenerationError(
                f"chave duplicada '{value}' em {relative_path(source)}"
            )
        result[value] = row
    return result


def number(row: dict[str, str], key: str, source: Path) -> float:
    try:
        value = float(row[key])
    except (KeyError, ValueError) as exc:
        raise SpreadsheetGenerationError(
            f"valor numerico invalido em {relative_path(source)}: {key}"
        ) from exc
    if not math.isfinite(value):
        raise SpreadsheetGenerationError(
            f"valor nao finito em {relative_path(source)}: {key}"
        )
    return value


def validate_summary(
    rows: list[dict[str, str]], expected_runs: tuple[str, ...], scenario: str
) -> None:
    run_ids = tuple(row["run_id"] for row in rows)
    if run_ids != expected_runs:
        raise SpreadsheetGenerationError(
            f"resumo de {scenario} deve conter {expected_runs}; obtido {run_ids}"
        )
    for row in rows:
        if row["validity"] != "VALID":
            raise SpreadsheetGenerationError(
                f"{row['run_id']} nao esta VALID no resumo consolidado"
            )


def normalized_load_profile(metadata: dict[str, Any]) -> str:
    load = metadata.get("load")
    if not isinstance(load, dict):
        raise SpreadsheetGenerationError("metadados sem objeto load")
    normalized = dict(load)
    normalized.pop("scenario", None)
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"))


def validate_same_official_load() -> None:
    profiles: dict[str, list[str]] = {"c1": [], "c2": []}
    for scenario, directory, run_names in (
        ("c1", C1_OFFICIAL_DIRECTORY, EXPECTED_C1_RUNS),
        ("c2", C2_OFFICIAL_DIRECTORY, EXPECTED_C2_RUNS),
    ):
        for run_name in run_names:
            metadata_path = directory / run_name / "run-metadata.json"
            metadata = load_json(metadata_path)
            load = metadata.get("load")
            load_scenario = (
                str(load.get("scenario", "")).lower()
                if isinstance(load, dict)
                else ""
            )
            root_scenario = str(metadata.get("scenario", load_scenario)).lower()
            if load_scenario != scenario or root_scenario != scenario:
                raise SpreadsheetGenerationError(
                    f"cenario divergente em {relative_path(metadata_path)}"
                )
            profiles[scenario].append(normalized_load_profile(metadata))
        if len(set(profiles[scenario])) != 1:
            raise SpreadsheetGenerationError(
                f"as execucoes de {scenario} nao usam a mesma carga"
            )
    if profiles["c1"][0] != profiles["c2"][0]:
        raise SpreadsheetGenerationError(
            "C1 e C2 nao usam a mesma carga oficial"
        )


def validate_c2_inputs(
    c2_summary_rows: list[dict[str, str]],
    stage_rows: list[dict[str, str]],
    hpa_rows: list[dict[str, str]],
) -> None:
    stability = {row["run_id"]: row["stability"] for row in c2_summary_rows}
    expected_stability = {
        "c2-run-1": "STABLE",
        "c2-run-2": "UNSTABLE",
        "c2-run-3": "STABLE",
    }
    if stability != expected_stability:
        raise SpreadsheetGenerationError(
            f"estabilidade do C2 divergente: {stability}"
        )

    if len(stage_rows) != 12:
        raise SpreadsheetGenerationError(
            f"resumo por estagio deve possuir 12 linhas; possui {len(stage_rows)}"
        )
    for run_name in EXPECTED_C2_RUNS:
        observed = tuple(
            row["stage"] for row in stage_rows if row["run_id"] == run_name
        )
        if observed != ("stage_1", "stage_2", "stage_3", "stage_4"):
            raise SpreadsheetGenerationError(
                f"estagios divergentes para {run_name}: {observed}"
            )

    hpa_by_run = records_by_key(hpa_rows, "run_id", C2_HPA_PATH)
    if tuple(hpa_by_run) != EXPECTED_C2_RUNS:
        raise SpreadsheetGenerationError("resumo do HPA deve conter os tres runs")
    for run_name, row in hpa_by_run.items():
        required_values = {
            "initial_api_replicas": 1,
            "configured_min_replicas": 1,
            "configured_max_replicas": 5,
            "target_cpu_utilization": 70,
        }
        for key, expected in required_values.items():
            if number(row, key, C2_HPA_PATH) != expected:
                raise SpreadsheetGenerationError(
                    f"{run_name}: {key} deve ser {expected}"
                )


def parse_timestamp(value: str, source: Path) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SpreadsheetGenerationError(
            f"timestamp invalido em {relative_path(source)}: {value}"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SpreadsheetGenerationError(
            f"timestamp sem fuso em {relative_path(source)}: {value}"
        )
    return parsed


def load_hpa_time_series() -> dict[str, Any]:
    series: dict[str, list[tuple[float, int, int]]] = {}
    first_offsets = []
    for run_name in EXPECTED_C2_RUNS:
        metadata_path = C2_OFFICIAL_DIRECTORY / run_name / "run-metadata.json"
        hpa_path = C2_OFFICIAL_DIRECTORY / run_name / "metrics/hpa-samples.csv"
        metadata = load_json(metadata_path)
        timestamps = metadata.get("timestamps")
        if not isinstance(timestamps, dict) or not isinstance(
            timestamps.get("load_started_at"), str
        ):
            raise SpreadsheetGenerationError(
                f"inicio da carga ausente em {relative_path(metadata_path)}"
            )
        load_started = parse_timestamp(
            timestamps["load_started_at"], metadata_path
        )
        headers, rows = read_csv(hpa_path)
        required = {"timestamp", "current_replicas", "desired_replicas"}
        if not required.issubset(headers):
            raise SpreadsheetGenerationError(
                f"colunas do HPA ausentes em {relative_path(hpa_path)}"
            )
        samples = []
        previous_timestamp: datetime | None = None
        for row in rows:
            timestamp = parse_timestamp(row["timestamp"], hpa_path)
            if previous_timestamp is not None and timestamp <= previous_timestamp:
                raise SpreadsheetGenerationError(
                    f"serie do HPA fora de ordem em {relative_path(hpa_path)}"
                )
            previous_timestamp = timestamp
            try:
                current = int(row["current_replicas"])
                desired = int(row["desired_replicas"])
            except ValueError as exc:
                raise SpreadsheetGenerationError(
                    f"replicas invalidas em {relative_path(hpa_path)}"
                ) from exc
            if not (1 <= current <= 5 and 1 <= desired <= 5):
                raise SpreadsheetGenerationError(
                    f"replicas fora de 1..5 em {relative_path(hpa_path)}"
                )
            offset = (timestamp - load_started).total_seconds()
            samples.append((offset, current, desired))
        if not samples:
            raise SpreadsheetGenerationError(
                f"serie vazia em {relative_path(hpa_path)}"
            )
        series[run_name] = samples
        first_offsets.append(samples[0][0])

    max_length = max(len(samples) for samples in series.values())
    nominal_start = round(min(first_offsets) / 5) * 5
    headers = ["Segundos desde início da carga"]
    for run_name in EXPECTED_C2_RUNS:
        headers.extend((f"Atual {run_name}", f"Desejada {run_name}"))
    rows: list[list[int | None]] = []
    for index in range(max_length):
        row: list[int | None] = [nominal_start + index * 5]
        for run_name in EXPECTED_C2_RUNS:
            samples = series[run_name]
            if index < len(samples):
                row.extend((samples[index][1], samples[index][2]))
            else:
                row.extend((None, None))
        rows.append(row)
    return {
        "headers": headers,
        "rows": rows,
        "numberFormats": [["0"] * len(headers) for _ in rows],
        "widths": [22, 17, 19, 17, 19, 17, 19],
    }


def policy_description(
    policies_json: str, stabilization: str, selector: str
) -> str:
    try:
        policies = json.loads(policies_json)
    except json.JSONDecodeError as exc:
        raise SpreadsheetGenerationError("politica do HPA possui JSON invalido") from exc
    if not isinstance(policies, list) or not policies:
        raise SpreadsheetGenerationError("politica do HPA deve ser uma lista")
    descriptions = []
    for policy in policies:
        if not isinstance(policy, dict):
            raise SpreadsheetGenerationError("politica do HPA deve ser objeto")
        value = policy.get("value")
        policy_type = policy.get("type")
        if policy_type == "Percent":
            quantity = f"{value}%"
        elif policy_type == "Pods":
            quantity = f"{value} {'Pod' if value == 1 else 'Pods'}"
        else:
            quantity = f"{value} {policy_type}"
        descriptions.append(f"{quantity}/{policy.get('periodSeconds')} s")
    return (
        f"{stabilization} s de estabilização; seleção {selector}; "
        + "; ".join(descriptions)
    )


def hpa_display_specification(hpa_rows: list[dict[str, str]]) -> dict[str, Any]:
    headers = [
        "Execução",
        "Inicial",
        "Pods mín. observados",
        "Pods máx. observados",
        "Desejadas mín.",
        "Desejadas máx.",
        "1ª solicitação (s)",
        "Múltiplos Pods (s)",
        "CPU alvo",
        "Instante da 1ª solicitação",
        "Instante de múltiplos Pods",
    ]
    rows = []
    for row in hpa_rows:
        rows.append(
            [
                row["run_id"],
                int(row["initial_api_replicas"]),
                int(row["api_pods_observed_min"]),
                int(row["api_pods_observed_max"]),
                int(row["hpa_desired_replicas_min"]),
                int(row["hpa_desired_replicas_max"]),
                float(row["seconds_from_load_start_to_first_scale_request"]),
                float(row["seconds_from_load_start_to_multiple_api_pods"]),
                float(row["target_cpu_utilization"]) / 100,
                f"UTC {row['first_scale_request_at']}",
                f"UTC {row['multiple_api_pods_observed_at']}",
            ]
        )
    return {
        "headers": headers,
        "rows": rows,
        "numberFormats": [
            ["General", "0", "0", "0", "0", "0", "0.000", "0.000", "0%", "General", "General"]
            for _ in rows
        ],
        "widths": [14, 10, 18, 18, 16, 16, 18, 18, 12, 27, 27],
    }


def hpa_configuration_specification(
    hpa_rows: list[dict[str, str]]
) -> dict[str, Any]:
    reference = hpa_rows[0]
    expected_fields = (
        "hpa_name",
        "target_kind",
        "target_name",
        "configured_min_replicas",
        "configured_max_replicas",
        "target_cpu_utilization",
        "scale_up_stabilization_seconds",
        "scale_up_select_policy",
        "scale_up_policies",
        "scale_down_stabilization_seconds",
        "scale_down_select_policy",
        "scale_down_policies",
    )
    for row in hpa_rows[1:]:
        if any(row[field] != reference[field] for field in expected_fields):
            raise SpreadsheetGenerationError(
                "a configuracao do HPA diverge entre as execucoes"
            )
    rows: list[list[Any]] = [
        ["Nome", reference["hpa_name"]],
        ["Alvo", f"{reference['target_kind']}/{reference['target_name']}"],
        ["Réplicas mínimas", int(reference["configured_min_replicas"])],
        ["Réplicas máximas", int(reference["configured_max_replicas"])],
        ["Alvo de CPU", float(reference["target_cpu_utilization"]) / 100],
        [
            "Scale-up",
            policy_description(
                reference["scale_up_policies"],
                reference["scale_up_stabilization_seconds"],
                reference["scale_up_select_policy"],
            ),
        ],
        [
            "Scale-down",
            policy_description(
                reference["scale_down_policies"],
                reference["scale_down_stabilization_seconds"],
                reference["scale_down_select_policy"],
            ),
        ],
    ]
    return {
        "headers": ["Parâmetro", "Valor"],
        "rows": rows,
        "numberFormats": [
            ["General", "General"],
            ["General", "General"],
            ["General", "0"],
            ["General", "0"],
            ["General", "0%"],
            ["General", "General"],
            ["General", "General"],
        ],
        "widths": [23, 61],
    }


def aggregate_index(
    rows: list[dict[str, str]], source: Path
) -> dict[str, dict[str, str]]:
    return records_by_key(rows, "metric", source)


def comparison_specification(
    c1_summary_rows: list[dict[str, str]],
    c2_summary_rows: list[dict[str, str]],
    c1_aggregate_rows: list[dict[str, str]],
    c2_aggregate_rows: list[dict[str, str]],
) -> dict[str, Any]:
    c1_aggregate = aggregate_index(c1_aggregate_rows, C1_AGGREGATE_PATH)
    c2_aggregate = aggregate_index(c2_aggregate_rows, C2_AGGREGATE_PATH)
    rows: list[list[Any]] = []
    formats: list[list[str]] = []
    chart_rows: list[list[Any]] = []
    chart_formulas: list[list[str]] = []
    for definition in COMPARISON_METRICS:
        metric = definition["metric"]
        if metric not in c1_aggregate or metric not in c2_aggregate:
            raise SpreadsheetGenerationError(
                f"metrica comparavel ausente: {metric}"
            )
        c1_row = c1_aggregate[metric]
        c2_row = c2_aggregate[metric]
        rows.append(
            [
                definition["category"],
                definition["label"],
                definition["unit"],
                number(c1_row, "mean", C1_AGGREGATE_PATH),
                number(c2_row, "mean", C2_AGGREGATE_PATH),
                None,
                number(c1_row, "median", C1_AGGREGATE_PATH),
                number(c2_row, "median", C2_AGGREGATE_PATH),
                None,
                definition["reading"],
            ]
        )
        value_format = definition["number_format"]
        formats.append(
            [
                "General",
                "General",
                "General",
                value_format,
                value_format,
                "+0.0%;-0.0%;0.0%",
                value_format,
                value_format,
                "+0.0%;-0.0%;0.0%",
                "General",
            ]
        )
        if metric in COMPARISON_CHART_METRICS:
            source_row = 6 + len(rows)
            chart_rows.append([COMPARISON_CHART_METRICS[metric], None])
            chart_formulas.append([f"=F{source_row}"])

    c1_stability = [1 if row["stability"] == "STABLE" else 0 for row in c1_summary_rows]
    c2_stability = [1 if row["stability"] == "STABLE" else 0 for row in c2_summary_rows]
    rows.append(
        [
            "Estabilidade",
            "Execuções estáveis",
            "% das execuções",
            statistics.mean(c1_stability),
            statistics.mean(c2_stability),
            None,
            statistics.median(c1_stability),
            statistics.median(c2_stability),
            None,
            "Maior é melhor; C1: 0/3, C2: 2/3",
        ]
    )
    formats.append(
        [
            "General",
            "General",
            "General",
            "0.0%",
            "0.0%",
            "+0.0%;-0.0%;0.0%",
            "0.0%",
            "0.0%",
            "+0.0%;-0.0%;0.0%",
            "General",
        ]
    )

    start_row = 7
    mean_formulas = []
    median_formulas = []
    for index in range(len(rows)):
        excel_row = start_row + index
        mean_formulas.append(
            [f'=IF(D{excel_row}=0,"n.a.",E{excel_row}/D{excel_row}-1)']
        )
        median_formulas.append(
            [f'=IF(G{excel_row}=0,"n.a.",H{excel_row}/G{excel_row}-1)']
        )
    return {
        "headers": [
            "Categoria",
            "Métrica",
            "Unidade",
            "Média C1",
            "Média C2",
            "Variação da média",
            "Mediana C1",
            "Mediana C2",
            "Variação da mediana",
            "Leitura",
        ],
        "rows": rows,
        "numberFormats": formats,
        "widths": [20, 36, 23, 15, 15, 18, 15, 15, 20, 45],
        "meanVariationFormulas": mean_formulas,
        "medianVariationFormulas": median_formulas,
        "chartData": {
            "headers": ["Métrica", "Variação média"],
            "rows": chart_rows,
            "numberFormats": [
                ["General", "+0.0%;-0.0%;0.0%"] for _ in chart_rows
            ],
            "widths": [22, 18],
            "formulas": chart_formulas,
        },
    }


def methodology_rows() -> list[list[str]]:
    return [
        [
            "Repetições oficiais",
            "C1 e C2 possuem três repetições oficiais independentes. Cada execução é a unidade usada nas estatísticas consolidadas.",
        ],
        [
            "Carga oficial",
            "C1 e C2 usaram a mesma carga: ramping-arrival-rate, taxa inicial de 1 req/s e estágios 20 req/s por 20 s, 22 req/s por 30 s, 22 req/s por 30 s e 0 req/s por 10 s.",
        ],
        [
            "Variável principal",
            "C1 manteve uma réplica fixa da API. C2 habilitou o HPA da API com mínimo 1, máximo 5 e alvo de CPU de 70%.",
        ],
        [
            "Estatísticas",
            "Média, mediana, desvio-padrão amostral, mínimo e máximo são calculados sobre os três valores observados por cenário.",
        ],
        [
            "Percentis",
            "Os percentis p95 e p99 são calculados em cada execução. A consolidação resume os três percentis individuais e não combina amostras brutas entre execuções.",
        ],
        [
            "Validade",
            "VALID indica que o protocolo terminou com os artefatos obrigatórios e as verificações de execução aprovadas. Validade não significa ausência de saturação.",
        ],
        [
            "Estabilidade",
            "STABLE/UNSTABLE descreve sinais operacionais como reinicializações e drenagem. Uma execução pode ser VALID e UNSTABLE.",
        ],
        [
            "c2-run-2",
            "O c2-run-2 é VALID / UNSTABLE porque registrou uma reinicialização da API sob carga. Ele permanece em todas as estatísticas e comparações.",
        ],
        [
            "Comparação",
            "A comparação C1 x C2 usa somente métricas presentes nos dois cenários. A variação percentual é calculada por (C2 ÷ C1) − 1 quando o valor de C1 é diferente de zero.",
        ],
        [
            "Série temporal HPA",
            "O gráfico usa a amostragem do HPA em intervalos de 5 s. O tempo é normalizado pelo início da carga; valores negativos mostram a coleta ociosa anterior ao k6.",
        ],
        [
            "Fontes consolidadas",
            "results/consolidated/c1-summary.csv, c1-aggregate-summary.csv, c2-summary.csv, c2-stage-summary.csv, c2-hpa-summary.csv e c2-aggregate-summary.csv.",
        ],
        [
            "Fonte temporal",
            "results/experiments/c2/official/c2-run-{1,2,3}/metrics/hpa-samples.csv, preservados sem alteração.",
        ],
    ]


def artifact_tool_node_modules() -> Path:
    configured = os.environ.get("C2_ARTIFACT_TOOL_NODE_MODULES")
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
    raise SpreadsheetGenerationError(
        "dependencia @oai/artifact-tool nao encontrada; defina "
        "C2_ARTIFACT_TOOL_NODE_MODULES"
    )


def create_node_modules_link(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
        return
    except OSError as symlink_error:
        if os.name != "nt":
            raise SpreadsheetGenerationError(
                f"nao foi possivel criar link para @oai/artifact-tool: "
                f"{symlink_error}"
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
        raise SpreadsheetGenerationError(
            f"nao foi possivel criar juncao para @oai/artifact-tool: {detail}"
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
        replacements = {
            relationship_id: f"rId{index}".encode("ascii")
            for index, relationship_id in enumerate(relationship_ids, start=1)
        }
        for relationship_id, stable_id in replacements.items():
            contents[name] = contents[name].replace(relationship_id, stable_id)
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
) -> str:
    try:
        command = subprocess.run(
            [node, "--input-type=module", "-e", script],
            cwd=temporary_path,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=180,
        )
    except subprocess.TimeoutExpired as exc:
        raise SpreadsheetGenerationError(
            f"tempo excedido ao {action}"
        ) from exc
    if command.returncode != 0:
        detail = command.stderr.strip() or command.stdout.strip()
        raise SpreadsheetGenerationError(f"falha ao {action}: {detail}")
    return command.stdout.strip()


def build_specification() -> dict[str, Any]:
    required = [
        C1_WORKBOOK_PATH,
        C1_SUMMARY_PATH,
        C1_AGGREGATE_PATH,
        C2_SUMMARY_PATH,
        C2_STAGE_PATH,
        C2_HPA_PATH,
        C2_AGGREGATE_PATH,
    ]
    for directory, run_names in (
        (C1_OFFICIAL_DIRECTORY, EXPECTED_C1_RUNS),
        (C2_OFFICIAL_DIRECTORY, EXPECTED_C2_RUNS),
    ):
        required.extend(
            directory / run_name / "run-metadata.json"
            for run_name in run_names
        )
    required.extend(
        C2_OFFICIAL_DIRECTORY / run_name / "metrics/hpa-samples.csv"
        for run_name in EXPECTED_C2_RUNS
    )
    require_files(required)

    c1_summary_headers, c1_summary_rows = read_csv(C1_SUMMARY_PATH)
    c1_aggregate_headers, c1_aggregate_rows = read_csv(C1_AGGREGATE_PATH)
    c2_summary_headers, c2_summary_rows = read_csv(C2_SUMMARY_PATH)
    stage_headers, stage_rows = read_csv(C2_STAGE_PATH)
    hpa_headers, hpa_rows = read_csv(C2_HPA_PATH)
    c2_aggregate_headers, c2_aggregate_rows = read_csv(C2_AGGREGATE_PATH)

    del c1_summary_headers, c1_aggregate_headers, hpa_headers
    validate_summary(c1_summary_rows, EXPECTED_C1_RUNS, "c1")
    validate_summary(c2_summary_rows, EXPECTED_C2_RUNS, "c2")
    validate_c2_inputs(c2_summary_rows, stage_rows, hpa_rows)
    validate_same_official_load()

    return {
        "sheetNames": list(EXPECTED_SHEETS),
        "c2Summary": csv_table_specification(
            c2_summary_headers, c2_summary_rows
        ),
        "c2Stages": csv_table_specification(stage_headers, stage_rows),
        "c2Aggregate": csv_table_specification(
            c2_aggregate_headers, c2_aggregate_rows
        ),
        "hpaDisplay": hpa_display_specification(hpa_rows),
        "hpaConfiguration": hpa_configuration_specification(hpa_rows),
        "hpaSeries": load_hpa_time_series(),
        "comparison": comparison_specification(
            c1_summary_rows,
            c2_summary_rows,
            c1_aggregate_rows,
            c2_aggregate_rows,
        ),
        "methodologyRows": methodology_rows(),
    }


def generate_workbook() -> str:
    node = shutil.which("node")
    if node is None:
        raise SpreadsheetGenerationError("Node.js nao encontrado")
    node_modules = artifact_tool_node_modules()
    specification = build_specification()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="c2-spreadsheet-") as temporary:
        temporary_path = Path(temporary)
        create_node_modules_link(temporary_path / "node_modules", node_modules)
        specification_path = temporary_path / "workbook-specification.json"
        temporary_output = temporary_path / OUTPUT_PATH.name
        preview_directory = temporary_path / "previews"
        specification_path.write_text(
            json.dumps(
                specification,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )

        environment = os.environ.copy()
        environment["C2_WORKBOOK_SPECIFICATION"] = str(specification_path)
        environment["C2_WORKBOOK_OUTPUT"] = str(temporary_output)
        environment["C2_WORKBOOK_PREVIEW_DIRECTORY"] = str(preview_directory)
        run_artifact_tool_script(
            node,
            WORKBOOK_BUILDER_JS,
            temporary_path,
            environment,
            "gerar a planilha do C2",
        )
        if not temporary_output.is_file():
            raise SpreadsheetGenerationError("o gerador nao criou o XLSX")
        normalize_workbook_package(temporary_output)
        validation_output = run_artifact_tool_script(
            node,
            WORKBOOK_VALIDATOR_JS,
            temporary_path,
            environment,
            "validar e renderizar a planilha do C2",
        )
        previews = sorted(preview_directory.glob("sheet-*.png"))
        if len(previews) != len(EXPECTED_SHEETS):
            raise SpreadsheetGenerationError(
                "nem todas as abas foram renderizadas durante a validacao"
            )
        os.replace(temporary_output, OUTPUT_PATH)
    return validation_output


def main() -> int:
    try:
        validation_output = generate_workbook()
    except SpreadsheetGenerationError as exc:
        print(f"Erro ao gerar planilha do C2: {exc}", file=sys.stderr)
        return 1

    print(f"Planilha C2 gerada: {relative_path(OUTPUT_PATH)}")
    print("Abas: " + ", ".join(EXPECTED_SHEETS))
    if validation_output:
        print(validation_output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
