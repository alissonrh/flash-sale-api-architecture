# Entendendo a implantação Kubernetes da aplicação

Os arquivos da pasta [`k8s`](../k8s) descrevem como executar a arquitetura atual do Flash Sale dentro de um cluster Kubernetes.

Se o Docker Compose funciona como uma planta para subir vários containers em uma única máquina, o Kubernetes funciona como uma administradora de condomínio: recebe a descrição do estado desejado, cria os componentes, acompanha sua saúde e tenta corrigir diferenças entre o que foi pedido e o que está rodando.

Neste projeto, o estado desejado pode ser resumido assim:

```text
Usuário (fora do cluster)
        |
        | kubectl port-forward
        v
Service api:8000
        |
        v
Pod da API
   |          \
   |           \ publica checkout_requests
   v            v
PostgreSQL    RabbitMQ ---> Pod do worker
   ^                         |
   |_________________________|

API e worker ---> OTel Collector ---> Jaeger

Prometheus ---> GET api:8000/metrics
```

Todos esses recursos ficam no namespace `flash-sale`. Atualmente, os Services são do tipo `ClusterIP`, portanto nenhum componente é publicado diretamente para fora do cluster.

## 1. Kubernetes em termos simples

Antes de analisar cada arquivo, vale separar os principais objetos usados pelo projeto.

### Cluster

É o conjunto de máquinas administrado pelo Kubernetes. Em desenvolvimento, pode ser um cluster local do Docker Desktop, Minikube ou Kind. Em produção, poderia ser um serviço gerenciado em nuvem.

### Namespace

É uma divisão lógica dentro do cluster. Uma analogia útil é a de um bairro: podem existir ruas com nomes parecidos em bairros diferentes sem que sejam o mesmo endereço.

Neste projeto, todos os componentes moram no mesmo bairro:

```yaml
namespace: flash-sale
```

### Pod

É a menor unidade executada pelo Kubernetes. Neste projeto, cada Pod possui um container principal: API, worker, PostgreSQL, RabbitMQ, Collector, Jaeger ou Prometheus.

Um Pod é descartável. Se ele falhar, o Kubernetes pode substituí-lo por outro, possivelmente com outro nome e outro endereço IP.

### Deployment

É o gerente dos Pods. Ele declara quantas réplicas devem existir, qual imagem usar, quais variáveis entregar, quais recursos reservar e como verificar a saúde do processo.

Por exemplo:

```yaml
kind: Deployment
spec:
  replicas: 1
```

Isso significa: mantenha um Pod dessa aplicação em execução. Se o Pod desaparecer, o Deployment cria outro.

### Service

É um endereço estável na frente de Pods que podem ser substituídos. Pense nele como o número da recepção de uma empresa: os funcionários podem mudar de sala, mas quem liga continua usando o mesmo número.

É por isso que a API usa:

```text
postgres:5432
rabbitmq:5672
otel-collector:4317
```

Esses nomes são Services e são resolvidos pelo DNS interno do Kubernetes. A aplicação não precisa conhecer o IP de cada Pod.

### ConfigMap e Secret

Os dois armazenam configuração fora da imagem do container:

- `ConfigMap`: dados não sigilosos, como flags e endereços internos.
- `Secret`: credenciais, como usuário e senha.

Uma analogia é separar o manual de operação das chaves do prédio. O manual pode ser compartilhado; as chaves exigem mais cuidado.

### PersistentVolumeClaim

Um `PersistentVolumeClaim`, ou PVC, é um pedido de armazenamento persistente. O Pod pode ser trocado, mas o disco solicitado continua existindo e pode ser conectado ao novo Pod.

Sem PVC, trocar o Pod do PostgreSQL seria parecido com trocar um computador e esquecer o disco antigo: os dados desapareceriam junto com a máquina descartada.

## 2. Visão geral dos arquivos

Os manifests estão divididos por responsabilidade:

| Grupo | Arquivos | Função |
|---|---|---|
| Isolamento | `namespace.yaml` | Cria o namespace `flash-sale` |
| Configuração | `configmap.yaml`, `secret.example.yaml` | Entrega flags, endpoints e credenciais |
| Banco | `postgres-deployment.yaml`, `postgres-service.yaml`, `postgres-pvc.yaml` | Executa e persiste o PostgreSQL |
| Mensageria | `rabbitmq-deployment.yaml`, `rabbitmq-service.yaml`, `rabbitmq-pvc.yaml` | Executa e persiste o RabbitMQ |
| Aplicação | `api-deployment.yaml`, `api-service.yaml`, `worker-deployment.yaml` | Executa a API e o consumidor assíncrono |
| Tracing | `otel-collector-*`, `jaeger-*` | Recebe, encaminha e apresenta traces |
| Métricas | `prometheus-*` | Coleta e armazena temporariamente métricas da API |

O histórico do Git confirma que a infraestrutura foi construída nessa ordem conceitual:

```text
Namespace
  -> ConfigMap e Secret
  -> PostgreSQL
  -> RabbitMQ
  -> Collector e Jaeger
  -> API e worker
  -> Prometheus
```

Os commits de 28 de agosto de 2026 registram essas etapas separadamente:

| Commit | Alteração |
|---|---|
| `ae6d629` | `feat: add Kubernetes namespace` |
| `b6fff1d` | `feat: add Kubernetes application ConfigMap` |
| `1c6d138` | `feat: add Kubernetes Secret template` |
| `ef60463` | `feat: deploy PostgreSQL on Kubernetes` |
| `fb08934` | `feat: deploy RabbitMQ on Kubernetes` |
| `ae58fb4` | `feat: deploy tracing infrastructure on Kubernetes` |
| `81efdf2` | `feat: deploy API and worker on Kubernetes` |
| `ef8a18b` | `feat: deploy Prometheus on Kubernetes` |

Isso ajuda a entender que cada grupo de arquivos representa uma parte independente da arquitetura e documenta a intenção original das mudanças.

## 3. O namespace

O arquivo [`namespace.yaml`](../k8s/namespace.yaml) cria:

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: flash-sale
```

Depois disso, os outros manifests usam:

```yaml
metadata:
  namespace: flash-sale
```

Essa repetição garante que API, worker, banco e observabilidade sejam criados no mesmo espaço lógico e possam usar nomes DNS curtos, como `postgres` e `jaeger`.

Se o namespace for removido, todos os recursos namespaced que estão dentro dele também entram no processo de remoção. Por isso, apagar um namespace não equivale a apagar apenas uma pasta: é uma operação destrutiva sobre todo o ambiente.

## 4. Configuração compartilhada e segredos

### 4.1. `configmap.yaml`

O [`configmap.yaml`](../k8s/configmap.yaml) cria o `flash-sale-config`:

```yaml
data:
  DIAGNOSTIC_LOGS: '1'
  OTEL_ENABLED: '1'
  OTEL_EXPORTER_OTLP_ENABLED: '1'
  OTEL_EXPORTER_OTLP_ENDPOINT: 'http://otel-collector:4317'
```

Na API e no worker, ele é importado com:

```yaml
envFrom:
  - configMapRef:
      name: flash-sale-config
```

Na prática, cada chave vira uma variável de ambiente dentro do container.

O fluxo de tracing fica assim:

```text
API/worker lê OTEL_EXPORTER_OTLP_ENDPOINT
            |
            v
http://otel-collector:4317
            |
            v
Service encontra o Pod do Collector
```

O prefixo `http://` aparece porque a biblioteca recebe um endpoint OTLP. A comunicação usada pela configuração é OTLP sobre gRPC na porta `4317`.

### 4.2. `secret.example.yaml`

O [`secret.example.yaml`](../k8s/secret.example.yaml) é apenas um molde:

```yaml
stringData:
  POSTGRES_USER: replace-me
  POSTGRES_PASSWORD: replace-me
  RABBITMQ_DEFAULT_USER: replace-me
  RABBITMQ_DEFAULT_PASS: replace-me
```

Ele não contém credenciais válidas. A ideia é copiá-lo para `secret.local.yaml`, substituir os valores e manter o arquivo local fora do Git. O `.gitignore` já ignora:

```text
k8s/secret.local.yaml
```

Exemplo em PowerShell:

```powershell
Copy-Item k8s/secret.example.yaml k8s/secret.local.yaml
```

Depois, os quatro valores devem ser editados antes da aplicação do Secret.

Um `Secret` do Kubernetes não deve ser tratado como um cofre por si só. Os valores podem ser representados em base64 pela API do Kubernetes, mas base64 é codificação, não criptografia. Em um ambiente real, também seriam necessários controles de acesso, criptografia do cluster e possivelmente integração com um gerenciador externo de segredos.

### 4.3. Como API e worker montam suas URLs

Os Deployments primeiro carregam as variáveis individuais do Secret:

```yaml
- name: POSTGRES_USER
  valueFrom:
    secretKeyRef:
      name: flash-sale-secrets
      key: POSTGRES_USER
```

Depois montam valores derivados:

```yaml
- name: DATABASE_URL
  value: postgresql+psycopg://$(POSTGRES_USER):$(POSTGRES_PASSWORD)@postgres:5432/flash_sale_db

- name: RABBITMQ_URL
  value: amqp://$(RABBITMQ_DEFAULT_USER):$(RABBITMQ_DEFAULT_PASS)@rabbitmq:5672/
```

Como as variáveis referenciadas aparecem antes dessas URLs, o Kubernetes expande `$(POSTGRES_USER)` e os demais valores ao criar o container.

Com credenciais fictícias, o resultado seria parecido com:

```text
postgresql+psycopg://app:senha@postgres:5432/flash_sale_db
amqp://app:senha@rabbitmq:5672/
```

## 5. PostgreSQL: Deployment, Service e PVC

O PostgreSQL usa três recursos que trabalham juntos:

```text
postgres-service.yaml
        |
        | seleciona app=postgres
        v
postgres-deployment.yaml
        |
        | monta o claim
        v
postgres-pvc.yaml
```

### 5.1. Deployment

O [`postgres-deployment.yaml`](../k8s/postgres-deployment.yaml) executa `postgres:18.3` com uma réplica.

O trecho abaixo conecta o Deployment ao armazenamento:

```yaml
volumeMounts:
  - name: postgres-storage
    mountPath: /var/lib/postgresql

volumes:
  - name: postgres-storage
    persistentVolumeClaim:
      claimName: postgres-data
```

O Deployment usa a estratégia:

```yaml
strategy:
  type: Recreate
```

Isso evita manter o Pod antigo e o novo ativos ao mesmo tempo durante uma atualização. Para um banco com volume `ReadWriteOnce`, é uma escolha coerente nesta implantação simples: primeiro encerra-se a instância antiga, depois inicia-se a nova.

### 5.2. Service

O [`postgres-service.yaml`](../k8s/postgres-service.yaml) procura Pods com:

```yaml
selector:
  app: postgres
```

O Deployment coloca exatamente esse label no Pod:

```yaml
labels:
  app: postgres
```

Essa igualdade é o vínculo entre Service e Pod. O Service expõe a porta `5432`, então `postgres:5432` se torna o endereço interno estável usado pela API e pelo worker.

### 5.3. PVC

O [`postgres-pvc.yaml`](../k8s/postgres-pvc.yaml) solicita:

```yaml
accessModes:
  - ReadWriteOnce
resources:
  requests:
    storage: 1Gi
```

`ReadWriteOnce` significa que o volume pode ser montado para leitura e escrita por um nó de cada vez. A entrega efetiva desse disco depende de o cluster possuir uma `StorageClass` padrão ou outro provisionamento compatível.

### 5.4. Probes do banco

As probes executam:

```sh
pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"
```

- `readinessProbe`: decide quando o Pod está pronto para receber conexões pelo Service.
- `livenessProbe`: detecta quando o processo deixou de responder e deve ser reiniciado.

A analogia é a de um restaurante: readiness verifica se ele já abriu as portas; liveness verifica se ele continua funcionando depois de aberto.

## 6. RabbitMQ: fila e persistência

O RabbitMQ repete a mesma composição:

```text
rabbitmq-service.yaml
        |
        v
rabbitmq-deployment.yaml
        |
        v
rabbitmq-pvc.yaml
```

O [`rabbitmq-deployment.yaml`](../k8s/rabbitmq-deployment.yaml) usa a imagem `rabbitmq:4.3-management` e oferece duas portas:

| Porta | Uso |
|---|---|
| `5672` | Protocolo AMQP usado pela API e pelo worker |
| `15672` | Interface administrativa do RabbitMQ |

A API publica pedidos na fila durável `checkout_requests`, e o worker consome essa fila.

Uma analogia é uma cozinha:

```text
API = atendente que registra o pedido
RabbitMQ = balcão onde as comandas aguardam
worker = cozinheiro que retira e processa cada comanda
```

Isso permite que a API responda sem executar todo o processamento crítico no mesmo instante.

O volume `rabbitmq-data`, solicitado em [`rabbitmq-pvc.yaml`](../k8s/rabbitmq-pvc.yaml), é montado em `/var/lib/rabbitmq`. Além disso, as mensagens são publicadas como persistentes e a fila é declarada como durável no código. Essas camadas reduzem o risco de perder a fila quando o Pod é substituído, embora persistência não substitua backup nem configuração de alta disponibilidade.

A probe usa:

```text
rabbitmq-diagnostics -q ping
```

O [`rabbitmq-service.yaml`](../k8s/rabbitmq-service.yaml) fornece os endereços internos `rabbitmq:5672` e `rabbitmq:15672`.

## 7. A API

O [`api-deployment.yaml`](../k8s/api-deployment.yaml) usa a imagem local:

```yaml
image: flash-sale-api:k8s
imagePullPolicy: IfNotPresent
```

Essa imagem deve existir nos nós do cluster ou estar disponível em um registry. Construí-la apenas no Docker do computador não garante que um cluster Kind ou Minikube separado consiga enxergá-la; nesses casos, é necessário carregar a imagem no cluster ou publicar em um registry.

O container executa:

```yaml
command:
  - ./start-api.sh
```

O script real faz quatro coisas:

1. tenta conectar ao PostgreSQL até ele responder;
2. cria as tabelas;
3. aplica a migração existente de `orders`;
4. inicia o Uvicorn na porta `8000`.

No Kubernetes não existe um `depends_on` como no Compose. Em vez de depender de uma ordem perfeita de inicialização, a aplicação precisa tolerar que outra peça ainda não esteja pronta. O laço do `start-api.sh` faz isso para o PostgreSQL.

### 7.1. As três probes da API

As probes acessam `GET /health`, que atualmente responde:

```json
{"status":"ok"}
```

Elas têm papéis diferentes:

```text
startupProbe   -> a aplicação já terminou de iniciar?
readinessProbe -> ela deve receber tráfego agora?
livenessProbe  -> ela ainda está viva ou deve ser reiniciada?
```

A `startupProbe` permite até 24 falhas em intervalos de 5 segundos, aproximadamente 120 segundos de tolerância. Isso é útil porque o script pode estar aguardando o banco ou executando a inicialização antes de abrir a porta HTTP.

Um ponto importante: o `/health` atual confirma que o processo HTTP responde, mas não testa PostgreSQL nem RabbitMQ. Portanto, a API pode aparecer saudável mesmo se uma dependência ficar indisponível depois da inicialização.

### 7.2. Requests e limits

A API declara:

```yaml
requests:
  cpu: 100m
  memory: 256Mi
limits:
  cpu: 500m
  memory: 512Mi
```

`requests` é a reserva usada pelo scheduler para escolher um nó. `limits` é o teto permitido.

Uma analogia é reservar uma mesa para duas pessoas com capacidade máxima para quatro: a reserva mínima ajuda no planejamento, e o máximo evita que um único cliente ocupe o salão inteiro.

`100m` de CPU equivale a 0,1 CPU; `500m` equivale a 0,5 CPU. Se o processo ultrapassar o limite de memória, ele pode ser encerrado por falta de memória. Se ultrapassar o limite de CPU, tende a ser limitado, não necessariamente encerrado.

### 7.3. Service da API

O [`api-service.yaml`](../k8s/api-service.yaml) seleciona `app: api` e oferece a porta `8000` apenas dentro do cluster.

O Prometheus usa esse Service para acessar:

```text
http://api:8000/metrics
```

Para um usuário fora do cluster, ainda não existe Ingress, `LoadBalancer` ou `NodePort`. Em desenvolvimento, o acesso pode ser feito com `port-forward`.

## 8. O worker

O [`worker-deployment.yaml`](../k8s/worker-deployment.yaml) usa a mesma imagem da API:

```yaml
image: flash-sale-api:k8s
```

O que muda é o comando:

```yaml
command:
  - ./start-worker.sh
```

Isso ilustra uma vantagem comum de imagens reutilizáveis: o mesmo pacote de código pode assumir papéis diferentes de acordo com o comando de inicialização.

O script do worker:

1. espera o PostgreSQL;
2. espera o RabbitMQ;
3. inicia `app.workers.checkout_worker`.

O worker não precisa de Service porque nenhum outro componente inicia conexões para ele. É o próprio worker que abre conexões de saída para RabbitMQ, PostgreSQL e Collector.

```text
Service é necessário para ser encontrado
worker apenas procura outros serviços
logo, nesta arquitetura, worker não precisa de Service
```

Ele recebe `OTEL_SERVICE_NAME=flash-sale-worker`, enquanto a API recebe `flash-sale-api`. Assim, o Jaeger consegue distinguir os spans produzidos pelos dois processos.

Atualmente, o worker não possui startup, readiness ou liveness probe. Se o processo principal encerrar, o container será reiniciado pelo Deployment. Porém, se ele permanecer vivo e travado sem consumir mensagens, não há uma probe específica para detectar essa situação.

## 9. Tracing: Collector e Jaeger

O caminho completo dos traces no Kubernetes é:

```text
Pod da API --------------------\
                                > otel-collector:4317
Pod do worker -----------------/            |
                                            v
                                      Pod do Collector
                                            |
                                            | jaeger:4317
                                            v
                                       Pod do Jaeger
                                            |
                                            v
                                      interface :16686
```

### 9.1. Configuração do Collector

O [`otel-collector-configmap.yaml`](../k8s/otel-collector-configmap.yaml) guarda um arquivo inteiro dentro de uma chave do ConfigMap:

```yaml
data:
  config.yaml: |
```

Esse arquivo define três estágios:

```text
receiver -> processor -> exporter
receber     agrupar      encaminhar
```

O receiver escuta OTLP gRPC em todas as interfaces do container:

```yaml
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
```

O processor agrupa spans em lotes:

```yaml
processors:
  batch: {}
```

O exporter envia para o Service do Jaeger:

```yaml
exporters:
  otlp_grpc/jaeger:
    endpoint: jaeger:4317
    tls:
      insecure: true
```

Finalmente, o pipeline conecta essas peças:

```yaml
pipelines:
  traces:
    receivers: [otlp]
    processors: [batch]
    exporters: [otlp_grpc/jaeger]
```

Esse pipeline aceita somente **traces**. Apesar de o OpenTelemetry também poder transportar métricas e logs, a configuração atual não possui pipelines para esses sinais.

### 9.2. Deployment e volume do Collector

O [`otel-collector-deployment.yaml`](../k8s/otel-collector-deployment.yaml) transforma o ConfigMap em arquivos dentro do Pod:

```yaml
volumeMounts:
  - name: collector-config
    mountPath: /etc/otelcol

volumes:
  - name: collector-config
    configMap:
      name: otel-collector-config
```

O argumento:

```yaml
--config=/etc/otelcol/config.yaml
```

diz ao Collector onde ler a configuração montada.

O [`otel-collector-service.yaml`](../k8s/otel-collector-service.yaml) cria o endereço `otel-collector:4317`, utilizado por API e worker.

### 9.3. Jaeger

O [`jaeger-deployment.yaml`](../k8s/jaeger-deployment.yaml) oferece:

- `4317`: entrada OTLP gRPC usada pelo Collector;
- `16686`: interface web para pesquisar traces.

O [`jaeger-service.yaml`](../k8s/jaeger-service.yaml) publica as duas portas apenas dentro do cluster.

O Collector é semelhante a uma central de distribuição: recebe pacotes de vários remetentes e decide para qual destino encaminhá-los. O Jaeger é o arquivo consultável onde os pacotes de tracing são organizados e visualizados.

Nesta implantação, o Jaeger não possui PVC. Seus traces não devem ser considerados persistentes após recriações do Pod.

## 10. Métricas com Prometheus

O Prometheus segue um modelo diferente do tracing.

No tracing, a aplicação envia dados ao Collector:

```text
API/worker --envia--> Collector --envia--> Jaeger
```

Nas métricas, o Prometheus visita periodicamente a API:

```text
Prometheus --GET /metrics--> API
```

### 10.1. ConfigMap do Prometheus

O [`prometheus-configmap.yaml`](../k8s/prometheus-configmap.yaml) define:

```yaml
global:
  scrape_interval: 5s
  evaluation_interval: 5s

scrape_configs:
  - job_name: flash-sale-api
    metrics_path: /metrics
    static_configs:
      - targets:
          - api:8000
```

A cada cinco segundos, o Prometheus consulta `api:8000/metrics`. Esse endpoint é criado pelo `prometheus-fastapi-instrumentator` em `app/main.py`.

O Prometheus não recebe essas métricas pelo OTel Collector. São dois caminhos independentes.

### 10.2. Deployment e armazenamento

O [`prometheus-deployment.yaml`](../k8s/prometheus-deployment.yaml) monta a configuração em:

```text
/etc/prometheus/prometheus.yml
```

Os dados são gravados em `/prometheus`, mas o volume atual é:

```yaml
- name: prometheus-data
  emptyDir: {}
```

`emptyDir` vive junto com o Pod. Reiniciar apenas o container pode preservar o diretório enquanto o Pod existe, mas substituir ou remover o Pod elimina os dados. Para histórico durável, seria necessário um PVC ou armazenamento remoto.

O [`prometheus-service.yaml`](../k8s/prometheus-service.yaml) oferece a interface na porta `9090` dentro do cluster.

## 11. Como um checkout percorre o cluster

Considere esta requisição real da aplicação:

```http
POST /checkout
Content-Type: application/json

{
  "product_id": 1,
  "quantity": 1
}
```

O caminho é:

1. O acesso local encaminhado chega ao Service `api`.
2. O Service encontra o Pod com label `app: api`.
3. A API consulta `postgres:5432` pelo Service do PostgreSQL.
4. A API registra o pedido e publica uma mensagem em `rabbitmq:5672`.
5. O worker consome a mensagem `checkout_requests`.
6. O worker consulta e atualiza pedido e estoque no PostgreSQL.
7. API e worker enviam spans para `otel-collector:4317`.
8. O Collector agrupa e envia os spans para `jaeger:4317`.
9. O Prometheus consulta periodicamente as métricas HTTP em `api:8000/metrics`.

Em termos de analogia:

```text
Service       = recepção/endereço fixo
Pod da API    = atendente
RabbitMQ      = balcão de comandas
worker        = cozinheiro
PostgreSQL    = livro-caixa e estoque
Collector     = central de distribuição de telemetria
Jaeger        = arquivo das trajetórias
Prometheus    = fiscal que anota indicadores a cada 5 segundos
Kubernetes    = administração que mantém cada posto ocupado
```

## 12. Labels, selectors e portas nomeadas

O Kubernetes conecta vários desses recursos por labels.

Na API:

```yaml
# Pod criado pelo Deployment
labels:
  app: api

# Service
selector:
  app: api
```

Se esses valores forem diferentes, o Service existe, mas não encontra nenhum endpoint.

As portas também possuem nomes:

```yaml
# Deployment
ports:
  - name: http
    containerPort: 8000

# Service
ports:
  - port: 8000
    targetPort: http
```

Usar `targetPort: http` cria uma referência ao nome da porta do container. Isso torna a intenção mais clara e permite alterar o número interno mantendo a referência nominal, desde que os manifests permaneçam coerentes.

## 13. Ordem segura de implantação local

Primeiro, a imagem da aplicação precisa ser construída:

```powershell
docker build -t flash-sale-api:k8s .
```

Dependendo do cluster local, pode ser necessário carregar essa imagem. Exemplos:

```powershell
# Kind
kind load docker-image flash-sale-api:k8s

# Minikube
minikube image load flash-sale-api:k8s
```

Depois, crie e edite o Secret local:

```powershell
Copy-Item k8s/secret.example.yaml k8s/secret.local.yaml
```

Uma aplicação explícita evita misturar o arquivo de exemplo com o Secret real:

```powershell
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/configmap.yaml
kubectl apply -f k8s/secret.local.yaml

kubectl apply -f k8s/postgres-pvc.yaml
kubectl apply -f k8s/postgres-deployment.yaml
kubectl apply -f k8s/postgres-service.yaml

kubectl apply -f k8s/rabbitmq-pvc.yaml
kubectl apply -f k8s/rabbitmq-deployment.yaml
kubectl apply -f k8s/rabbitmq-service.yaml

kubectl apply -f k8s/jaeger-deployment.yaml
kubectl apply -f k8s/jaeger-service.yaml
kubectl apply -f k8s/otel-collector-configmap.yaml
kubectl apply -f k8s/otel-collector-deployment.yaml
kubectl apply -f k8s/otel-collector-service.yaml

kubectl apply -f k8s/api-deployment.yaml
kubectl apply -f k8s/api-service.yaml
kubectl apply -f k8s/worker-deployment.yaml

kubectl apply -f k8s/prometheus-configmap.yaml
kubectl apply -f k8s/prometheus-deployment.yaml
kubectl apply -f k8s/prometheus-service.yaml
```

Não é recomendável executar diretamente:

```powershell
kubectl apply -f k8s
```

Isso também pode aplicar `secret.example.yaml`, cujo nome de recurso é o mesmo do Secret real e cujos valores são `replace-me`. O `.gitignore` impede o commit de `secret.local.yaml`, mas não impede que o `kubectl` leia o arquivo existente no diretório.

Não é obrigatório aguardar manualmente cada aplicação antes de enviar a próxima. O Kubernetes aceita os estados desejados, enquanto probes e scripts de retry ajudam os componentes a convergir. Ainda assim, aplicar em grupos facilita identificar erros.

## 14. Como verificar o ambiente

Veja todos os recursos principais:

```powershell
kubectl get all -n flash-sale
kubectl get pvc -n flash-sale
kubectl get configmap -n flash-sale
kubectl get secret -n flash-sale
```

Acompanhe os Pods:

```powershell
kubectl get pods -n flash-sale -w
```

Consulte os logs:

```powershell
kubectl logs -n flash-sale deployment/api
kubectl logs -n flash-sale deployment/worker
kubectl logs -n flash-sale deployment/otel-collector
```

Se um Pod não iniciar, estes comandos ajudam a explicar eventos, probes, imagem e volumes:

```powershell
kubectl describe pod -n flash-sale <nome-do-pod>
kubectl get events -n flash-sale --sort-by=.metadata.creationTimestamp
```

### Acessar a API

Em um terminal:

```powershell
kubectl port-forward -n flash-sale service/api 8000:8000
```

Em outro terminal:

```powershell
Invoke-RestMethod http://localhost:8000/health
Invoke-RestMethod http://localhost:8000/products
```

Para enviar um checkout:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://localhost:8000/checkout `
  -ContentType application/json `
  -Body '{"product_id":1,"quantity":1}'
```

### Acessar o Jaeger

```powershell
kubectl port-forward -n flash-sale service/jaeger 16686:16686
```

Depois, abra:

```text
http://localhost:16686
```

Procure pelos serviços `flash-sale-api` e `flash-sale-worker`.

### Acessar o Prometheus

```powershell
kubectl port-forward -n flash-sale service/prometheus 9090:9090
```

Depois, abra:

```text
http://localhost:9090
```

Em **Status > Targets**, o job `flash-sale-api` deve aparecer como `UP`.

### Acessar a interface do RabbitMQ

```powershell
kubectl port-forward -n flash-sale service/rabbitmq 15672:15672
```

Depois, abra:

```text
http://localhost:15672
```

Use as credenciais definidas no Secret local.

## 15. O que acontece quando algo falha

### Pod da API é removido

O Deployment percebe que existe menos de uma réplica e cria outro Pod. O Service passa a encaminhar para o novo Pod quando sua readiness probe fica saudável.

### Pod do PostgreSQL é substituído

O novo Pod monta novamente o PVC `postgres-data`. A intenção é preservar os dados mesmo com a troca do Pod.

### Pod do RabbitMQ é substituído

O novo Pod monta `rabbitmq-data`. As mensagens duráveis podem sobreviver à troca, desde que tenham sido persistidas corretamente no volume.

### Collector fica indisponível

A operação de negócio pode continuar, mas spans podem deixar de ser exportados ou ser perdidos após os limites de buffer e retry do SDK. O tracing não deve ser uma dependência obrigatória para concluir o checkout.

### Worker fica indisponível

A API ainda pode publicar mensagens no RabbitMQ. Elas aguardam na fila até o consumidor voltar, respeitadas a persistência e a capacidade do broker. O pedido assíncrono demora mais para chegar ao estado final.

### Prometheus fica indisponível

A API continua respondendo. O efeito é uma lacuna na coleta de métricas e, como o armazenamento atual é efêmero, possível perda do histórico local.

## 16. Configuração alterada não significa Pod reiniciado

Quando `flash-sale-config` é usado como variáveis de ambiente por `envFrom`, atualizar o ConfigMap não modifica as variáveis de Pods já existentes. É necessário recriar ou reiniciar os Deployments consumidores:

```powershell
kubectl rollout restart deployment/api deployment/worker -n flash-sale
```

O Collector e o Prometheus recebem arquivos montados a partir de ConfigMaps. O arquivo montado pode ser atualizado pelo Kubernetes com algum atraso, mas isso não garante que cada processo recarregue automaticamente sua configuração. Uma reinicialização explícita torna a aplicação da mudança previsível:

```powershell
kubectl rollout restart deployment/otel-collector deployment/prometheus -n flash-sale
```

Depois, acompanhe:

```powershell
kubectl rollout status deployment/api -n flash-sale
kubectl rollout status deployment/worker -n flash-sale
```

## 17. Limitações e próximos passos

Os manifests atuais formam uma implantação local coerente, mas ainda são uma base inicial.

### Acesso externo

Todos os Services são `ClusterIP`. Não existem Ingress, Gateway, Kong, `LoadBalancer` ou certificado TLS nos arquivos atuais. O acesso externo depende de `port-forward`.

### Escalabilidade

API e worker têm `replicas: 1`. Não existem manifests de HPA ou KEDA. Portanto, a descrição mais ampla do README sobre HPA, KEDA, Kong e Grafana representa a arquitetura pretendida, não os recursos presentes hoje na pasta `k8s`.

### Componentes com estado

PostgreSQL e RabbitMQ estão em Deployments de uma réplica. Para um ambiente de produção, StatefulSets, operadores, backups, replicação e políticas de recuperação merecem avaliação. Um PVC preserva dados contra a troca comum de Pod, mas não resolve sozinho falha de disco, exclusão acidental ou desastre do cluster.

### Persistência da observabilidade

O Jaeger não tem volume declarado. O Prometheus usa `emptyDir`. Traces e séries históricas não devem ser tratados como duráveis nesta versão.

### Saúde da aplicação

O endpoint `/health` da API é superficial e não valida dependências. O worker não possui probes. Uma evolução poderia separar verificações de processo, prontidão e dependências, sem reiniciar a aplicação desnecessariamente por falhas externas transitórias.

### Migrações e múltiplas réplicas

O `start-api.sh` executa criação de tabelas e migração antes de iniciar o servidor. Com uma réplica isso é simples. Ao escalar a API, vários Pods poderiam tentar migrar simultaneamente. Um `Job`, init container controlado ou ferramenta formal de migração seria mais seguro.

### Segurança

Não há `NetworkPolicy`, `securityContext`, ServiceAccounts específicos, políticas de admissão ou TLS entre Collector e Jaeger. O exporter usa `insecure: true`. Esses pontos podem ser aceitáveis em laboratório local, mas precisam de revisão para produção.

### Atualização de imagens

`IfNotPresent` combinado com uma tag reutilizada como `k8s` pode manter uma versão antiga já existente no nó. Para desenvolvimento, pode ser necessário carregar novamente a imagem e reiniciar o Deployment. Para uma entrega controlada, tags imutáveis ou digestos deixam a versão explícita.

### Recursos e testes de carga

Os requests e limits atuais são valores iniciais, não evidência de capacidade. Os testes do projeto podem ajudar a calibrá-los. Antes de adicionar autoscaling, também é necessário definir métricas, metas e comportamento esperado sob saturação.

## 18. Comparação com Docker Compose

| Docker Compose | Kubernetes neste projeto |
|---|---|
| `service` | Deployment + Service |
| nome do serviço no DNS | nome do Service no DNS interno |
| `environment` | `env`, ConfigMap e Secret |
| volume nomeado | PVC montado no Pod |
| `depends_on` | probes, retry da aplicação e reconciliação |
| `ports` no host | Service e, localmente, `port-forward` |
| `restart` | controlador do Deployment mantém réplicas |

O Compose descreve containers e sua execução conjunta. O Kubernetes separa responsabilidades em objetos menores. Isso aumenta a quantidade de YAML, mas permite atualizar, observar, substituir e escalar cada parte de modo independente.

## 19. Resumo final

A pasta `k8s` transforma a aplicação em um conjunto de estados desejados:

- o namespace organiza o ambiente;
- ConfigMap e Secret fornecem configuração;
- Deployments mantêm os processos em execução;
- Services criam endereços internos estáveis;
- PVCs preservam os dados do PostgreSQL e RabbitMQ;
- probes informam quando vários componentes estão prontos ou precisam ser reiniciados;
- API e worker dividem a mesma imagem, mas executam comandos diferentes;
- o OTel Collector recebe traces e os encaminha ao Jaeger;
- o Prometheus coleta `/metrics` diretamente da API;
- `port-forward` permite acessar localmente as interfaces que continuam privadas no cluster.

O princípio central é que a aplicação não conversa com Pods específicos. Ela conversa com endereços estáveis como `postgres`, `rabbitmq`, `otel-collector` e `jaeger`, enquanto o Kubernetes administra os Pods que existem atrás desses nomes.
