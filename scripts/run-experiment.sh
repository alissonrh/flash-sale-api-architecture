#!/usr/bin/env bash
set -euo pipefail

K6_IMAGE="grafana/k6@sha256:632ddbc81a4a9fdc9e597da91ab1d8fcf1916dd988b43b4a4559d2f8d8e73d47"
BASE_URL="http://host.docker.internal:8000"
RESULTS_ROOT="results/experiments"
PRE_COLLECTION_SECONDS=30
DRAIN_TIMEOUT_SECONDS=180
COOLDOWN_SECONDS=120
PROGRAMMED_LOAD_SECONDS=90
SCENARIO=""
RUN_NUMBER=""
NO_COOLDOWN=0
COLLECTOR_PID=""
STOP_FILE=""
RESULT_DIR=""
STARTED_AT=""
COLLECTION_STARTED_AT=""
K6_STARTED_AT=""
K6_FINISHED_AT=""
FINISHED_AT=""
K6_EXIT_CODE=""
EXECUTION_STATUS="not_started"
FINALIZED=0

usage() {
  cat <<'EOF'
Uso:
  ./scripts/run-experiment.sh --scenario c0b --run 1
  ./scripts/run-experiment.sh --scenario c0m --run 1
  ./scripts/run-experiment.sh --scenario c0a --run 1

Opcoes:
  --scenario c0b|c0m|c0a  Cenario oficial
  --run 1|2|3             Repeticao oficial
  --base-url <url>         URL da API vista pelo k6
  --no-cooldown            Nao aguarda 120 segundos ao final
  --help                   Mostra esta ajuda
EOF
}

die() {
  echo "Erro: $*" >&2
  exit 1
}

iso_utc() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

epoch_utc() {
  date -u +"%s"
}

load_profile() {
  case "$1" in
    c0b) echo "baixa" ;;
    c0m) echo "moderada" ;;
    c0a) echo "alta" ;;
    *) die "cenario invalido: $1" ;;
  esac
}

to_docker_path() {
  local path="$1"

  if command -v cygpath >/dev/null 2>&1; then
    cygpath -aw "$path" | tr '\\' '/'
    return
  fi

  if command -v pwd >/dev/null 2>&1 && pwd -W >/dev/null 2>&1; then
    local old_pwd
    old_pwd="$PWD"
    cd "$path"
    pwd -W | tr '\\' '/'
    cd "$old_pwd"
    return
  fi

  (cd "$path" && pwd)
}

count_exact_container() {
  docker ps --filter "name=^/$1$" --format "{{.Names}}" | wc -l | tr -d '[:space:]'
}

require_running_container() {
  local container="$1"
  local running
  running="$(docker inspect -f "{{.State.Running}}" "$container" 2>/dev/null || true)"
  [[ "$running" == "true" ]] || die "container nao esta em execucao: $container"
}

compose_exec_api() {
  docker compose exec -T api "$@"
}

rabbitmq_json() {
  python -c "import base64,json,urllib.request; req=urllib.request.Request('http://localhost:15672/api/queues/%2F/checkout_requests', headers={'Authorization':'Basic '+base64.b64encode(b'app:app').decode()}); print(urllib.request.urlopen(req, timeout=10).read().decode())"
}

rabbitmq_field() {
  local field="$1"
  rabbitmq_json | python -c "import json,sys; data=json.load(sys.stdin); print(data.get('$field', 0))"
}

rabbitmq_prefetch_count() {
  rabbitmq_json | python -c "import json,sys; data=json.load(sys.stdin); details=data.get('consumer_details') or [{}]; print(details[0].get('prefetch_count', 0))"
}

write_json_file() {
  local path="$1"
  local payload="$2"
  python -c "import json,sys; json.dump(json.loads(sys.argv[2]), open(sys.argv[1], 'w', encoding='utf-8'), ensure_ascii=False, indent=2); open(sys.argv[1], 'a', encoding='utf-8').write('\n')" "$path" "$payload"
}

write_metadata() {
  local status="$1"
  local finished_at="${2:-}"
  local observed_k6_seconds=""

  if [[ -n "$K6_STARTED_AT" && -n "$K6_FINISHED_AT" ]]; then
    observed_k6_seconds="$((K6_FINISHED_EPOCH - K6_STARTED_EPOCH))"
  fi

  METADATA_FILE="$RESULT_DIR/metadata.json" \
  EXPERIMENT_ID="$SCENARIO-run-$RUN_NUMBER" \
  SCENARIO_VALUE="$SCENARIO" \
  LOAD_PROFILE_VALUE="$(load_profile "$SCENARIO")" \
  RUN_NUMBER_VALUE="$RUN_NUMBER" \
  SCRIPT_VALUE="load-tests/$SCENARIO.js" \
  GIT_COMMIT_VALUE="$(git rev-parse HEAD 2>/dev/null || echo unknown)" \
  GIT_DIRTY_VALUE="$(if [[ -n "$(git status --short 2>/dev/null)" ]]; then echo true; else echo false; fi)" \
  STARTED_AT_VALUE="$STARTED_AT" \
  K6_STARTED_AT_VALUE="$K6_STARTED_AT" \
  K6_FINISHED_AT_VALUE="$K6_FINISHED_AT" \
  FINISHED_AT_VALUE="$finished_at" \
  OBSERVED_K6_SECONDS_VALUE="$observed_k6_seconds" \
  BASE_URL_VALUE="$BASE_URL" \
  K6_EXIT_CODE_VALUE="$K6_EXIT_CODE" \
  EXECUTION_STATUS_VALUE="$status" \
  python - <<'PY'
import json
import os

def env(name, default=None):
    return os.environ.get(name, default)

def env_int(name, default=None):
    value = env(name)
    if value in (None, ""):
        return default
    return int(value)

metadata = {
    "experiment_id": env("EXPERIMENT_ID"),
    "scenario": env("SCENARIO_VALUE"),
    "load_profile": env("LOAD_PROFILE_VALUE"),
    "run_number": env_int("RUN_NUMBER_VALUE"),
    "script": env("SCRIPT_VALUE"),
    "git_commit": env("GIT_COMMIT_VALUE"),
    "git_dirty": env("GIT_DIRTY_VALUE") == "true",
    "started_at": env("STARTED_AT_VALUE"),
    "k6_started_at": env("K6_STARTED_AT_VALUE"),
    "k6_finished_at": env("K6_FINISHED_AT_VALUE"),
    "finished_at": env("FINISHED_AT_VALUE"),
    "programmed_load_seconds": 90,
    "observed_k6_seconds": env_int("OBSERVED_K6_SECONDS_VALUE"),
    "drain_timeout_seconds": 180,
    "pre_collection_seconds": 30,
    "cooldown_seconds": 120,
    "base_url": env("BASE_URL_VALUE"),
    "product_ids": [1, 2, 3],
    "initial_stock_per_product": {"1": 10000, "2": 10000, "3": 10000},
    "api_processes": 1,
    "worker_containers": 1,
    "rabbitmq_consumers": 1,
    "rabbitmq_prefetch": 1,
    "worker_processing_delay_seconds": 0,
    "diagnostic_logs": False,
    "uvicorn_reload": False,
    "uvicorn_access_log": False,
    "sqlalchemy_echo": False,
    "sqlalchemy_pool_configuration": "padrao do SQLAlchemy, sem ajuste explicito",
    "prometheus_scrape_interval": "5 segundos",
    "versions": {
        "Python": "3.13.13",
        "FastAPI": "0.136.3",
        "Uvicorn": "0.48.0",
        "SQLAlchemy": "2.0.50",
        "Pika": "1.4.1",
        "Psycopg": "3.3.4",
        "PostgreSQL": "18.3",
        "RabbitMQ": "4.3.0",
        "Prometheus": "3.11.3",
        "Docker": "29.1.3",
        "Docker Compose": "5.0.1",
        "k6": "v2.0.0+dirty",
    },
    "k6_exit_code": env_int("K6_EXIT_CODE_VALUE"),
    "execution_status": env("EXECUTION_STATUS_VALUE"),
}

with open(env("METADATA_FILE"), "w", encoding="utf-8") as fh:
    json.dump(metadata, fh, ensure_ascii=False, indent=2)
    fh.write("\n")
PY
}

stop_collector() {
  if [[ -n "$STOP_FILE" && -d "$(dirname "$STOP_FILE")" ]]; then
    touch "$STOP_FILE"
  fi

  if [[ -n "$COLLECTOR_PID" ]]; then
    wait "$COLLECTOR_PID" 2>/dev/null || true
    COLLECTOR_PID=""
  fi
}

cleanup() {
  local exit_code=$?
  stop_collector

  if [[ -n "$RESULT_DIR" && -d "$RESULT_DIR" && "$FINALIZED" -eq 0 ]]; then
    FINISHED_AT="$(iso_utc)"
    write_metadata "interrupted_or_failed" "$FINISHED_AT" || true
  fi

  exit "$exit_code"
}

trap cleanup EXIT
trap 'echo "Interrompido pelo usuario." >&2; exit 130' INT TERM

while [[ $# -gt 0 ]]; do
  case "$1" in
    --scenario)
      [[ $# -ge 2 ]] || die "--scenario precisa de um valor"
      SCENARIO="$2"
      shift 2
      ;;
    --run)
      [[ $# -ge 2 ]] || die "--run precisa de um valor"
      RUN_NUMBER="$2"
      shift 2
      ;;
    --base-url)
      [[ $# -ge 2 ]] || die "--base-url precisa de uma URL"
      BASE_URL="$2"
      shift 2
      ;;
    --no-cooldown)
      NO_COOLDOWN=1
      shift
      ;;
    --help)
      usage
      exit 0
      ;;
    *)
      die "argumento desconhecido: $1"
      ;;
  esac
done

[[ "$SCENARIO" =~ ^c0(b|m|a)$ ]] || die "use --scenario c0b, c0m ou c0a"
[[ "$RUN_NUMBER" =~ ^[123]$ ]] || die "use --run 1, 2 ou 3"
[[ -f "load-tests/$SCENARIO.js" ]] || die "script nao encontrado: load-tests/$SCENARIO.js"

command -v docker >/dev/null 2>&1 || die "docker nao encontrado no PATH"
command -v python >/dev/null 2>&1 || die "python nao encontrado no PATH"

EXPERIMENT_ID="$SCENARIO-run-$RUN_NUMBER"
RESULT_DIR="$RESULTS_ROOT/$EXPERIMENT_ID"
STOP_FILE="$RESULT_DIR/.collector-stop"

mkdir -p "$RESULTS_ROOT"
if [[ -e "$RESULT_DIR" ]]; then
  die "diretorio de execucao ja existe: $RESULT_DIR"
fi

STARTED_AT="$(iso_utc)"
COLLECTION_STARTED_AT="$STARTED_AT"
EXECUTION_STATUS="preflight"

echo "Validando ambiente para $EXPERIMENT_ID..."
for container in \
  flash-sale-postgres \
  flash-sale-rabbitmq \
  flash-sale-api \
  flash-sale-worker \
  flash-sale-prometheus
do
  require_running_container "$container"
done

[[ "$(count_exact_container flash-sale-api)" == "1" ]] || die "deve existir exatamente um container flash-sale-api"
[[ "$(count_exact_container flash-sale-worker)" == "1" ]] || die "deve existir exatamente um container flash-sale-worker"

compose_exec_api python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=10).read()" >/dev/null

[[ "$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' flash-sale-api | grep '^DEV_MODE=' | cut -d= -f2-)" == "0" ]] || die "DEV_MODE precisa ser 0"
[[ "$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' flash-sale-api | grep '^DIAGNOSTIC_LOGS=' | cut -d= -f2-)" == "0" ]] || die "DIAGNOSTIC_LOGS precisa ser 0 na API"
[[ "$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' flash-sale-worker | grep '^DIAGNOSTIC_LOGS=' | cut -d= -f2-)" == "0" ]] || die "DIAGNOSTIC_LOGS precisa ser 0 no worker"
[[ "$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' flash-sale-worker | grep '^WORKER_PROCESSING_DELAY_SECONDS=' | cut -d= -f2-)" == "0" ]] || die "WORKER_PROCESSING_DELAY_SECONDS precisa ser 0"

[[ "$(rabbitmq_field messages_ready)" == "0" ]] || die "fila checkout_requests possui messages_ready diferente de 0"
[[ "$(rabbitmq_field messages_unacknowledged)" == "0" ]] || die "fila checkout_requests possui messages_unacknowledged diferente de 0"
[[ "$(rabbitmq_field consumers)" == "1" ]] || die "fila checkout_requests precisa ter exatamente 1 consumidor"
[[ "$(rabbitmq_prefetch_count)" == "1" ]] || die "fila checkout_requests precisa ter prefetch_count=1"

compose_exec_api python export_experiment_db_summary.py --assert-idle >/dev/null

echo "Resetando dados dentro do container da API..."
docker compose exec -T api python reset_demo_data.py
compose_exec_api python export_experiment_db_summary.py --assert-stocks >/dev/null

mkdir "$RESULT_DIR"
write_metadata "$EXECUTION_STATUS" ""

echo "Iniciando coleta 30 segundos antes do k6..."
python scripts/collect-experiment-metrics.py collect \
  --results-dir "$RESULT_DIR" \
  --stop-file "$STOP_FILE" \
  --interval 5 >"$RESULT_DIR/collector.log" 2>&1 &
COLLECTOR_PID=$!
sleep "$PRE_COLLECTION_SECONDS"

host_load_tests_dir="$(to_docker_path "load-tests")"
host_results_dir="$(to_docker_path "$RESULT_DIR")"

echo "Executando k6 para $EXPERIMENT_ID..."
K6_STARTED_AT="$(iso_utc)"
K6_STARTED_EPOCH="$(epoch_utc)"
set +e
MSYS_NO_PATHCONV=1 docker run --rm \
  -e BASE_URL="$BASE_URL" \
  -e RUN_ID="$EXPERIMENT_ID" \
  -v "$host_load_tests_dir:/scripts:ro" \
  -v "$host_results_dir:/results" \
  "$K6_IMAGE" \
  run \
  --summary-export=/results/k6-summary.json \
  "/scripts/$SCENARIO.js" 2>&1 | tee "$RESULT_DIR/k6.log"
K6_EXIT_CODE=${PIPESTATUS[0]}
set -e
K6_FINISHED_AT="$(iso_utc)"
K6_FINISHED_EPOCH="$(epoch_utc)"

echo "Drenando fila e pedidos por ate $DRAIN_TIMEOUT_SECONDS segundos..."
DRAIN_STARTED_AT="$(iso_utc)"
DRAIN_STARTED_EPOCH="$(epoch_utc)"
DRAIN_COMPLETED=false
FINAL_READY=0
FINAL_UNACK=0
FINAL_PENDING=0
FINAL_PROCESSING=0

while true; do
  FINAL_READY="$(rabbitmq_field messages_ready)"
  FINAL_UNACK="$(rabbitmq_field messages_unacknowledged)"
  DB_STATE="$(compose_exec_api python export_experiment_db_summary.py)"
  FINAL_PENDING="$(python -c "import json,sys; print(json.load(sys.stdin)['orders_by_status'].get('PENDING', 0))" <<<"$DB_STATE")"
  FINAL_PROCESSING="$(python -c "import json,sys; print(json.load(sys.stdin)['orders_by_status'].get('PROCESSING', 0))" <<<"$DB_STATE")"

  if [[ "$FINAL_READY" == "0" && "$FINAL_UNACK" == "0" && "$FINAL_PENDING" == "0" && "$FINAL_PROCESSING" == "0" ]]; then
    DRAIN_COMPLETED=true
    break
  fi

  now_epoch="$(epoch_utc)"
  if (( now_epoch - DRAIN_STARTED_EPOCH >= DRAIN_TIMEOUT_SECONDS )); then
    break
  fi

  sleep 5
done

DRAIN_FINISHED_AT="$(iso_utc)"
DRAIN_FINISHED_EPOCH="$(epoch_utc)"
DRAIN_DURATION_SECONDS="$((DRAIN_FINISHED_EPOCH - DRAIN_STARTED_EPOCH))"

python -c "import json,sys; json.dump({'drain_started_at':sys.argv[1],'drain_finished_at':sys.argv[2],'drain_duration_seconds':int(sys.argv[3]),'completed':sys.argv[4]=='true','timeout_seconds':int(sys.argv[5]),'final_messages_ready':int(sys.argv[6]),'final_messages_unacknowledged':int(sys.argv[7]),'final_pending_orders':int(sys.argv[8]),'final_processing_orders':int(sys.argv[9])}, open(sys.argv[10], 'w', encoding='utf-8'), ensure_ascii=False, indent=2); open(sys.argv[10], 'a', encoding='utf-8').write('\n')" \
  "$DRAIN_STARTED_AT" "$DRAIN_FINISHED_AT" "$DRAIN_DURATION_SECONDS" "$DRAIN_COMPLETED" "$DRAIN_TIMEOUT_SECONDS" \
  "$FINAL_READY" "$FINAL_UNACK" "$FINAL_PENDING" "$FINAL_PROCESSING" "$RESULT_DIR/drain-summary.json"

stop_collector

echo "Exportando resumo do banco, Prometheus e logs..."
compose_exec_api python export_experiment_db_summary.py >"$RESULT_DIR/db-summary.json"
python scripts/collect-experiment-metrics.py export-prometheus \
  --output "$RESULT_DIR/prometheus.csv" \
  --start "$COLLECTION_STARTED_AT" \
  --end "$DRAIN_FINISHED_AT" \
  --step 5s
docker logs flash-sale-api --since "$COLLECTION_STARTED_AT" >"$RESULT_DIR/api.log" 2>&1
docker logs flash-sale-worker --since "$COLLECTION_STARTED_AT" >"$RESULT_DIR/worker.log" 2>&1

FINISHED_AT="$(iso_utc)"
if [[ "$K6_EXIT_CODE" == "0" ]]; then
  EXECUTION_STATUS="completed"
else
  EXECUTION_STATUS="completed_with_k6_exit_$K6_EXIT_CODE"
fi
write_metadata "$EXECUTION_STATUS" "$FINISHED_AT"
FINALIZED=1

if [[ "$NO_COOLDOWN" -eq 0 ]]; then
  echo "Coleta salva. Aguardando cooldown de $COOLDOWN_SECONDS segundos antes de encerrar..."
  sleep "$COOLDOWN_SECONDS"
fi

echo "Execucao finalizada: $RESULT_DIR"
exit "$K6_EXIT_CODE"
