#!/usr/bin/env bash
set -Eeuo pipefail

NAMESPACE="flash-sale"
EXPECTED_CONTEXT="docker-desktop"
RESULTS_ROOT="results/experiments/c1"
LOAD_TEST_SCRIPT="load-tests/kubernetes_checkout.js"
COLLECTOR_SCRIPT="scripts/collect-k8s-experiment-metrics.py"
DB_EXPORTER_SCRIPT="export_k8s_experiment_db_summary.py"
K6_IMAGE="grafana/k6@sha256:632ddbc81a4a9fdc9e597da91ab1d8fcf1916dd988b43b4a4559d2f8d8e73d47"
BASE_URL="http://api:8000"

PRE_COLLECTION_SECONDS=60
COLLECTION_INTERVAL_SECONDS=5
DRAIN_OBJECTIVE_SECONDS=180
DRAIN_MAX_SECONDS=600
COOLDOWN_SECONDS=60
K6_WAIT_SECONDS=240

START_RATE=1
STAGE_1_RATE=20
STAGE_1_DURATION="20s"
STAGE_2_RATE=40
STAGE_2_DURATION="30s"
STAGE_3_RATE=60
STAGE_3_DURATION="30s"
STAGE_4_RATE=0
STAGE_4_DURATION="10s"
PRE_ALLOCATED_VUS=100
MAX_VUS=300
GRACEFUL_STOP="30s"
REQUEST_TIMEOUT="60s"
SCENARIO="c1"

K6_CPU_REQUEST="500m"
K6_CPU_LIMIT="2"
K6_MEMORY_REQUEST="256Mi"
K6_MEMORY_LIMIT="1Gi"

RABBITMQ_LOCAL_PORT="${RABBITMQ_LOCAL_PORT:-25672}"
PROMETHEUS_LOCAL_PORT="${PROMETHEUS_LOCAL_PORT:-29090}"
JAEGER_LOCAL_PORT="${JAEGER_LOCAL_PORT:-26686}"

RUN_TYPE=""
RUN_ID=""
RESULT_DIR=""
TEMP_DIR=""
STOP_FILE=""
CURRENT_STAGE="inicializacao"
LAST_ERROR=""
RESULT_CREATED=0
FINALIZED=0
PREFLIGHT_OK=false
COLLECTOR_PID=""
COLLECTOR_EXIT_CODE=""
K6_JOB_NAME=""
K6_CONFIGMAP_NAME=""
K6_POD_NAME=""
K6_ARTIFACTS_COPIED=false
K6_EXIT_CODE=""
K6_VERSION=""
EXECUTION_STATUS="not_started"
EXPORTS_OK=false
REQUIRED_FILES_OK=false
NODE_REMAINED_READY=false
NO_POD_RESTARTS=false
COLLECTOR_OK=false
DRAIN_COMPLETED=false
DRAIN_OBJECTIVE_MET=false
DRAIN_DURATION_SECONDS=""
FINAL_MESSAGES_READY=""
FINAL_MESSAGES_UNACKNOWLEDGED=""
FINAL_PENDING_ORDERS=""
FINAL_PROCESSING_ORDERS=""
TRACE_COUNT=""

STARTED_AT=""
FINISHED_AT=""
COLLECTION_STARTED_AT=""
COLLECTION_FINISHED_AT=""
K6_STARTED_AT=""
K6_FINISHED_AT=""
DRAIN_STARTED_AT=""
DRAIN_FINISHED_AT=""

GIT_COMMIT=""
GIT_DIRTY=""
DOCKER_VERSION=""
KUBERNETES_CLIENT_VERSION=""
KUBERNETES_SERVER_VERSION=""
API_IMAGE=""
WORKER_IMAGE=""
INITIAL_API_REPLICAS=""
INITIAL_WORKER_REPLICAS=""
TRACE_SAMPLE_RATIO=""
RABBITMQ_USERNAME=""
RABBITMQ_PASSWORD=""

declare -a PORT_FORWARD_PIDS=()
declare -a INVALID_REASONS=()

usage() {
  cat <<'EOF'
Uso:
  bash scripts/run-k8s-experiment.sh --type validation --id c1-validation-001

Opcoes obrigatorias:
  --type validation|exploratory|official
  --id <identificador-unico>

Opcoes de carga:
  --base-url <url>
  --scenario <nome>
  --start-rate <taxa>
  --stage-1-rate <taxa>       --stage-1-duration <duracao>
  --stage-2-rate <taxa>       --stage-2-duration <duracao>
  --stage-3-rate <taxa>       --stage-3-duration <duracao>
  --stage-4-rate <taxa>       --stage-4-duration <duracao>
  --pre-allocated-vus <valor>
  --max-vus <valor>
  --graceful-stop <duracao>
  --request-timeout <duracao>
EOF
}

iso_utc() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

epoch_utc() {
  date -u +"%s"
}

print_step() {
  CURRENT_STAGE="$1"
  printf '\n==> %s\n' "$CURRENT_STAGE"
}

print_action() {
  printf '  -> %s\n' "$1"
}

add_invalid_reason() {
  INVALID_REASONS+=("$1")
}

die() {
  LAST_ERROR="$*"
  printf 'ERRO na etapa "%s": %s\n' "$CURRENT_STAGE" "$LAST_ERROR" >&2
  exit 1
}

handle_error() {
  local exit_code="$1"
  local line_number="$2"
  local command="$3"

  LAST_ERROR="linha $line_number: $command"
  printf '\nERRO na etapa "%s" (linha %s).\n' \
    "$CURRENT_STAGE" "$line_number" >&2
  printf 'Comando que falhou: %s\n' "$command" >&2
  exit "$exit_code"
}

trap 'handle_error "$?" "$LINENO" "$BASH_COMMAND"' ERR

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

require_command() {
  local command_name="$1"
  command -v "$command_name" >/dev/null 2>&1 || \
    die "comando '$command_name' nao encontrado no PATH"
}

strip_carriage_return() {
  tr -d '\r'
}

json_field_from_stdin() {
  local field="$1"
  python -c \
    'import json,sys; print(json.load(sys.stdin)[sys.argv[1]])' \
    "$field"
}

json_file_field() {
  local file="$1"
  local field="$2"
  python -c \
    'import json,sys; value=json.load(open(sys.argv[1], encoding="utf-8"));
for part in sys.argv[2].split("."):
    value=value[part]
print(str(value).lower() if isinstance(value, bool) else value)' \
    "$file" "$field"
}

environment_value() {
  local deployment="$1"
  local variable_name="$2"
  kubectl exec "deployment/$deployment" -n "$NAMESPACE" -- \
    printenv "$variable_name" | strip_carriage_return
}

assert_environment_value() {
  local deployment="$1"
  local variable_name="$2"
  local expected_value="$3"
  local actual_value

  actual_value="$(environment_value "$deployment" "$variable_name")"
  [[ "$actual_value" == "$expected_value" ]] || \
    die "deployment/$deployment: $variable_name='$actual_value'; esperado '$expected_value'"
}

secret_value() {
  local secret_json="$1"
  local key="$2"
  printf '%s' "$secret_json" | python -c \
    'import base64,json,sys; print(base64.b64decode(json.load(sys.stdin)["data"][sys.argv[1]]).decode())' \
    "$key"
}

start_port_forward() {
  local service="$1"
  local local_port="$2"
  local remote_port="$3"
  local log_file="$TEMP_DIR/port-forward-$service.log"

  kubectl port-forward "service/$service" \
    -n "$NAMESPACE" \
    "$local_port:$remote_port" >"$log_file" 2>&1 &
  PORT_FORWARD_PIDS+=("$!")
}

stop_port_forwards() {
  local pid

  for pid in "${PORT_FORWARD_PIDS[@]}"; do
    if kill -0 "$pid" >/dev/null 2>&1; then
      kill "$pid" >/dev/null 2>&1 || true
      wait "$pid" 2>/dev/null || true
    fi
  done
  PORT_FORWARD_PIDS=()
}

rabbitmq_snapshot() {
  RABBITMQ_USERNAME="$RABBITMQ_USERNAME" \
  RABBITMQ_PASSWORD="$RABBITMQ_PASSWORD" \
    python "$COLLECTOR_SCRIPT" rabbitmq-snapshot \
      --url "http://127.0.0.1:$RABBITMQ_LOCAL_PORT/api/queues/%2F/checkout_requests"
}

wait_for_rabbitmq() {
  local attempt

  for attempt in $(seq 1 60); do
    if rabbitmq_snapshot >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done

  cat "$TEMP_DIR/port-forward-rabbitmq.log" >&2 || true
  die "port-forward do RabbitMQ nao ficou disponivel"
}

wait_for_http() {
  local name="$1"
  local url="$2"
  local log_file="$3"
  local attempt

  for attempt in $(seq 1 60); do
    if python -c \
      'import sys,urllib.request; urllib.request.urlopen(sys.argv[1], timeout=2).read()' \
      "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done

  cat "$log_file" >&2 || true
  die "$name nao ficou disponivel pelo port-forward"
}

wait_for_metrics_api() {
  local attempt
  local endpoint="/apis/metrics.k8s.io/v1beta1/namespaces/$NAMESPACE/pods"

  for attempt in $(seq 1 30); do
    if MSYS_NO_PATHCONV=1 kubectl get --raw "$endpoint" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done

  die "Metrics API de Pods nao ficou disponivel apos 60 segundos"
}

stop_collector() {
  local exit_code

  if [[ -z "$COLLECTOR_PID" ]]; then
    return 0
  fi

  touch "$STOP_FILE"
  set +e
  wait "$COLLECTOR_PID"
  exit_code=$?
  set -e
  COLLECTOR_PID=""
  COLLECTOR_EXIT_CODE="$exit_code"

  if [[ "$exit_code" == "0" ]]; then
    COLLECTOR_OK=true
  else
    COLLECTOR_OK=false
    add_invalid_reason "coletor terminou com codigo $exit_code"
  fi
}

delete_k6_resources_if_copied() {
  if [[ "$K6_ARTIFACTS_COPIED" != "true" ]]; then
    return 0
  fi

  if [[ -n "$K6_JOB_NAME" ]]; then
    kubectl delete "job/$K6_JOB_NAME" \
      -n "$NAMESPACE" \
      --ignore-not-found \
      --wait=true >/dev/null 2>&1 || true
  fi
  if [[ -n "$K6_CONFIGMAP_NAME" ]]; then
    kubectl delete "configmap/$K6_CONFIGMAP_NAME" \
      -n "$NAMESPACE" \
      --ignore-not-found >/dev/null 2>&1 || true
  fi
}

invalid_reasons_text() {
  if ((${#INVALID_REASONS[@]} == 0)); then
    return 0
  fi
  printf '%s\n' "${INVALID_REASONS[@]}"
}

write_metadata() {
  local status="$1"
  local finished_at="$2"
  local reasons

  reasons="$(invalid_reasons_text)"

  METADATA_FILE="$RESULT_DIR/run-metadata.json" \
  RUN_TYPE_VALUE="$RUN_TYPE" \
  RUN_ID_VALUE="$RUN_ID" \
  GIT_COMMIT_VALUE="$GIT_COMMIT" \
  GIT_DIRTY_VALUE="$GIT_DIRTY" \
  API_IMAGE_VALUE="$API_IMAGE" \
  WORKER_IMAGE_VALUE="$WORKER_IMAGE" \
  DOCKER_VERSION_VALUE="$DOCKER_VERSION" \
  KUBERNETES_CLIENT_VERSION_VALUE="$KUBERNETES_CLIENT_VERSION" \
  KUBERNETES_SERVER_VERSION_VALUE="$KUBERNETES_SERVER_VERSION" \
  K6_VERSION_VALUE="$K6_VERSION" \
  STARTED_AT_VALUE="$STARTED_AT" \
  FINISHED_AT_VALUE="$finished_at" \
  COLLECTION_STARTED_AT_VALUE="$COLLECTION_STARTED_AT" \
  COLLECTION_FINISHED_AT_VALUE="$COLLECTION_FINISHED_AT" \
  K6_STARTED_AT_VALUE="$K6_STARTED_AT" \
  K6_FINISHED_AT_VALUE="$K6_FINISHED_AT" \
  DRAIN_STARTED_AT_VALUE="$DRAIN_STARTED_AT" \
  DRAIN_FINISHED_AT_VALUE="$DRAIN_FINISHED_AT" \
  DRAIN_DURATION_SECONDS_VALUE="$DRAIN_DURATION_SECONDS" \
  DRAIN_COMPLETED_VALUE="$DRAIN_COMPLETED" \
  DRAIN_OBJECTIVE_MET_VALUE="$DRAIN_OBJECTIVE_MET" \
  START_RATE_VALUE="$START_RATE" \
  STAGE_1_RATE_VALUE="$STAGE_1_RATE" \
  STAGE_1_DURATION_VALUE="$STAGE_1_DURATION" \
  STAGE_2_RATE_VALUE="$STAGE_2_RATE" \
  STAGE_2_DURATION_VALUE="$STAGE_2_DURATION" \
  STAGE_3_RATE_VALUE="$STAGE_3_RATE" \
  STAGE_3_DURATION_VALUE="$STAGE_3_DURATION" \
  STAGE_4_RATE_VALUE="$STAGE_4_RATE" \
  STAGE_4_DURATION_VALUE="$STAGE_4_DURATION" \
  PRE_ALLOCATED_VUS_VALUE="$PRE_ALLOCATED_VUS" \
  MAX_VUS_VALUE="$MAX_VUS" \
  GRACEFUL_STOP_VALUE="$GRACEFUL_STOP" \
  REQUEST_TIMEOUT_VALUE="$REQUEST_TIMEOUT" \
  SCENARIO_VALUE="$SCENARIO" \
  BASE_URL_VALUE="$BASE_URL" \
  API_REPLICAS_VALUE="$INITIAL_API_REPLICAS" \
  WORKER_REPLICAS_VALUE="$INITIAL_WORKER_REPLICAS" \
  TRACE_SAMPLE_RATIO_VALUE="$TRACE_SAMPLE_RATIO" \
  K6_EXIT_CODE_VALUE="$K6_EXIT_CODE" \
  COLLECTOR_EXIT_CODE_VALUE="$COLLECTOR_EXIT_CODE" \
  EXECUTION_STATUS_VALUE="$status" \
  INVALID_REASONS_VALUE="$reasons" \
  python - <<'PY'
import json
import os


def env(name, default=None):
    return os.environ.get(name, default)


def env_int(name, default=None):
    value = env(name)
    return default if value in (None, "") else int(value)


def env_bool(name, default=False):
    value = env(name)
    return default if value in (None, "") else value.lower() == "true"


metadata = {
    "type": env("RUN_TYPE_VALUE"),
    "id": env("RUN_ID_VALUE"),
    "git_commit": env("GIT_COMMIT_VALUE"),
    "git_dirty": env_bool("GIT_DIRTY_VALUE"),
    "images": {
        "api": env("API_IMAGE_VALUE"),
        "worker": env("WORKER_IMAGE_VALUE"),
    },
    "versions": {
        "docker": env("DOCKER_VERSION_VALUE"),
        "kubernetes_client": env("KUBERNETES_CLIENT_VERSION_VALUE"),
        "kubernetes_server": env("KUBERNETES_SERVER_VERSION_VALUE"),
        "k6": env("K6_VERSION_VALUE"),
    },
    "timestamps": {
        "started_at": env("STARTED_AT_VALUE"),
        "finished_at": env("FINISHED_AT_VALUE"),
        "collection_started_at": env("COLLECTION_STARTED_AT_VALUE"),
        "collection_finished_at": env("COLLECTION_FINISHED_AT_VALUE"),
        "load_started_at": env("K6_STARTED_AT_VALUE"),
        "load_finished_at": env("K6_FINISHED_AT_VALUE"),
        "drain_started_at": env("DRAIN_STARTED_AT_VALUE"),
        "drain_finished_at": env("DRAIN_FINISHED_AT_VALUE"),
    },
    "load": {
        "executor": "ramping-arrival-rate",
        "base_url": env("BASE_URL_VALUE"),
        "scenario": env("SCENARIO_VALUE"),
        "start_rate": env_int("START_RATE_VALUE"),
        "time_unit": "1s",
        "stages": [
            {"target": env_int("STAGE_1_RATE_VALUE"), "duration": env("STAGE_1_DURATION_VALUE")},
            {"target": env_int("STAGE_2_RATE_VALUE"), "duration": env("STAGE_2_DURATION_VALUE")},
            {"target": env_int("STAGE_3_RATE_VALUE"), "duration": env("STAGE_3_DURATION_VALUE")},
            {"target": env_int("STAGE_4_RATE_VALUE"), "duration": env("STAGE_4_DURATION_VALUE")},
        ],
        "pre_allocated_vus": env_int("PRE_ALLOCATED_VUS_VALUE"),
        "max_vus": env_int("MAX_VUS_VALUE"),
        "graceful_stop": env("GRACEFUL_STOP_VALUE"),
        "request_timeout": env("REQUEST_TIMEOUT_VALUE"),
    },
    "k6_resources": {
        "requests": {"cpu": "500m", "memory": "256Mi"},
        "limits": {"cpu": "2", "memory": "1Gi"},
    },
    "initial_replicas": {
        "api": env_int("API_REPLICAS_VALUE"),
        "worker": env_int("WORKER_REPLICAS_VALUE"),
    },
    "trace_sample_ratio": float(env("TRACE_SAMPLE_RATIO_VALUE", "0")),
    "collection_interval_seconds": 5,
    "idle_collection_seconds": 60,
    "drain": {
        "objective_seconds": 180,
        "maximum_observation_seconds": 600,
        "duration_seconds": env_int("DRAIN_DURATION_SECONDS_VALUE"),
        "completed": env_bool("DRAIN_COMPLETED_VALUE"),
        "objective_met": env_bool("DRAIN_OBJECTIVE_MET_VALUE"),
    },
    "cooldown_seconds": 60,
    "k6_exit_code": env_int("K6_EXIT_CODE_VALUE"),
    "collector_exit_code": env_int("COLLECTOR_EXIT_CODE_VALUE"),
    "execution_status": env("EXECUTION_STATUS_VALUE"),
    "invalid_reasons": [
        item for item in env("INVALID_REASONS_VALUE", "").splitlines() if item
    ],
}

with open(env("METADATA_FILE"), "w", encoding="utf-8") as output:
    json.dump(metadata, output, ensure_ascii=False, indent=2)
    output.write("\n")
PY
}

write_checklist() {
  local overall_status="$1"
  local reasons

  reasons="$(invalid_reasons_text)"

  CHECKLIST_FILE="$RESULT_DIR/validity-checklist.md" \
  OVERALL_STATUS_VALUE="$overall_status" \
  PREFLIGHT_OK_VALUE="$PREFLIGHT_OK" \
  NODE_READY_VALUE="$NODE_REMAINED_READY" \
  NO_RESTARTS_VALUE="$NO_POD_RESTARTS" \
  COLLECTOR_OK_VALUE="$COLLECTOR_OK" \
  K6_COPIED_VALUE="$K6_ARTIFACTS_COPIED" \
  K6_EXIT_CODE_VALUE="$K6_EXIT_CODE" \
  DRAIN_COMPLETED_VALUE="$DRAIN_COMPLETED" \
  DRAIN_OBJECTIVE_MET_VALUE="$DRAIN_OBJECTIVE_MET" \
  EXPORTS_OK_VALUE="$EXPORTS_OK" \
  REQUIRED_FILES_OK_VALUE="$REQUIRED_FILES_OK" \
  INVALID_REASONS_VALUE="$reasons" \
  python - <<'PY'
import os


def checked(name):
    return "x" if os.environ.get(name, "false").lower() == "true" else " "


status = os.environ.get("OVERALL_STATUS_VALUE", "INVALID")
items = [
    ("PREFLIGHT_OK_VALUE", "Preflight do C1 aprovado"),
    ("NODE_READY_VALUE", "Node permaneceu Ready durante a coleta"),
    ("NO_RESTARTS_VALUE", "Nenhum Pod reiniciou durante a coleta"),
    ("COLLECTOR_OK_VALUE", "Coletor terminou sem falhas"),
    ("K6_COPIED_VALUE", "Logs e summary do k6 foram copiados antes da remocao do Job"),
    ("DRAIN_COMPLETED_VALUE", "Fila e pedidos ativos drenaram dentro da janela maxima"),
    ("EXPORTS_OK_VALUE", "Banco, Prometheus, logs, traces e Kubernetes foram exportados"),
    ("REQUIRED_FILES_OK_VALUE", "Todos os arquivos obrigatorios estao presentes"),
]

lines = [
    "# Checklist de validade",
    "",
    f"**Resultado: {status}**",
    "",
]
for variable, label in items:
    lines.append(f"- [{checked(variable)}] {label}")

k6_exit = os.environ.get("K6_EXIT_CODE_VALUE", "")
k6_ok = k6_exit == "0"
lines.append(f"- [{'x' if k6_ok else ' '}] k6 terminou com codigo 0 (observado: {k6_exit or 'indisponivel'})")

objective_met = os.environ.get("DRAIN_OBJECTIVE_MET_VALUE", "false").lower() == "true"
lines.extend(
    [
        "",
        "## Observacoes",
        "",
        f"- Objetivo de drenagem de 180 segundos: {'atingido' if objective_met else 'nao atingido'}.",
        "- Desempenho ruim da aplicacao, isoladamente, nao invalida a execucao.",
    ]
)

reasons = [
    item
    for item in os.environ.get("INVALID_REASONS_VALUE", "").splitlines()
    if item
]
if reasons:
    lines.extend(["", "## Motivos de invalidade", ""])
    lines.extend(f"- {reason}" for reason in reasons)

with open(os.environ["CHECKLIST_FILE"], "w", encoding="utf-8") as output:
    output.write("\n".join(lines) + "\n")
PY
}

cleanup() {
  local exit_code=$?

  trap - EXIT ERR INT TERM
  set +e

  stop_collector
  delete_k6_resources_if_copied
  stop_port_forwards

  if [[ "$RESULT_CREATED" == "1" && "$FINALIZED" == "0" ]]; then
    if [[ -n "$LAST_ERROR" ]]; then
      add_invalid_reason "$CURRENT_STAGE: $LAST_ERROR"
    elif [[ "$exit_code" != "0" ]]; then
      add_invalid_reason "executor interrompido com codigo $exit_code"
    fi

    FINISHED_AT="$(iso_utc)"
    EXECUTION_STATUS="invalid"
    write_metadata "$EXECUTION_STATUS" "$FINISHED_AT" || true
    write_checklist "INVALID" || true
  fi

  if [[ -n "$TEMP_DIR" && -d "$TEMP_DIR" ]]; then
    rm -rf -- "$TEMP_DIR"
  fi

  exit "$exit_code"
}

trap cleanup EXIT
trap 'LAST_ERROR="execucao interrompida"; exit 130' INT TERM

parse_arguments() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --type) RUN_TYPE="${2:-}"; shift 2 ;;
      --id) RUN_ID="${2:-}"; shift 2 ;;
      --base-url) BASE_URL="${2:-}"; shift 2 ;;
      --scenario) SCENARIO="${2:-}"; shift 2 ;;
      --start-rate) START_RATE="${2:-}"; shift 2 ;;
      --stage-1-rate) STAGE_1_RATE="${2:-}"; shift 2 ;;
      --stage-1-duration) STAGE_1_DURATION="${2:-}"; shift 2 ;;
      --stage-2-rate) STAGE_2_RATE="${2:-}"; shift 2 ;;
      --stage-2-duration) STAGE_2_DURATION="${2:-}"; shift 2 ;;
      --stage-3-rate) STAGE_3_RATE="${2:-}"; shift 2 ;;
      --stage-3-duration) STAGE_3_DURATION="${2:-}"; shift 2 ;;
      --stage-4-rate) STAGE_4_RATE="${2:-}"; shift 2 ;;
      --stage-4-duration) STAGE_4_DURATION="${2:-}"; shift 2 ;;
      --pre-allocated-vus) PRE_ALLOCATED_VUS="${2:-}"; shift 2 ;;
      --max-vus) MAX_VUS="${2:-}"; shift 2 ;;
      --graceful-stop) GRACEFUL_STOP="${2:-}"; shift 2 ;;
      --request-timeout) REQUEST_TIMEOUT="${2:-}"; shift 2 ;;
      --help) usage; exit 0 ;;
      *) die "argumento desconhecido: $1" ;;
    esac
  done
}

validate_arguments() {
  local value
  local duration

  [[ "$RUN_TYPE" =~ ^(validation|exploratory|official)$ ]] || \
    die "use --type validation, exploratory ou official"
  [[ "$RUN_ID" =~ ^[a-z0-9]([-a-z0-9]*[a-z0-9])?$ ]] || \
    die "--id deve ser um nome DNS em minusculas"
  ((${#RUN_ID} <= 50)) || die "--id deve ter no maximo 50 caracteres"
  [[ "$SCENARIO" =~ ^[a-zA-Z0-9_-]+$ ]] || die "cenario invalido"
  [[ "$BASE_URL" =~ ^https?://[a-zA-Z0-9._:/-]+$ ]] || die "BASE_URL invalida"

  for value in \
    "$START_RATE" "$STAGE_1_RATE" "$STAGE_2_RATE" \
    "$STAGE_3_RATE" "$STAGE_4_RATE" "$PRE_ALLOCATED_VUS" "$MAX_VUS"; do
    [[ "$value" =~ ^[0-9]+$ ]] || die "taxas e VUs devem ser inteiros nao negativos"
  done

  for duration in \
    "$STAGE_1_DURATION" "$STAGE_2_DURATION" "$STAGE_3_DURATION" \
    "$STAGE_4_DURATION" "$GRACEFUL_STOP" "$REQUEST_TIMEOUT"; do
    [[ "$duration" =~ ^[0-9]+(ms|s|m)$ ]] || die "duracao invalida: $duration"
  done

  RESULT_DIR="$RESULTS_ROOT/$RUN_TYPE/$RUN_ID"
  K6_JOB_NAME="k6-$RUN_ID"
  K6_CONFIGMAP_NAME="k6-script-$RUN_ID"

  [[ ! -e "$RESULT_DIR" ]] || die "diretorio de execucao ja existe: $RESULT_DIR"
}

validate_deployments() {
  kubectl get deployments -n "$NAMESPACE" -o json | python -c '
import json,sys
items=json.load(sys.stdin).get("items", [])
if len(items) != 7:
    raise SystemExit(f"esperados 7 Deployments, encontrados {len(items)}")
problems=[]
for item in items:
    name=item["metadata"]["name"]
    desired=item.get("spec", {}).get("replicas", 1)
    available=item.get("status", {}).get("availableReplicas", 0)
    if available != desired:
        problems.append(f"{name}={available}/{desired}")
if problems:
    raise SystemExit("Deployments indisponiveis: " + ", ".join(problems))
'

  kubectl get deployment/api deployment/worker -n "$NAMESPACE" -o json | \
    python -c '
import json,sys
for item in json.load(sys.stdin).get("items", []):
    name=item["metadata"]["name"]
    desired=item.get("spec", {}).get("replicas", 1)
    available=item.get("status", {}).get("availableReplicas", 0)
    if desired != 1 or available != 1:
        raise SystemExit(f"deployment/{name} precisa estar exatamente 1/1")
'
}

validate_node_ready() {
  kubectl get nodes -o json | python -c '
import json,sys
items=json.load(sys.stdin).get("items", [])
ready=[]
for item in items:
    conditions=item.get("status", {}).get("conditions", [])
    if any(c.get("type") == "Ready" and c.get("status") == "True" for c in conditions):
        ready.append(item["metadata"]["name"])
if not ready:
    raise SystemExit("nenhum Node esta Ready")
'
}

run_preflight() {
  local context
  local hpas
  local secret_json
  local queue_json
  local messages_ready
  local messages_unacknowledged
  local consumers

  print_step "Preflight"

  require_command docker
  require_command kubectl
  require_command python

  for file in "$LOAD_TEST_SCRIPT" "$COLLECTOR_SCRIPT" "$DB_EXPORTER_SCRIPT"; do
    [[ -f "$file" ]] || die "arquivo obrigatorio ausente: $file"
  done

  context="$(kubectl config current-context | strip_carriage_return)"
  [[ "$context" == "$EXPECTED_CONTEXT" ]] || \
    die "contexto atual '$context'; esperado '$EXPECTED_CONTEXT'"

  validate_node_ready
  validate_deployments

  hpas="$(kubectl get hpa -n "$NAMESPACE" -o name | strip_carriage_return)"
  [[ -z "$hpas" ]] || die "C1 nao pode possuir HPA: $hpas"

  kubectl exec deployment/api -n "$NAMESPACE" -- python -c \
    "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=10).read()" \
    >/dev/null

  wait_for_metrics_api

  assert_environment_value api DIAGNOSTIC_LOGS 0
  assert_environment_value api OTEL_ENABLED 1
  assert_environment_value api OTEL_TRACE_SAMPLE_RATIO 0.01
  assert_environment_value worker DIAGNOSTIC_LOGS 0
  assert_environment_value worker OTEL_ENABLED 1
  assert_environment_value worker OTEL_TRACE_SAMPLE_RATIO 0.01

  secret_json="$(kubectl get secret flash-sale-secrets -n "$NAMESPACE" -o json)"
  RABBITMQ_USERNAME="$(secret_value "$secret_json" RABBITMQ_DEFAULT_USER)"
  RABBITMQ_PASSWORD="$(secret_value "$secret_json" RABBITMQ_DEFAULT_PASS)"

  start_port_forward rabbitmq "$RABBITMQ_LOCAL_PORT" 15672
  wait_for_rabbitmq

  queue_json="$(rabbitmq_snapshot)"
  messages_ready="$(printf '%s' "$queue_json" | json_field_from_stdin messages_ready)"
  messages_unacknowledged="$(
    printf '%s' "$queue_json" |
      json_field_from_stdin messages_unacknowledged
  )"
  consumers="$(printf '%s' "$queue_json" | json_field_from_stdin consumers)"

  [[ "$messages_ready" == "0" ]] || die "fila possui $messages_ready mensagens prontas"
  [[ "$messages_unacknowledged" == "0" ]] || \
    die "fila possui $messages_unacknowledged mensagens nao confirmadas"
  [[ "$consumers" == "1" ]] || die "fila precisa ter exatamente 1 consumidor; observado $consumers"

  kubectl exec deployment/api -n "$NAMESPACE" -- \
    python export_experiment_db_summary.py --assert-idle >/dev/null

  GIT_COMMIT="$(git rev-parse HEAD)"
  if [[ -n "$(git status --short)" ]]; then
    GIT_DIRTY=true
  else
    GIT_DIRTY=false
  fi
  DOCKER_VERSION="$(docker version --format '{{.Server.Version}}' | strip_carriage_return)"
  KUBERNETES_CLIENT_VERSION="$(
    kubectl version -o json |
      python -c 'import json,sys; print(json.load(sys.stdin)["clientVersion"]["gitVersion"])'
  )"
  KUBERNETES_SERVER_VERSION="$(
    kubectl version -o json |
      python -c 'import json,sys; print(json.load(sys.stdin)["serverVersion"]["gitVersion"])'
  )"
  API_IMAGE="$(
    kubectl get deployment/api -n "$NAMESPACE" \
      -o jsonpath='{.spec.template.spec.containers[0].image}' |
      strip_carriage_return
  )"
  WORKER_IMAGE="$(
    kubectl get deployment/worker -n "$NAMESPACE" \
      -o jsonpath='{.spec.template.spec.containers[0].image}' |
      strip_carriage_return
  )"
  INITIAL_API_REPLICAS="$(
    kubectl get deployment/api -n "$NAMESPACE" -o jsonpath='{.spec.replicas}' |
      strip_carriage_return
  )"
  INITIAL_WORKER_REPLICAS="$(
    kubectl get deployment/worker -n "$NAMESPACE" -o jsonpath='{.spec.replicas}' |
      strip_carriage_return
  )"
  TRACE_SAMPLE_RATIO="$(environment_value api OTEL_TRACE_SAMPLE_RATIO)"

  PREFLIGHT_OK=true
  print_action "Preflight aprovado"
}

prepare_data() {
  print_step "Dados iniciais"
  kubectl exec deployment/api -n "$NAMESPACE" -- python seed_products.py
  kubectl exec deployment/api -n "$NAMESPACE" -- python reset_demo_data.py
  kubectl exec deployment/api -n "$NAMESPACE" -- \
    python export_experiment_db_summary.py --assert-idle --assert-stocks >/dev/null
  print_action "Banco zerado e estoques em 10000"
}

create_result_structure() {
  print_step "Estrutura de resultados"

  mkdir -p \
    "$RESULT_DIR/k6" \
    "$RESULT_DIR/metrics" \
    "$RESULT_DIR/logs" \
    "$RESULT_DIR/traces" \
    "$RESULT_DIR/kubernetes" \
    "$RESULT_DIR/rabbitmq" \
    "$RESULT_DIR/database"

  RESULT_CREATED=1
  STOP_FILE="$TEMP_DIR/collector-stop"
  STARTED_AT="$(iso_utc)"
  EXECUTION_STATUS="running"
  write_metadata "$EXECUTION_STATUS" ""

  python "$COLLECTOR_SCRIPT" snapshot-kubernetes \
    --namespace "$NAMESPACE" \
    --output "$RESULT_DIR/kubernetes/before.json"
}

start_export_port_forwards() {
  print_step "Port-forwards de evidencias"

  start_port_forward prometheus "$PROMETHEUS_LOCAL_PORT" 9090
  start_port_forward jaeger "$JAEGER_LOCAL_PORT" 16686

  wait_for_http \
    "Prometheus" \
    "http://127.0.0.1:$PROMETHEUS_LOCAL_PORT/-/ready" \
    "$TEMP_DIR/port-forward-prometheus.log"
  wait_for_http \
    "Jaeger" \
    "http://127.0.0.1:$JAEGER_LOCAL_PORT/api/services" \
    "$TEMP_DIR/port-forward-jaeger.log"
}

start_collector() {
  print_step "Coleta ociosa"
  COLLECTION_STARTED_AT="$(iso_utc)"

  RABBITMQ_USERNAME="$RABBITMQ_USERNAME" \
  RABBITMQ_PASSWORD="$RABBITMQ_PASSWORD" \
    python "$COLLECTOR_SCRIPT" collect \
      --namespace "$NAMESPACE" \
      --resources-output "$RESULT_DIR/metrics/kubernetes-resources.csv" \
      --pods-output "$RESULT_DIR/metrics/kubernetes-pods.csv" \
      --queue-output "$RESULT_DIR/rabbitmq/queue.csv" \
      --errors-output "$RESULT_DIR/metrics/collector-errors.jsonl" \
      --stop-file "$STOP_FILE" \
      --rabbitmq-url "http://127.0.0.1:$RABBITMQ_LOCAL_PORT/api/queues/%2F/checkout_requests" \
      --interval "$COLLECTION_INTERVAL_SECONDS" \
      >"$RESULT_DIR/metrics/collector.log" 2>&1 &
  COLLECTOR_PID=$!

  print_action "Coletando $PRE_COLLECTION_SECONDS segundos antes da carga"
  sleep "$PRE_COLLECTION_SECONDS"

  if ! kill -0 "$COLLECTOR_PID" >/dev/null 2>&1; then
    wait "$COLLECTOR_PID" || true
    COLLECTOR_PID=""
    COLLECTOR_OK=false
    die "coletor terminou durante o periodo ocioso"
  fi
}

write_k6_job_manifest() {
  cat >"$RESULT_DIR/kubernetes/k6-job.yaml" <<EOF
apiVersion: batch/v1
kind: Job
metadata:
  name: $K6_JOB_NAME
  namespace: $NAMESPACE
  labels:
    app: k6
    run-id: $RUN_ID
spec:
  backoffLimit: 0
  template:
    metadata:
      labels:
        app: k6
        run-id: $RUN_ID
    spec:
      restartPolicy: Never
      containers:
        - name: k6
          image: $K6_IMAGE
          command:
            - /bin/sh
            - -c
          args:
            - |
              set +e
              k6 version > /results/k6-version.txt 2>&1
              k6 run --summary-export=/results/k6-summary.json /scripts/kubernetes_checkout.js
              exit_code=\$?
              printf '%s\\n' "\$exit_code" > /results/k6-exit-code
              touch /results/k6-finished
              while :; do sleep 3600; done
          env:
            - name: BASE_URL
              value: "$BASE_URL"
            - name: RUN_ID
              value: "$RUN_ID"
            - name: SCENARIO
              value: "$SCENARIO"
            - name: START_RATE
              value: "$START_RATE"
            - name: STAGE_1_RATE
              value: "$STAGE_1_RATE"
            - name: STAGE_1_DURATION
              value: "$STAGE_1_DURATION"
            - name: STAGE_2_RATE
              value: "$STAGE_2_RATE"
            - name: STAGE_2_DURATION
              value: "$STAGE_2_DURATION"
            - name: STAGE_3_RATE
              value: "$STAGE_3_RATE"
            - name: STAGE_3_DURATION
              value: "$STAGE_3_DURATION"
            - name: STAGE_4_RATE
              value: "$STAGE_4_RATE"
            - name: STAGE_4_DURATION
              value: "$STAGE_4_DURATION"
            - name: PRE_ALLOCATED_VUS
              value: "$PRE_ALLOCATED_VUS"
            - name: MAX_VUS
              value: "$MAX_VUS"
            - name: GRACEFUL_STOP
              value: "$GRACEFUL_STOP"
            - name: REQUEST_TIMEOUT
              value: "$REQUEST_TIMEOUT"
          resources:
            requests:
              cpu: $K6_CPU_REQUEST
              memory: $K6_MEMORY_REQUEST
            limits:
              cpu: "$K6_CPU_LIMIT"
              memory: $K6_MEMORY_LIMIT
          volumeMounts:
            - name: script
              mountPath: /scripts
              readOnly: true
            - name: results
              mountPath: /results
      volumes:
        - name: script
          configMap:
            name: $K6_CONFIGMAP_NAME
        - name: results
          emptyDir: {}
EOF
}

wait_for_k6_pod() {
  local deadline=$((SECONDS + 120))

  while true; do
    K6_POD_NAME="$(
      kubectl get pods \
        -n "$NAMESPACE" \
        -l "job-name=$K6_JOB_NAME" \
        -o jsonpath='{.items[0].metadata.name}' 2>/dev/null |
        strip_carriage_return
    )"
    if [[ -n "$K6_POD_NAME" ]]; then
      return 0
    fi
    ((SECONDS < deadline)) || die "Pod do k6 nao foi criado"
    sleep 1
  done
}

wait_for_k6_finish() {
  local deadline=$((SECONDS + K6_WAIT_SECONDS))
  local phase

  while true; do
    if MSYS_NO_PATHCONV=1 kubectl exec "$K6_POD_NAME" \
      -n "$NAMESPACE" -- test -f /results/k6-finished >/dev/null 2>&1; then
      K6_FINISHED_AT="$(iso_utc)"
      return 0
    fi

    phase="$(
      kubectl get "pod/$K6_POD_NAME" -n "$NAMESPACE" \
        -o jsonpath='{.status.phase}' |
        strip_carriage_return
    )"
    [[ "$phase" != "Failed" ]] || die "Pod do k6 falhou antes de salvar o summary"
    ((SECONDS < deadline)) || die "timeout aguardando conclusao do k6"
    sleep 2
  done
}

copy_k6_artifacts() {
  kubectl logs "$K6_POD_NAME" -n "$NAMESPACE" >"$RESULT_DIR/k6/k6.log" 2>&1
  MSYS_NO_PATHCONV=1 kubectl exec "$K6_POD_NAME" -n "$NAMESPACE" -- \
    cat /results/k6-summary.json >"$RESULT_DIR/k6/k6-summary.json"
  K6_EXIT_CODE="$(
    MSYS_NO_PATHCONV=1 kubectl exec "$K6_POD_NAME" -n "$NAMESPACE" -- \
      cat /results/k6-exit-code |
      strip_carriage_return
  )"
  K6_VERSION="$(
    MSYS_NO_PATHCONV=1 kubectl exec "$K6_POD_NAME" -n "$NAMESPACE" -- \
      cat /results/k6-version.txt |
      strip_carriage_return
  )"

  [[ "$K6_EXIT_CODE" =~ ^[0-9]+$ ]] || die "codigo de saida do k6 invalido"
  python -c 'import json,sys; json.load(open(sys.argv[1], encoding="utf-8"))' \
    "$RESULT_DIR/k6/k6-summary.json"

  K6_ARTIFACTS_COPIED=true
  delete_k6_resources_if_copied
}

run_k6_job() {
  print_step "Carga k6 no Kubernetes"

  kubectl create configmap "$K6_CONFIGMAP_NAME" \
    -n "$NAMESPACE" \
    --from-file="kubernetes_checkout.js=$LOAD_TEST_SCRIPT" \
    --dry-run=client \
    -o yaml | kubectl apply -f -

  write_k6_job_manifest
  K6_STARTED_AT="$(iso_utc)"
  kubectl apply -f "$RESULT_DIR/kubernetes/k6-job.yaml"
  wait_for_k6_pod
  print_action "Job executando no Pod $K6_POD_NAME"
  wait_for_k6_finish
  copy_k6_artifacts

  if [[ "$K6_EXIT_CODE" != "0" ]]; then
    add_invalid_reason "k6 terminou com codigo $K6_EXIT_CODE"
  fi
}

database_state() {
  kubectl exec deployment/api -n "$NAMESPACE" -- \
    python export_experiment_db_summary.py
}

write_drain_summary() {
  DRAIN_FILE="$RESULT_DIR/rabbitmq/drain-summary.json" \
  DRAIN_STARTED_AT_VALUE="$DRAIN_STARTED_AT" \
  DRAIN_FINISHED_AT_VALUE="$DRAIN_FINISHED_AT" \
  DRAIN_DURATION_SECONDS_VALUE="$DRAIN_DURATION_SECONDS" \
  DRAIN_COMPLETED_VALUE="$DRAIN_COMPLETED" \
  DRAIN_OBJECTIVE_MET_VALUE="$DRAIN_OBJECTIVE_MET" \
  FINAL_READY_VALUE="$FINAL_MESSAGES_READY" \
  FINAL_UNACK_VALUE="$FINAL_MESSAGES_UNACKNOWLEDGED" \
  FINAL_PENDING_VALUE="$FINAL_PENDING_ORDERS" \
  FINAL_PROCESSING_VALUE="$FINAL_PROCESSING_ORDERS" \
  python - <<'PY'
import json
import os

data = {
    "drain_started_at": os.environ["DRAIN_STARTED_AT_VALUE"],
    "drain_finished_at": os.environ["DRAIN_FINISHED_AT_VALUE"],
    "drain_duration_seconds": int(os.environ["DRAIN_DURATION_SECONDS_VALUE"]),
    "completed": os.environ["DRAIN_COMPLETED_VALUE"] == "true",
    "objective_seconds": 180,
    "objective_met": os.environ["DRAIN_OBJECTIVE_MET_VALUE"] == "true",
    "maximum_observation_seconds": 600,
    "final_messages_ready": int(os.environ["FINAL_READY_VALUE"]),
    "final_messages_unacknowledged": int(os.environ["FINAL_UNACK_VALUE"]),
    "final_pending_orders": int(os.environ["FINAL_PENDING_VALUE"]),
    "final_processing_orders": int(os.environ["FINAL_PROCESSING_VALUE"]),
}
with open(os.environ["DRAIN_FILE"], "w", encoding="utf-8") as output:
    json.dump(data, output, ensure_ascii=False, indent=2)
    output.write("\n")
PY
}

drain_workload() {
  local drain_started_epoch
  local now_epoch
  local elapsed
  local queue_json
  local db_json
  local objective_reported=false

  print_step "Drenagem"
  DRAIN_STARTED_AT="$(iso_utc)"
  drain_started_epoch="$(epoch_utc)"

  while true; do
    queue_json="$(rabbitmq_snapshot)"
    db_json="$(database_state)"

    FINAL_MESSAGES_READY="$(
      printf '%s' "$queue_json" | json_field_from_stdin messages_ready
    )"
    FINAL_MESSAGES_UNACKNOWLEDGED="$(
      printf '%s' "$queue_json" |
        json_field_from_stdin messages_unacknowledged
    )"
    FINAL_PENDING_ORDERS="$(
      printf '%s' "$db_json" |
        python -c 'import json,sys; print(json.load(sys.stdin)["orders_by_status"]["PENDING"])'
    )"
    FINAL_PROCESSING_ORDERS="$(
      printf '%s' "$db_json" |
        python -c 'import json,sys; print(json.load(sys.stdin)["orders_by_status"]["PROCESSING"])'
    )"

    now_epoch="$(epoch_utc)"
    elapsed=$((now_epoch - drain_started_epoch))

    if [[ "$FINAL_MESSAGES_READY" == "0" && \
      "$FINAL_MESSAGES_UNACKNOWLEDGED" == "0" && \
      "$FINAL_PENDING_ORDERS" == "0" && \
      "$FINAL_PROCESSING_ORDERS" == "0" ]]; then
      DRAIN_COMPLETED=true
      DRAIN_DURATION_SECONDS="$elapsed"
      if ((elapsed <= DRAIN_OBJECTIVE_SECONDS)); then
        DRAIN_OBJECTIVE_MET=true
      fi
      break
    fi

    if ((elapsed >= DRAIN_OBJECTIVE_SECONDS)) && \
      [[ "$objective_reported" == "false" ]]; then
      print_action "Objetivo de 180s excedido; observacao continua ate 600s"
      objective_reported=true
    fi

    if ((elapsed >= DRAIN_MAX_SECONDS)); then
      DRAIN_COMPLETED=false
      DRAIN_DURATION_SECONDS="$elapsed"
      add_invalid_reason "drenagem nao concluiu em 600 segundos"
      break
    fi

    sleep 5
  done

  DRAIN_FINISHED_AT="$(iso_utc)"
  write_drain_summary
  print_action "Drenagem concluida=$DRAIN_COMPLETED em ${DRAIN_DURATION_SECONDS}s"
}

export_evidence() {
  print_step "Exportacao de evidencias"

  stop_collector
  COLLECTION_FINISHED_AT="$(iso_utc)"

  MSYS_NO_PATHCONV=1 kubectl exec -i deployment/api -n "$NAMESPACE" -- \
    python - <"$DB_EXPORTER_SCRIPT" >"$RESULT_DIR/database/db-summary.json"

  python "$COLLECTOR_SCRIPT" export-prometheus \
    --output "$RESULT_DIR/metrics/prometheus.csv" \
    --start "$COLLECTION_STARTED_AT" \
    --end "$COLLECTION_FINISHED_AT" \
    --step "${COLLECTION_INTERVAL_SECONDS}s" \
    --prometheus-url "http://127.0.0.1:$PROMETHEUS_LOCAL_PORT"

  python "$COLLECTOR_SCRIPT" export-jaeger \
    --output "$RESULT_DIR/traces/jaeger-traces.json" \
    --start "$COLLECTION_STARTED_AT" \
    --end "$COLLECTION_FINISHED_AT" \
    --service flash-sale-api \
    --jaeger-url "http://127.0.0.1:$JAEGER_LOCAL_PORT"

  kubectl logs deployment/api -n "$NAMESPACE" \
    --since-time="$COLLECTION_STARTED_AT" \
    --timestamps >"$RESULT_DIR/logs/api.log" 2>&1
  kubectl logs deployment/worker -n "$NAMESPACE" \
    --since-time="$COLLECTION_STARTED_AT" \
    --timestamps >"$RESULT_DIR/logs/worker.log" 2>&1

  python "$COLLECTOR_SCRIPT" snapshot-kubernetes \
    --namespace "$NAMESPACE" \
    --output "$RESULT_DIR/kubernetes/after.json"
  kubectl get events -n "$NAMESPACE" \
    --sort-by=.metadata.creationTimestamp >"$RESULT_DIR/kubernetes/events.txt"

  TRACE_COUNT="$(
    python -c \
      'import json,sys; print(len(json.load(open(sys.argv[1], encoding="utf-8")).get("data", [])))' \
      "$RESULT_DIR/traces/jaeger-traces.json"
  )"
  EXPORTS_OK=true
}

evaluate_collection() {
  python - \
    "$RESULT_DIR/metrics/kubernetes-resources.csv" \
    "$RESULT_DIR/metrics/kubernetes-pods.csv" \
    "$RESULT_DIR/rabbitmq/queue.csv" \
    "$RESULT_DIR/metrics/collection-summary.json" <<'PY'
import csv
import json
import sys

resources_file, pods_file, queue_file, output_file = sys.argv[1:]

resource_peaks = {}
with open(resources_file, newline="", encoding="utf-8") as source:
    for row in csv.DictReader(source):
        container = row["container"]
        peak = resource_peaks.setdefault(
            container,
            {"max_cpu_cores": 0.0, "max_memory_mib": 0.0},
        )
        peak["max_cpu_cores"] = max(peak["max_cpu_cores"], float(row["cpu_cores"]))
        peak["max_memory_mib"] = max(peak["max_memory_mib"], float(row["memory_mib"]))

node_remained_ready = True
no_pod_restarts = True
api_pod_counts = []
with open(pods_file, newline="", encoding="utf-8") as source:
    for row in csv.DictReader(source):
        node_remained_ready = node_remained_ready and row["all_nodes_ready"].lower() == "true"
        no_pod_restarts = no_pod_restarts and int(row["restart_count"]) == 0
        api_pod_counts.append(int(row["api_pod_count"]))

queue_rows = []
with open(queue_file, newline="", encoding="utf-8") as source:
    for row in csv.DictReader(source):
        queue_rows.append({key: int(value) if key != "timestamp" else value for key, value in row.items()})

summary = {
    "node_remained_ready": node_remained_ready,
    "no_pod_restarts": no_pod_restarts,
    "api_pod_count_min": min(api_pod_counts) if api_pod_counts else None,
    "api_pod_count_max": max(api_pod_counts) if api_pod_counts else None,
    "resource_peaks": resource_peaks,
    "queue": {
        "sample_count": len(queue_rows),
        "max_messages_ready": max((row["messages_ready"] for row in queue_rows), default=0),
        "max_messages_unacknowledged": max((row["messages_unacknowledged"] for row in queue_rows), default=0),
        "max_messages": max((row["messages"] for row in queue_rows), default=0),
        "final": queue_rows[-1] if queue_rows else None,
    },
}

with open(output_file, "w", encoding="utf-8") as output:
    json.dump(summary, output, ensure_ascii=False, indent=2)
    output.write("\n")
PY

  NODE_REMAINED_READY="$(
    json_file_field "$RESULT_DIR/metrics/collection-summary.json" node_remained_ready
  )"
  NO_POD_RESTARTS="$(
    json_file_field "$RESULT_DIR/metrics/collection-summary.json" no_pod_restarts
  )"

  [[ "$NODE_REMAINED_READY" == "true" ]] || \
    add_invalid_reason "Node ficou NotReady durante a coleta"
  [[ "$NO_POD_RESTARTS" == "true" ]] || \
    add_invalid_reason "ao menos um Pod reiniciou durante a coleta"

  if [[ -s "$RESULT_DIR/metrics/collector-errors.jsonl" ]]; then
    COLLECTOR_OK=false
    add_invalid_reason "collector-errors.jsonl possui falhas"
  fi
}

validate_required_files() {
  local required_file
  local missing=false
  local -a required_files=(
    "run-metadata.json"
    "k6/k6-summary.json"
    "k6/k6.log"
    "metrics/kubernetes-resources.csv"
    "metrics/kubernetes-pods.csv"
    "metrics/prometheus.csv"
    "logs/api.log"
    "logs/worker.log"
    "traces/jaeger-traces.json"
    "kubernetes/before.json"
    "kubernetes/after.json"
    "kubernetes/events.txt"
    "rabbitmq/queue.csv"
    "rabbitmq/drain-summary.json"
    "database/db-summary.json"
  )

  for required_file in "${required_files[@]}"; do
    if [[ ! -f "$RESULT_DIR/$required_file" ]]; then
      add_invalid_reason "arquivo obrigatorio ausente: $required_file"
      missing=true
    fi
  done

  if [[ "$missing" == "false" ]]; then
    REQUIRED_FILES_OK=true
  else
    REQUIRED_FILES_OK=false
  fi
}

finalize_execution() {
  local overall_status

  print_step "Cooldown"
  print_action "Aguardando $COOLDOWN_SECONDS segundos"
  sleep "$COOLDOWN_SECONDS"

  evaluate_collection
  validate_required_files

  if [[ "$K6_EXIT_CODE" != "0" ]]; then
    add_invalid_reason "k6 nao terminou com codigo zero"
  fi
  if [[ "$K6_ARTIFACTS_COPIED" != "true" ]]; then
    add_invalid_reason "artefatos do k6 nao foram copiados"
  fi
  if [[ "$DRAIN_COMPLETED" != "true" ]]; then
    add_invalid_reason "drenagem incompleta"
  fi
  if [[ "$EXPORTS_OK" != "true" ]]; then
    add_invalid_reason "exportacao de evidencias incompleta"
  fi
  if [[ "$COLLECTOR_OK" != "true" ]]; then
    add_invalid_reason "coleta de metricas invalida"
  fi

  FINISHED_AT="$(iso_utc)"
  if ((${#INVALID_REASONS[@]} == 0)); then
    EXECUTION_STATUS="valid"
    overall_status="VALID"
  else
    EXECUTION_STATUS="invalid"
    overall_status="INVALID"
  fi

  write_metadata "$EXECUTION_STATUS" "$FINISHED_AT"
  write_checklist "$overall_status"
  FINALIZED=1

  print_step "Resultado"
  print_action "Execucao $overall_status: $RESULT_DIR"
  print_action "Traces exportados: $TRACE_COUNT"
}

main() {
  parse_arguments "$@"
  validate_arguments

  TEMP_DIR="$(mktemp -d)"
  run_preflight
  prepare_data
  create_result_structure
  start_export_port_forwards
  start_collector
  run_k6_job
  drain_workload
  export_evidence
  finalize_execution
}

main "$@"
