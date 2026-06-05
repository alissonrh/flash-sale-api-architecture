#!/usr/bin/env bash
set -euo pipefail

load_tests_dir="load-tests"
results_dir="results/k6"
base_url="http://host.docker.internal:8000"
reset_cmd="python reset_demo_data.py"
run_reset=1
run_all=1
list_only=0
timestamp="$(date +"%Y%m%d-%H%M%S")"
declare -a selected_scripts=()

usage() {
  cat <<'EOF'
Uso:
  ./scripts/run-k6.sh
  ./scripts/run-k6.sh --script baseline_checkout.js
  ./scripts/run-k6.sh --script load-tests/checkout-sync-baseline.js

Opcoes:
  --all                    Roda todos os .js em load-tests (padrao)
  --script <arquivo>       Roda um teste especifico. Pode ser nome ou caminho
  --base-url <url>         URL da API vista pelo k6 em Docker
                           Padrao: http://host.docker.internal:8000
  --results-dir <dir>      Pasta dos JSONs de resultado
                           Padrao: results/k6
  --reset-cmd <comando>    Comando executado antes de cada teste
                           Padrao: python reset_demo_data.py
  --no-reset               Nao executa reset_demo_data.py antes dos testes
  --list                   Lista os testes encontrados e sai
  -h, --help               Mostra esta ajuda

Exemplos:
  ./scripts/run-k6.sh
  ./scripts/run-k6.sh --script checkout-sync-baseline.js
  ./scripts/run-k6.sh --base-url http://host.docker.internal:8000
  ./scripts/run-k6.sh --reset-cmd "docker compose exec -T api python reset_demo_data.py"
EOF
}

die() {
  echo "Erro: $*" >&2
  exit 1
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

normalize_script_path() {
  local script="$1"

  if [[ -f "$script" ]]; then
    echo "$script"
    return
  fi

  if [[ -f "$load_tests_dir/$script" ]]; then
    echo "$load_tests_dir/$script"
    return
  fi

  die "teste k6 nao encontrado: $script"
}

collect_all_scripts() {
  local found=()

  while IFS= read -r script; do
    found+=("$script")
  done < <(find "$load_tests_dir" -maxdepth 1 -type f -name '*.js' | sort)

  if [[ ${#found[@]} -eq 0 ]]; then
    die "nenhum teste .js encontrado em $load_tests_dir"
  fi

  printf '%s\n' "${found[@]}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --all)
      run_all=1
      selected_scripts=()
      shift
      ;;
    --script)
      [[ $# -ge 2 ]] || die "--script precisa de um arquivo"
      run_all=0
      selected_scripts+=("$(normalize_script_path "$2")")
      shift 2
      ;;
    --base-url)
      [[ $# -ge 2 ]] || die "--base-url precisa de uma URL"
      base_url="$2"
      shift 2
      ;;
    --results-dir)
      [[ $# -ge 2 ]] || die "--results-dir precisa de uma pasta"
      results_dir="$2"
      shift 2
      ;;
    --reset-cmd)
      [[ $# -ge 2 ]] || die "--reset-cmd precisa de um comando"
      reset_cmd="$2"
      shift 2
      ;;
    --no-reset)
      run_reset=0
      shift
      ;;
    --list)
      list_only=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "argumento desconhecido: $1. Use --help para ver as opcoes."
      ;;
  esac
done

[[ -d "$load_tests_dir" ]] || die "pasta nao encontrada: $load_tests_dir"

if [[ $run_all -eq 1 ]]; then
  mapfile -t selected_scripts < <(collect_all_scripts)
fi

if [[ $list_only -eq 1 ]]; then
  printf 'Testes encontrados em %s:\n' "$load_tests_dir"
  printf '  %s\n' "${selected_scripts[@]}"
  exit 0
fi

command -v docker >/dev/null 2>&1 || die "docker nao encontrado no PATH"

if [[ $run_reset -eq 1 ]]; then
  command -v python >/dev/null 2>&1 || {
    if [[ "$reset_cmd" == "python reset_demo_data.py" ]]; then
      die "python nao encontrado no PATH. Use --reset-cmd \"docker compose exec -T api python reset_demo_data.py\" ou --no-reset."
    fi
  }
fi

mkdir -p "$results_dir"

host_load_tests_dir="$(to_docker_path "$load_tests_dir")"
host_results_dir="$(to_docker_path "$results_dir")"

for script in "${selected_scripts[@]}"; do
  script_name="$(basename "$script" .js)"
  summary_file="${script_name}-${timestamp}-summary.json"
  summary_container="/results/$summary_file"
  start_ts="$(date +"%H:%M:%S")"

  echo
  echo "==> Teste: $script"
  echo "==> INICIO K6 $start_ts"

  if [[ $run_reset -eq 1 ]]; then
    echo "==> Resetando dados: $reset_cmd"
    eval "$reset_cmd"
  fi

  echo "==> Rodando k6 contra $base_url"
  k6_status=0
  MSYS_NO_PATHCONV=1 docker run --rm \
    -v "$host_load_tests_dir:/scripts:ro" \
    -v "$host_results_dir:/results" \
    grafana/k6 run \
      -e BASE_URL="$base_url" \
      --summary-export "$summary_container" \
      "/scripts/$(basename "$script")" || k6_status=$?

  end_ts="$(date +"%H:%M:%S")"
  echo "==> FIM K6 $end_ts"
  echo "==> JSON salvo em: $results_dir/$summary_file"

  if [[ $k6_status -ne 0 ]]; then
    echo "Erro: k6 terminou com status $k6_status" >&2
    exit $k6_status
  fi
done

echo
echo "Concluido. Resultados em: $results_dir"
