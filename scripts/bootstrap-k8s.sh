#!/usr/bin/env bash
set -Eeuo pipefail

NAMESPACE="flash-sale"
SCENARIO="c1"
APPLICATION_IMAGE="flash-sale-api:k8s"
DOCKER_DESKTOP_CONTEXT="docker-desktop"
ROLLOUT_TIMEOUT="${ROLLOUT_TIMEOUT:-300s}"
METRICS_WAIT_SECONDS="${METRICS_WAIT_SECONDS:-180}"
POD_STABILIZATION_WAIT_SECONDS="${POD_STABILIZATION_WAIT_SECONDS:-120}"
METRICS_SERVER_VERSION="v0.8.1"
METRICS_SERVER_URL="https://github.com/kubernetes-sigs/metrics-server/releases/download/v0.8.1/components.yaml"
APPLICATION_IMAGE_DIGEST=""
APPLICATION_PRELOAD_IMAGE=""
APPLICATION_RUNTIME_IMAGE=""
IMAGE_LOADER_POD="flash-sale-image-loader"

CURRENT_STAGE="inicializacao"

handle_error() {
  local exit_code="$1"
  local line_number="$2"
  local command="$3"

  trap - ERR
  set +e

  printf '\nERRO na etapa "%s" (linha %s).\n' "$CURRENT_STAGE" "$line_number" >&2
  printf 'Comando que falhou: %s\n' "$command" >&2

  if command -v kubectl >/dev/null 2>&1 &&
    kubectl get namespace "$NAMESPACE" >/dev/null 2>&1; then
    printf '\nEstado atual dos Pods:\n' >&2
    kubectl get pods -n "$NAMESPACE" -o wide >&2
  fi

  exit "$exit_code"
}

trap 'handle_error "$?" "$LINENO" "$BASH_COMMAND"' ERR

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

NAMESPACE_MANIFESTS=(
  "k8s/namespace.yaml"
)

CONFIGURATION_MANIFESTS=(
  "k8s/configmap.yaml"
  "k8s/secret.local.yaml"
)

BASE_MANIFESTS=(
  "k8s/postgres-pvc.yaml"
  "k8s/postgres-service.yaml"
  "k8s/postgres-deployment.yaml"
  "k8s/rabbitmq-pvc.yaml"
  "k8s/rabbitmq-service.yaml"
  "k8s/rabbitmq-deployment.yaml"
)

OBSERVABILITY_MANIFESTS=(
  "k8s/jaeger-service.yaml"
  "k8s/jaeger-deployment.yaml"
  "k8s/otel-collector-configmap.yaml"
  "k8s/otel-collector-service.yaml"
  "k8s/otel-collector-deployment.yaml"
  "k8s/prometheus-configmap.yaml"
  "k8s/prometheus-service.yaml"
  "k8s/prometheus-deployment.yaml"
)

APPLICATION_MANIFESTS=(
  "k8s/api-service.yaml"
  "k8s/api-deployment.yaml"
  "k8s/worker-deployment.yaml"
)

# Manifests especificos do cenario. O C1 nao possui adicionais, o C2 inclui o
# HPA da API, o C3 inclui o gateway Kong DB-less com rate limiting e o C4
# combina os mesmos manifests de C2 e C3.
SCENARIO_MANIFESTS=(
)

usage() {
  cat <<'EOF'
Uso:
  bash scripts/bootstrap-k8s.sh [--scenario c1|c2|c3|c4]

O cenario padrao e c1.
EOF
}

print_step() {
  CURRENT_STAGE="$1"
  printf '\n==> %s\n' "$CURRENT_STAGE"
}

print_action() {
  printf '  -> %s\n' "$1"
}

die() {
  printf 'ERRO na etapa "%s": %s\n' "$CURRENT_STAGE" "$*" >&2
  exit 1
}

parse_arguments() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --scenario)
        [[ $# -ge 2 ]] || die "--scenario exige um valor"
        SCENARIO="$2"
        shift 2
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

  [[ "$SCENARIO" =~ ^(c1|c2|c3|c4)$ ]] || \
    die "--scenario aceita somente c1, c2, c3 ou c4"

  if [[ "$SCENARIO" == "c2" ]]; then
    SCENARIO_MANIFESTS=("k8s/api-hpa.yaml")
  elif [[ "$SCENARIO" == "c3" ]]; then
    SCENARIO_MANIFESTS=(
      "k8s/gateway-configmap.yaml"
      "k8s/gateway-service.yaml"
      "k8s/gateway-deployment.yaml"
    )
  elif [[ "$SCENARIO" == "c4" ]]; then
    SCENARIO_MANIFESTS=(
      "k8s/api-hpa.yaml"
      "k8s/gateway-configmap.yaml"
      "k8s/gateway-service.yaml"
      "k8s/gateway-deployment.yaml"
    )
  fi
}

require_command() {
  local command_name="$1"

  if ! command -v "$command_name" >/dev/null 2>&1; then
    die "comando '$command_name' nao encontrado no PATH"
  fi
}

validate_manifest_files() {
  local manifest

  for manifest in "$@"; do
    if [[ ! -f "$manifest" ]]; then
      die "manifest obrigatorio nao encontrado: $manifest"
    fi
  done
}

apply_manifests() {
  local manifest

  for manifest in "$@"; do
    print_action "Aplicando $manifest"
    kubectl apply -f "$manifest"
  done
}

wait_for_rollout() {
  local namespace="$1"
  local deployment="$2"

  print_action "Aguardando deployment/$deployment"
  kubectl rollout status "deployment/$deployment" \
    -n "$namespace" \
    --timeout="$ROLLOUT_TIMEOUT"
}

wait_for_pvc() {
  local pvc="$1"

  print_action "Aguardando pvc/$pvc ficar Bound"
  kubectl wait "pvc/$pvc" \
    -n "$NAMESPACE" \
    --for=jsonpath='{.status.phase}'=Bound \
    --timeout="$ROLLOUT_TIMEOUT"
}

wait_for_deployment_pods() {
  local deployment="$1"
  local selector="$2"
  local deadline=$((SECONDS + POD_STABILIZATION_WAIT_SECONDS))
  local expected_replicas
  local current_pods
  local current_count

  expected_replicas="$(
    kubectl get "deployment/$deployment" \
      -n "$NAMESPACE" \
      -o jsonpath='{.spec.replicas}'
  )"
  expected_replicas="${expected_replicas//$'\r'/}"

  print_action "Aguardando Pods antigos de deployment/$deployment encerrarem"

  while true; do
    current_pods="$(
      kubectl get pods \
        -n "$NAMESPACE" \
        -l "$selector" \
        -o name
    )"
    current_count="$(
      printf '%s\n' "$current_pods" |
        awk 'NF { count++ } END { print count + 0 }'
    )"

    if [[ "$current_count" == "$expected_replicas" ]]; then
      return 0
    fi

    if ((SECONDS >= deadline)); then
      kubectl get pods -n "$NAMESPACE" -l "$selector" -o wide >&2
      die "timeout aguardando estabilizacao dos Pods de deployment/$deployment"
    fi

    sleep 2
  done
}

wait_for_metrics_command() {
  local description="$1"
  shift

  local deadline=$((SECONDS + METRICS_WAIT_SECONDS))
  local output=""

  print_action "Aguardando $description"

  while true; do
    if output="$("$@" 2>&1)"; then
      printf '%s\n' "$output"
      return 0
    fi

    if ((SECONDS >= deadline)); then
      printf '%s\n' "$output" >&2
      die "timeout aguardando $description"
    fi

    sleep 5
  done
}

wait_for_hpa_cpu_metrics() {
  local deadline=$((SECONDS + METRICS_WAIT_SECONDS))
  local hpa_json=""

  print_action "Aguardando o HPA obter metricas de CPU"

  while true; do
    if hpa_json="$(kubectl get hpa/api-hpa -n "$NAMESPACE" -o json 2>/dev/null)" && \
      printf '%s' "$hpa_json" | python -c '
import json, sys
hpa = json.load(sys.stdin)
metrics = hpa.get("status", {}).get("currentMetrics") or []
cpu = next(
    (
        item.get("resource", {}).get("current", {}).get("averageUtilization")
        for item in metrics
        if item.get("type") == "Resource"
        and item.get("resource", {}).get("name") == "cpu"
    ),
    None,
)
conditions = hpa.get("status", {}).get("conditions") or []
active = any(
    item.get("type") == "ScalingActive" and item.get("status") == "True"
    for item in conditions
)
raise SystemExit(0 if cpu is not None and active else 1)
'; then
      return 0
    fi

    if ((SECONDS >= deadline)); then
      [[ -z "$hpa_json" ]] || printf '%s\n' "$hpa_json" >&2
      die "timeout aguardando metricas de CPU no HPA api-hpa"
    fi

    sleep 5
  done
}

assert_no_ingress_or_rate_limiting() {
  kubectl get ingress,configmap,deployment,service \
    -n "$NAMESPACE" \
    -o json | python -c '
import json, sys

items = json.load(sys.stdin).get("items", [])
ingresses = [
    item.get("metadata", {}).get("name", "")
    for item in items
    if item.get("kind") == "Ingress"
]
if ingresses:
    raise SystemExit("Ingress inesperado: " + ", ".join(ingresses))

markers = ("rate-limit", "rate_limit", "ratelimit", "kong")
for item in items:
    metadata = item.get("metadata") or {}
    searchable = {
        "name": metadata.get("name", ""),
        "labels": metadata.get("labels") or {},
        "annotations": metadata.get("annotations") or {},
    }
    if item.get("kind") == "ConfigMap":
        searchable["data_keys"] = sorted((item.get("data") or {}).keys())
    if item.get("kind") == "Deployment":
        containers = (
            item.get("spec", {})
            .get("template", {})
            .get("spec", {})
            .get("containers", [])
        )
        searchable["containers"] = [
            {
                "image": container.get("image", ""),
                "env_names": [
                    variable.get("name", "")
                    for variable in container.get("env", [])
                ],
            }
            for container in containers
        ]
    rendered = json.dumps(searchable, sort_keys=True).lower()
    if any(marker in rendered for marker in markers):
        kind = item.get("kind", "recurso")
        name = metadata.get("name", "")
        raise SystemExit(f"possivel rate limiting em {kind}/{name}")
'

  print_action "Ingress, Kong e configuracoes de rate limiting ausentes"
}

assert_keda_disabled() {
  local resource_types
  local resource_type
  local resources

  resource_types="$(kubectl api-resources --api-group=keda.sh -o name 2>/dev/null || true)"
  resource_types="${resource_types//$'\r'/}"

  while IFS= read -r resource_type; do
    [[ "$resource_type" == "scaledobjects.keda.sh" || \
      "$resource_type" == "scaledjobs.keda.sh" ]] || continue
    resources="$(kubectl get "$resource_type" -n "$NAMESPACE" -o name)"
    resources="${resources//$'\r'/}"
    [[ -z "$resources" ]] || \
      die "KEDA precisa estar desativado; recursos encontrados: $resources"
  done <<<"$resource_types"

  print_action "KEDA confirmado sem ScaledObjects ou ScaledJobs no namespace"
}

validate_gateway_configuration() {
  local health_result

  kubectl get deployment/gateway -n "$NAMESPACE" -o json | python -c '
import json, sys
deployment = json.load(sys.stdin)
spec = deployment.get("spec", {})
status = deployment.get("status", {})
containers = spec.get("template", {}).get("spec", {}).get("containers", [])
images = [item.get("image") for item in containers if item.get("name") == "gateway"]
gateway = next((item for item in containers if item.get("name") == "gateway"), {})
environment = {
    item.get("name"): item.get("value")
    for item in gateway.get("env", [])
}
values = (
    spec.get("replicas", 1),
    status.get("availableReplicas", 0),
    status.get("readyReplicas", 0),
    status.get("updatedReplicas", 0),
)
if values != (1, 1, 1, 1):
    raise SystemExit(f"deployment/gateway precisa estar exatamente 1/1; observado {values}")
if images != ["kong:3.9.1-ubuntu"]:
    raise SystemExit(f"imagem do gateway inesperada: {images}")
expected_environment = {
    "KONG_DATABASE": "off",
    "KONG_DECLARATIVE_CONFIG": "/kong_dbless/kong.yml",
    "KONG_PROXY_LISTEN": "0.0.0.0:8000",
}
if any(environment.get(key) != value for key, value in expected_environment.items()):
    raise SystemExit("deployment/gateway nao esta no modo DB-less congelado")
'

  kubectl get service/gateway -n "$NAMESPACE" -o json | python -c '
import json, sys
service = json.load(sys.stdin)
spec = service.get("spec", {})
ports = spec.get("ports") or []
if spec.get("selector") != {"app": "gateway"}:
    raise SystemExit("service/gateway possui selector inesperado")
if len(ports) != 1 or ports[0].get("port") != 8000 or ports[0].get("targetPort") != "proxy":
    raise SystemExit("service/gateway precisa expor 8000 para a porta proxy")
'

  kubectl get configmap/gateway-config -n "$NAMESPACE" -o json | python -c '
import json, sys
config = json.load(sys.stdin).get("data", {}).get("kong.yml", "")
required = (
    "url: http://api:8000",
    "name: checkout",
    "- /checkout",
    "name: rate-limiting",
    "second: 20",
    "limit_by: service",
    "policy: local",
    "hide_client_headers: false",
    "fault_tolerant: false",
)
missing = [value for value in required if value not in config]
if missing:
    raise SystemExit("configuracao DB-less incompleta: " + ", ".join(missing))
if "redis" in config.lower():
    raise SystemExit("o rate limiting nao pode depender de Redis")
'

  health_result="$(kubectl exec deployment/api -n "$NAMESPACE" -- python -c '
import json, urllib.request
with urllib.request.urlopen("http://gateway:8000/health", timeout=10) as response:
    headers = {key.lower(): value for key, value in response.headers.items()}
    standard = {"ratelimit-limit", "ratelimit-remaining", "ratelimit-reset"}
    kong = {"x-ratelimit-limit-second", "x-ratelimit-remaining-second"}
    if response.status != 200:
        raise SystemExit(f"gateway retornou HTTP {response.status} para /health")
    if not (standard.issubset(headers) or kong.issubset(headers)):
        raise SystemExit("cabecalhos de rate limiting ausentes: " + ", ".join(sorted(headers)))
    print(json.dumps({"status": response.status, "rate_limit_headers": {
        key: value for key, value in headers.items() if "ratelimit" in key
    }}, sort_keys=True))
')"

  print_action "Gateway alcancou http://api:8000 via /health: $health_result"
  print_action "Kong DB-less confirmado em 1/1 com limite global de 20 req/s"
}

validate_hpa_configuration() {
  local hpa_names

  hpa_names="$(kubectl get hpa -n "$NAMESPACE" -o name)"
  hpa_names="${hpa_names//$'\r'/}"
  [[ "$hpa_names" == "horizontalpodautoscaler.autoscaling/api-hpa" ]] || \
    die "${SCENARIO^^} exige somente o HPA api-hpa; observado: ${hpa_names:-nenhum}"

  kubectl get hpa/api-hpa -n "$NAMESPACE" -o json | python -c '
import json, sys
hpa = json.load(sys.stdin)
spec = hpa.get("spec", {})
target = spec.get("scaleTargetRef", {})
metrics = spec.get("metrics") or []
behavior = spec.get("behavior") or {}
expected_metrics = [{
    "type": "Resource",
    "resource": {
        "name": "cpu",
        "target": {"type": "Utilization", "averageUtilization": 70},
    },
}]
expected_behavior = {
    "scaleUp": {
        "stabilizationWindowSeconds": 0,
        "selectPolicy": "Max",
        "policies": [
            {"type": "Pods", "value": 4, "periodSeconds": 15},
            {"type": "Percent", "value": 400, "periodSeconds": 15},
        ],
    },
    "scaleDown": {
        "stabilizationWindowSeconds": 120,
        "selectPolicy": "Min",
        "policies": [
            {"type": "Pods", "value": 1, "periodSeconds": 60},
        ],
    },
}
problems = []
if target != {"apiVersion": "apps/v1", "kind": "Deployment", "name": "api"}:
    problems.append("alvo precisa ser apps/v1 Deployment/api")
if spec.get("minReplicas") != 1:
    problems.append("minReplicas precisa ser 1")
if spec.get("maxReplicas") != 5:
    problems.append("maxReplicas precisa ser 5")
if metrics != expected_metrics:
    problems.append("metrica precisa ser somente CPU Utilization em 70%")
if behavior != expected_behavior:
    problems.append("behavior de scale up/down diverge do C2 congelado")
if problems:
    raise SystemExit("; ".join(problems))
'

  wait_for_hpa_cpu_metrics
  print_action "HPA api-hpa confirmado com configuracao congelada do C2"
}

validate_scenario_configuration() {
  local hpa_names

  print_step "Validacao do cenario ${SCENARIO^^}"

  kubectl get deployment/api deployment/worker -n "$NAMESPACE" -o json | python -c '
import json, sys
for deployment in json.load(sys.stdin).get("items", []):
    name = deployment.get("metadata", {}).get("name", "")
    desired = deployment.get("spec", {}).get("replicas", 1)
    available = deployment.get("status", {}).get("availableReplicas", 0)
    if desired != 1 or available != 1:
        raise SystemExit(f"deployment/{name} precisa estar 1/1; observado {available}/{desired}")
'

  hpa_names="$(kubectl get hpa -n "$NAMESPACE" -o name)"
  hpa_names="${hpa_names//$'\r'/}"

  assert_keda_disabled

  if [[ "$SCENARIO" == "c1" || "$SCENARIO" == "c3" ]]; then
    [[ -z "$hpa_names" ]] || die "${SCENARIO^^} nao pode possuir HPA: $hpa_names"
    if [[ "$SCENARIO" == "c3" ]]; then
      validate_gateway_configuration
    else
      assert_no_ingress_or_rate_limiting
      print_action "C1 confirmado sem HPA"
    fi
    return 0
  fi

  validate_hpa_configuration
  if [[ "$SCENARIO" == "c4" ]]; then
    validate_gateway_configuration
    print_action "C4 confirmado com HPA de C2 e Kong de C3"
  else
    assert_no_ingress_or_rate_limiting
    print_action "C2 confirmado com HPA api-hpa (1..5, CPU 70%)"
  fi
}

assert_deployment_image() {
  local deployment="$1"
  local container="$2"
  local actual_image

  actual_image="$(
    kubectl get "deployment/$deployment" \
      -n "$NAMESPACE" \
      -o "jsonpath={.spec.template.spec.containers[?(@.name=='$container')].image}"
  )"
  actual_image="${actual_image//$'\r'/}"

  if [[ "$actual_image" != "$APPLICATION_RUNTIME_IMAGE" ]]; then
    die "deployment/$deployment usa '$actual_image'; esperado '$APPLICATION_RUNTIME_IMAGE'"
  fi

  print_action "deployment/$deployment usa o digest reconstruido"
}

assert_pod_image_id() {
  local application_label="$1"
  local container="$2"
  local actual_image_id

  actual_image_id="$(
    kubectl get pods \
      -n "$NAMESPACE" \
      -l "app=$application_label" \
      -o "jsonpath={.items[0].status.containerStatuses[?(@.name=='$container')].imageID}"
  )"
  actual_image_id="${actual_image_id//$'\r'/}"
  actual_image_id="${actual_image_id##*@}"

  if [[ "$actual_image_id" != "$APPLICATION_IMAGE_DIGEST" ]]; then
    die "Pod de $application_label usa digest '$actual_image_id'; esperado '$APPLICATION_IMAGE_DIGEST'"
  fi

  print_action "Pod de $application_label executa $APPLICATION_IMAGE_DIGEST"
}

preload_application_image() {
  local loader_image_id

  print_action "Removendo pre-carga anterior, se existir"
  kubectl delete pod "$IMAGE_LOADER_POD" \
    -n "$NAMESPACE" \
    --ignore-not-found \
    --wait=true

  print_action "Pre-carregando o digest atual no no Kubernetes"
  MSYS_NO_PATHCONV=1 kubectl run "$IMAGE_LOADER_POD" \
    -n "$NAMESPACE" \
    --image="$APPLICATION_PRELOAD_IMAGE" \
    --image-pull-policy=IfNotPresent \
    --restart=Never \
    --command -- /bin/true

  kubectl wait "pod/$IMAGE_LOADER_POD" \
    -n "$NAMESPACE" \
    --for=jsonpath='{.status.phase}'=Succeeded \
    --timeout="$ROLLOUT_TIMEOUT"

  loader_image_id="$(
    kubectl get "pod/$IMAGE_LOADER_POD" \
      -n "$NAMESPACE" \
      -o jsonpath='{.status.containerStatuses[0].imageID}'
  )"
  loader_image_id="${loader_image_id//$'\r'/}"
  loader_image_id="${loader_image_id##*@}"

  if [[ "$loader_image_id" != "$APPLICATION_IMAGE_DIGEST" ]]; then
    die "pre-carga usou digest '$loader_image_id'; esperado '$APPLICATION_IMAGE_DIGEST'"
  fi

  kubectl delete "pod/$IMAGE_LOADER_POD" \
    -n "$NAMESPACE" \
    --wait=true
}

assert_environment_value() {
  local deployment="$1"
  local variable_name="$2"
  local expected_value="$3"
  local actual_value

  actual_value="$(
    kubectl exec "deployment/$deployment" -n "$NAMESPACE" -- \
      printenv "$variable_name"
  )"
  actual_value="${actual_value//$'\r'/}"

  if [[ "$actual_value" != "$expected_value" ]]; then
    die "deployment/$deployment recebeu $variable_name='$actual_value'; esperado '$expected_value'"
  fi

  print_action "deployment/$deployment: $variable_name=$expected_value"
}

assert_trace_sample_ratio() {
  local deployment="$1"
  local actual_ratio

  actual_ratio="$(
    kubectl exec "deployment/$deployment" -n "$NAMESPACE" -- \
      python -c 'from app.observability.tracing import trace_sample_ratio; print(trace_sample_ratio())'
  )"
  actual_ratio="${actual_ratio//$'\r'/}"

  if [[ "$actual_ratio" != "0.01" ]]; then
    die "trace_sample_ratio() em deployment/$deployment retornou '$actual_ratio'; esperado '0.01'"
  fi

  print_action "deployment/$deployment: trace_sample_ratio()=0.01"
}

# -----------------------------------------------------------------------------
# Verificacoes
# -----------------------------------------------------------------------------

validate_prerequisites() {
  local current_context
  local node_states

  print_step "Verificacoes"

  require_command docker
  require_command kubectl
  require_command python

  print_action "Validando Docker"
  if ! docker info >/dev/null 2>&1; then
    die "Docker nao esta acessivel; inicie o Docker Desktop e tente novamente"
  fi

  print_action "Validando contexto do Kubernetes"
  if ! current_context="$(kubectl config current-context 2>/dev/null)"; then
    die "nao foi possivel obter o contexto atual do Kubernetes"
  fi
  current_context="${current_context//$'\r'/}"

  if [[ "$current_context" != "$DOCKER_DESKTOP_CONTEXT" ]]; then
    die "contexto atual e '$current_context'; esperado '$DOCKER_DESKTOP_CONTEXT'"
  fi

  print_action "Validando no Ready"
  if ! node_states="$(
    kubectl get nodes \
      -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{range .status.conditions[?(@.type=="Ready")]}{.status}{end}{"\n"}{end}'
  )"; then
    die "nao foi possivel consultar os nos do cluster"
  fi
  node_states="${node_states//$'\r'/}"

  if ! awk -F '\t' '$2 == "True" { ready = 1 } END { exit ready ? 0 : 1 }' \
    <<<"$node_states"; then
    die "nenhum no do Docker Desktop esta em estado Ready"
  fi

  if [[ ! -f "k8s/secret.local.yaml" ]]; then
    die "k8s/secret.local.yaml nao existe; crie-o antes de executar o bootstrap"
  fi

  validate_manifest_files \
    "${NAMESPACE_MANIFESTS[@]}" \
    "${CONFIGURATION_MANIFESTS[@]}" \
    "${BASE_MANIFESTS[@]}" \
    "${OBSERVABILITY_MANIFESTS[@]}" \
    "${APPLICATION_MANIFESTS[@]}" \
    "${SCENARIO_MANIFESTS[@]}"

  print_action "Pre-requisitos validados no contexto $current_context"
}

# -----------------------------------------------------------------------------
# Imagem
# -----------------------------------------------------------------------------

build_application_image() {
  local repository_digest

  print_step "Imagem"
  print_action "Reconstruindo $APPLICATION_IMAGE"
  docker build -t "$APPLICATION_IMAGE" .

  repository_digest="$(
    docker image inspect "$APPLICATION_IMAGE" \
      --format '{{index .RepoDigests 0}}'
  )"
  repository_digest="${repository_digest//$'\r'/}"
  APPLICATION_IMAGE_DIGEST="${repository_digest##*@}"

  if [[ ! "$APPLICATION_IMAGE_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]]; then
    die "nao foi possivel resolver o digest da imagem reconstruida"
  fi

  APPLICATION_RUNTIME_IMAGE="${APPLICATION_IMAGE}@${APPLICATION_IMAGE_DIGEST}"
  APPLICATION_PRELOAD_IMAGE="${APPLICATION_IMAGE}-${APPLICATION_IMAGE_DIGEST#sha256:}"
  docker tag "$APPLICATION_IMAGE" "$APPLICATION_PRELOAD_IMAGE"
  print_action "Imagem reconstruida: $APPLICATION_RUNTIME_IMAGE"
}

# -----------------------------------------------------------------------------
# Componentes base
# -----------------------------------------------------------------------------

apply_base_components() {
  print_step "Componentes base"

  apply_manifests "${NAMESPACE_MANIFESTS[@]}"
  apply_manifests "${CONFIGURATION_MANIFESTS[@]}"
  apply_manifests "${BASE_MANIFESTS[@]}"

  wait_for_pvc postgres-data
  wait_for_pvc rabbitmq-data
  wait_for_rollout "$NAMESPACE" postgres
  wait_for_rollout "$NAMESPACE" rabbitmq
}

# -----------------------------------------------------------------------------
# Observabilidade
# -----------------------------------------------------------------------------

apply_observability() {
  print_step "Observabilidade"

  apply_manifests "${OBSERVABILITY_MANIFESTS[@]}"

  wait_for_rollout "$NAMESPACE" jaeger
  wait_for_rollout "$NAMESPACE" otel-collector
  wait_for_rollout "$NAMESPACE" prometheus
}

install_metrics_server() {
  local metrics_args

  print_step "Metrics Server $METRICS_SERVER_VERSION"
  print_action "Aplicando manifest oficial versionado"
  MSYS_NO_PATHCONV=1 kubectl apply -f "$METRICS_SERVER_URL"

  metrics_args="$(
    kubectl get deployment/metrics-server \
      -n kube-system \
      -o jsonpath='{range .spec.template.spec.containers[?(@.name=="metrics-server")].args[*]}{.}{"\n"}{end}'
  )"
  metrics_args="${metrics_args//$'\r'/}"

  if grep -Fxq -- '--kubelet-insecure-tls' <<<"$metrics_args"; then
    print_action "--kubelet-insecure-tls ja esta configurado"
  else
    print_action "Adicionando --kubelet-insecure-tls para Docker Desktop"
    MSYS_NO_PATHCONV=1 kubectl patch deployment/metrics-server \
      -n kube-system \
      --type=json \
      --patch='[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]'
  fi

  wait_for_rollout kube-system metrics-server

  print_action "Aguardando APIService de metricas"
  kubectl wait apiservice/v1beta1.metrics.k8s.io \
    --for=condition=Available \
    --timeout="$ROLLOUT_TIMEOUT"

  wait_for_metrics_command "metricas dos nos" kubectl top nodes
}

# -----------------------------------------------------------------------------
# Aplicacao
# -----------------------------------------------------------------------------

apply_application() {
  print_step "Aplicacao"

  if [[ "$SCENARIO" != "c2" && "$SCENARIO" != "c4" ]]; then
    print_action "Removendo HPA residual para preservar ${SCENARIO^^} sem autoscaling"
    kubectl delete hpa/api-hpa \
      -n "$NAMESPACE" \
      --ignore-not-found \
      --wait=true
  fi

  if [[ "$SCENARIO" != "c3" && "$SCENARIO" != "c4" ]]; then
    print_action "Removendo gateway residual para preservar ${SCENARIO^^}"
    kubectl delete deployment/gateway service/gateway configmap/gateway-config \
      -n "$NAMESPACE" \
      --ignore-not-found \
      --wait=true
  fi

  apply_manifests "${APPLICATION_MANIFESTS[@]}"

  if ((${#SCENARIO_MANIFESTS[@]} > 0)); then
    print_action "Aplicando manifests especificos do cenario"
    apply_manifests "${SCENARIO_MANIFESTS[@]}"
  else
    print_action "Nenhum manifest especifico do cenario ${SCENARIO^^}"
  fi

  if [[ "$SCENARIO" == "c3" || "$SCENARIO" == "c4" ]]; then
    print_action "Reiniciando gateway para carregar a configuracao DB-less aplicada"
    kubectl rollout restart deployment/gateway -n "$NAMESPACE"
  fi

  print_action "Garantindo API e worker com exatamente 1 replica"
  kubectl scale deployment/api deployment/worker -n "$NAMESPACE" --replicas=1

  preload_application_image

  print_action "Fixando API e worker no digest da imagem reconstruida"
  kubectl set image deployment/api \
    -n "$NAMESPACE" \
    "api=$APPLICATION_RUNTIME_IMAGE"
  kubectl set image deployment/worker \
    -n "$NAMESPACE" \
    "worker=$APPLICATION_RUNTIME_IMAGE"

  print_action "Reiniciando API e worker para recarregar imagem e ConfigMap"
  kubectl rollout restart deployment/api deployment/worker -n "$NAMESPACE"

  wait_for_rollout "$NAMESPACE" api
  wait_for_rollout "$NAMESPACE" worker
  wait_for_deployment_pods api app=api
  wait_for_deployment_pods worker app=worker

  assert_deployment_image api api
  assert_deployment_image worker worker
  assert_pod_image_id api api
  assert_pod_image_id worker worker

  if [[ "$SCENARIO" == "c3" || "$SCENARIO" == "c4" ]]; then
    wait_for_rollout "$NAMESPACE" gateway
    wait_for_deployment_pods gateway app=gateway
  fi
}

# -----------------------------------------------------------------------------
# Dados
# -----------------------------------------------------------------------------

prepare_experiment_data() {
  print_step "Dados"

  print_action "Criando produtos ausentes"
  kubectl exec deployment/api -n "$NAMESPACE" -- python seed_products.py

  print_action "Descartando pedidos experimentais e restaurando estoque"
  kubectl exec deployment/api -n "$NAMESPACE" -- python reset_demo_data.py

  print_action "Validando resumo e estoques"
  kubectl exec deployment/api -n "$NAMESPACE" -- \
    python export_experiment_db_summary.py --assert-stocks
}

validate_observability_configuration() {
  local deployment

  print_step "Validacao da observabilidade"

  for deployment in api worker; do
    assert_environment_value "$deployment" DIAGNOSTIC_LOGS 0
    assert_environment_value "$deployment" OTEL_ENABLED 1
    assert_environment_value "$deployment" OTEL_TRACE_SAMPLE_RATIO 0.01
    assert_trace_sample_ratio "$deployment"
  done
}

# -----------------------------------------------------------------------------
# Validacao final
# -----------------------------------------------------------------------------

show_final_status() {
  print_step "Validacao final"

  printf '\nNo:\n'
  kubectl get nodes -o wide

  printf '\nDeployments:\n'
  kubectl get deployments -n "$NAMESPACE" -o wide

  printf '\nPods:\n'
  kubectl get pods -n "$NAMESPACE" -o wide

  printf '\nServices:\n'
  kubectl get services -n "$NAMESPACE" -o wide

  printf '\nPVCs:\n'
  kubectl get pvc -n "$NAMESPACE"

  printf '\nUso atual por container:\n'
  wait_for_metrics_command \
    "metricas dos Pods por container" \
    kubectl top pods -n "$NAMESPACE" --containers

  if [[ "$SCENARIO" == "c2" || "$SCENARIO" == "c4" ]]; then
    printf '\nHPA:\n'
    kubectl get hpa/api-hpa -n "$NAMESPACE" -o wide
  fi

  if [[ "$SCENARIO" == "c3" || "$SCENARIO" == "c4" ]]; then
    printf '\nGateway e rate limiting:\n'
    kubectl get deployment/gateway service/gateway configmap/gateway-config \
      -n "$NAMESPACE" -o wide
  fi

  printf '\nAmbiente %s pronto para os testes de carga.\n' "${SCENARIO^^}"
}

main() {
  parse_arguments "$@"
  validate_prerequisites
  build_application_image
  apply_base_components
  apply_observability
  install_metrics_server
  apply_application
  prepare_experiment_data
  validate_observability_configuration
  validate_scenario_configuration
  show_final_status
}

main "$@"
