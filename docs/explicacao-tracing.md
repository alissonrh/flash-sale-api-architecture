# Entendendo o sistema de tracing da aplicação

O sistema de tracing está conceitualmente organizado da seguinte forma: a aplicação produz *spans* com OpenTelemetry, envia esses spans para o OpenTelemetry Collector e o Collector encaminha tudo ao Jaeger, onde é possível visualizar o caminho completo de uma requisição.

O arquivo [`tracing.py`](../app/observability/tracing.py) é o centro dessa configuração, mas ele funciona em conjunto com o FastAPI, SQLAlchemy, Pika, worker, Docker Compose e Jaeger.

## 1. O que é um trace?

Imagine que um cliente faça a seguinte requisição:

```http
POST /checkout
```

Essa operação passa por várias etapas:

1. O FastAPI recebe a requisição.
2. A API consulta o produto no PostgreSQL.
3. A API cria o pedido.
4. A API publica uma mensagem no RabbitMQ.
5. O worker consome a mensagem.
6. O worker consulta o pedido e o produto.
7. O worker atualiza o estoque e o pedido.

Com tracing, essas operações são representadas como uma árvore:

```text
Trace: processamento completo do checkout
│
├── POST /checkout                         [API]
│   ├── SELECT product                     [PostgreSQL]
│   ├── INSERT order                       [PostgreSQL]
│   └── publish checkout_requests          [RabbitMQ]
│
└── consume checkout_requests              [Worker]
    └── worker.process_checkout
        ├── SELECT order                    [PostgreSQL]
        ├── UPDATE order → PROCESSING       [PostgreSQL]
        ├── SELECT product                  [PostgreSQL]
        └── UPDATE order/product            [PostgreSQL]
```

O conjunto inteiro é um **trace**. Cada operação individual é um **span**.

Todos os spans do mesmo fluxo possuem o mesmo `trace_id`. Cada span possui seu próprio `span_id`.

## 2. O papel de cada componente

```text
API / Worker
     │
     │ OTLP gRPC :4317
     ▼
OpenTelemetry Collector
     │
     │ OTLP gRPC :4317
     ▼
Jaeger
     │
     ▼
Interface web :16686
```

### OpenTelemetry

OpenTelemetry é o padrão e o conjunto de bibliotecas usados para criar e exportar informações de tracing.

Ele não é, neste projeto, a tela onde os traces são consultados. Ele produz os dados.

### OpenTelemetry Collector

O Collector é um intermediário. Ele recebe telemetria da API e do worker, processa os dados em lotes e os encaminha ao Jaeger.

Sua principal vantagem é desacoplar a aplicação do destino final. No futuro, seria possível trocar o Jaeger por outro backend, como Grafana Tempo, alterando principalmente a configuração do Collector, sem reescrever toda a instrumentação da aplicação.

### Jaeger

O Jaeger é o backend e a interface de consulta dos traces.

É nele que se procura uma requisição e se visualizam os spans, tempos, relações entre API e worker e atributos relacionados ao pedido.

## 3. Entendendo o `tracing.py`

O arquivo principal é [`app/observability/tracing.py`](../app/observability/tracing.py).

### 3.1. As duas chaves de ativação

```python
def otel_enabled() -> bool:
    return os.getenv("OTEL_ENABLED", "0").strip().lower() in _TRUE_VALUES
```

`OTEL_ENABLED` controla se o OpenTelemetry será configurado e se as instrumentações serão ativadas.

No Compose:

```yaml
OTEL_ENABLED: "1"
```

A segunda chave é lida por:

```python
def otlp_export_enabled() -> bool:
```

Ela consulta:

```yaml
OTEL_EXPORTER_OTLP_ENABLED: "1"
```

A diferença é importante:

- `OTEL_ENABLED=0`: não instrumenta FastAPI, banco nem RabbitMQ.
- `OTEL_ENABLED=1` e `OTEL_EXPORTER_OTLP_ENABLED=0`: cria spans, mas não os envia.
- As duas variáveis em `1`: cria e envia os spans ao Collector.

Antes da alteração atual do Compose, a exportação estava em `0`. Agora ela está em `1`, portanto os dados podem sair da aplicação.

### 3.2. `configure_tracing()`

A função começa criando um `Resource`:

```python
resource = Resource.create(
    {
        "service.name": service_name,
    }
)
```

Um resource descreve quem está gerando a telemetria.

Na API:

```yaml
OTEL_SERVICE_NAME: flash-sale-api
```

No worker:

```yaml
OTEL_SERVICE_NAME: flash-sale-worker
```

Por isso o Jaeger consegue diferenciar os dois serviços, mesmo que ambos usem o mesmo `tracing.py`.

Depois é criado o provider:

```python
provider = TracerProvider(resource=resource)
```

O `TracerProvider` pode ser entendido como o motor central do tracing dentro daquele processo. É ele que fornece tracers e administra os spans.

### 3.3. O exporter OTLP

Quando a exportação está ativa, é criado:

```python
exporter = OTLPSpanExporter(
    endpoint=endpoint,
    insecure=True,
)
```

O endpoint atual é:

```text
http://otel-collector:4317
```

Dentro da rede do Docker, `otel-collector` é o nome DNS do container. A porta `4317` é a porta padrão usada aqui pelo protocolo OTLP com gRPC.

`insecure=True` significa que a comunicação não usa TLS. Para comunicação interna desse ambiente de desenvolvimento isso é aceitável. Em produção, dependendo da rede, essa decisão precisaria ser reavaliada.

### 3.4. `BatchSpanProcessor`

```python
provider.add_span_processor(
    BatchSpanProcessor(exporter)
)
```

O processor recebe spans finalizados e os entrega ao exporter.

Ele trabalha em lotes para evitar uma chamada de rede sempre que um span termina:

```text
span 1 ─┐
span 2 ─┼─ lote ──> Collector
span 3 ─┘
```

Isso reduz o impacto do tracing no desempenho da aplicação. Uma consequência é que uma interrupção muito abrupta do container pode perder spans que ainda estejam no buffer.

### 3.5. Provider global

```python
trace.set_tracer_provider(provider)
```

Essa instrução registra o provider como padrão daquele processo Python.

A partir daí, quando uma biblioteca pede um tracer, ela utiliza essa configuração e, consequentemente, o mesmo exporter e o mesmo `service.name`.

## 4. Instrumentação automática

As funções seguintes habilitam integrações prontas do OpenTelemetry.

### 4.1. FastAPI

```python
FastAPIInstrumentor.instrument_app(...)
```

Isso faz o FastAPI criar spans automaticamente para requisições HTTP, como:

```text
POST /checkout
GET /orders/{order_id}
GET /products
```

O span normalmente contém informações como método HTTP, rota, status e duração.

A instrumentação é ligada em [`app/main.py`](../app/main.py):

```python
configure_tracing()
instrument_sqlalchemy(engine)
instrument_pika()
```

Depois que o objeto FastAPI é criado:

```python
instrument_fastapi(app)
```

A ordem está correta: primeiro configura-se o provider e depois as bibliotecas são instrumentadas.

### 4.2. SQLAlchemy

```python
SQLAlchemyInstrumentor().instrument(engine=engine, ...)
```

Essa integração intercepta as operações executadas pelo engine do SQLAlchemy.

Assim, chamadas como `db.execute()`, `db.commit()` e `db.refresh()` podem produzir spans de banco de dados, permitindo enxergar quanto tempo foi gasto no PostgreSQL.

Isso vale para a API e para o worker, pois os dois chamam `instrument_sqlalchemy(engine)`.

### 4.3. Pika e RabbitMQ

```python
PikaInstrumentor().instrument(...)
```

Pika é a biblioteca utilizada para conversar com o RabbitMQ.

Sua instrumentação acompanha a publicação e o consumo das mensagens. Ela também propaga o contexto do trace nos headers da mensagem.

Essa propagação permite associar:

```text
requisição HTTP → publicação no RabbitMQ → processamento no worker
```

Sem propagação, o worker produziria outro trace independente e haveria duas histórias separadas no Jaeger.

## 5. O span manual do worker

No [`checkout_worker.py`](../app/workers/checkout_worker.py), existe:

```python
tracer = trace.get_tracer(__name__)
```

Depois:

```python
@tracer.start_as_current_span("worker.process_checkout")
def process_message(...):
```

Isso cria explicitamente um span chamado `worker.process_checkout`, envolvendo o processamento de negócio.

A instrumentação automática do Pika sabe que uma mensagem foi consumida, mas não conhece o significado do código. Ela não sabe o que é pedido, estoque, falha ou checkout.

O span manual é útil porque representa a operação de negócio.

Dentro dele são adicionados atributos:

```python
current_span.set_attribute("order.id", order_id)
current_span.set_attribute("order.correlation_id", correlation_id)
current_span.set_attribute("messaging.queue_wait_ms", queue_wait_ms)
```

No final, também são adicionados:

```text
order.final_status
order.failure_reason
```

No Jaeger, esse span pode apresentar atributos semelhantes a:

```text
order.id: 42
order.correlation_id: ...
messaging.queue_wait_ms: 18
order.final_status: COMPLETED
```

A instrumentação automática mostra a infraestrutura. O span manual acrescenta o significado do negócio.

## 6. Como os logs foram ligados aos traces

Em [`app/utils/diagnostics.py`](../app/utils/diagnostics.py), o código consulta o span atual:

```python
span_context = trace.get_current_span().get_span_context()
```

Quando existe um span válido, ele adiciona aos logs:

```python
{
    "trace_id": "...",
    "span_id": "..."
}
```

Um log pode ficar assim:

```json
{
  "level": "INFO",
  "event": "worker_process_message",
  "trace_id": "489b8c...",
  "span_id": "98fc...",
  "order_id": 42,
  "final_status": "COMPLETED"
}
```

Isso cria uma correlação entre log e trace:

```text
log com trace_id X
        │
        └── procurar trace X no Jaeger
```

Esses logs não estão sendo enviados pelo Collector. Eles continuam sendo impressos no `stdout` dos containers. A configuração atual do Collector possui somente um pipeline de traces.

## 7. `correlation_id` e `trace_id` não são a mesma coisa

O sistema mantém os dois identificadores, e isso faz sentido.

### `correlation_id`

É criado pela aplicação na rota de checkout e devolvido no header:

```http
X-Correlation-ID: ...
```

Ele também é colocado no corpo da mensagem enviada ao RabbitMQ.

É um identificador funcional, controlado pela aplicação, que pode ser mostrado ao cliente ou usado em atendimento e diagnóstico.

### `trace_id`

É criado e propagado pelo OpenTelemetry.

Ele identifica tecnicamente todos os spans daquela execução distribuída.

Em resumo:

```text
correlation_id = identificador da aplicação/negócio
trace_id       = identificador da observabilidade distribuída
span_id        = identificador de uma etapa do trace
```

## 8. Configuração do Collector

Em [`otel-collector/config.yaml`](../otel-collector/config.yaml), o Collector possui três partes.

### Receiver

```yaml
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
```

Significa: receba OTLP por gRPC na porta `4317`.

### Processor

```yaml
processors:
  batch: {}
```

Agrupa os dados antes de enviá-los.

### Exporter

```yaml
exporters:
  otlp/jaeger:
    endpoint: jaeger:4317
```

Significa: envie os dados ao container chamado `jaeger`, na porta OTLP `4317`.

Finalmente, o pipeline conecta as três peças:

```yaml
traces:
  receivers: [otlp]
  processors: [batch]
  exporters: [otlp/jaeger]
```

O fluxo é:

```text
receber → agrupar → enviar
```

## 9. Por que foram adicionados dois containers?

No [`compose.yaml`](../compose.yaml), o serviço `otel-collector` recebe sua configuração por meio de um volume.

O Jaeger expõe:

```yaml
ports:
  - "16686:16686"
```

Essa é a interface web:

```text
http://localhost:16686
```

A porta `4317` não precisa estar publicada na máquina host porque API, worker, Collector e Jaeger conversam pela rede interna do Compose.

Se a API fosse executada fora do Docker, `otel-collector` não seria resolvido como hostname. Nesse caso, seria necessário publicar a porta `4317` e usar um endereço como `localhost:4317`.

## 10. Traces, métricas e logs no projeto

O desenho atual é:

| Sinal | Produzido por | Destino |
|---|---|---|
| Traces | OpenTelemetry | Collector → Jaeger |
| Métricas HTTP | Prometheus Instrumentator | endpoint `/metrics` → Prometheus |
| Logs | `log_event()` | `stdout` dos containers |

O `Instrumentator()` no final do `main.py` não faz parte do tracing. Ele cria métricas para o Prometheus.

## 11. Pontos de atenção

A implementação funciona como uma boa primeira versão, mas possui algumas limitações:

- Não existe configuração explícita de *sampling*. O comportamento padrão tende a registrar todas as requisições. Em testes de carga massivos isso pode gerar bastante volume.
- Falhas de negócio são gravadas em `order.final_status`, mas o span não é explicitamente marcado com o status OpenTelemetry `ERROR`. No Jaeger, ele pode não aparecer destacado como erro.
- Como as exceções do worker são capturadas internamente, o decorator também pode não perceber que ocorreu um erro.
- O Compose não configura um volume de persistência para o Jaeger. Não se deve contar com a permanência dos traces depois de remover ou recriar o ambiente.
- `depends_on` com `service_started` não garante que Collector e Jaeger estejam completamente prontos. Um healthcheck deixaria a inicialização mais robusta.
- O Collector trata somente traces; ele não está configurado para receber logs ou métricas.
- No momento desta análise, `otel-collector/config.yaml` ainda não estava rastreado pelo Git, e a alteração do Compose também não havia sido commitada. Sem adicioná-los ao repositório, essa infraestrutura não acompanhará um novo clone do projeto.

## 12. Como verificar na prática

Suba o ambiente:

```powershell
docker compose up --build
```

Faça um checkout:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://localhost:8000/checkout `
  -ContentType application/json `
  -Body '{"product_id":1,"quantity":1}'
```

Depois, abra:

```text
http://localhost:16686
```

No Jaeger:

1. Selecione `flash-sale-api` ou `flash-sale-worker`.
2. Clique em **Find Traces**.
3. Abra um trace.
4. Procure os spans HTTP, SQLAlchemy, RabbitMQ e `worker.process_checkout`.
5. Abra o span do worker e observe os atributos `order.*` e `messaging.queue_wait_ms`.

## Resumo

O `tracing.py` prepara os instrumentos e o canal de envio. As instrumentações observam FastAPI, PostgreSQL e RabbitMQ. O Collector centraliza e encaminha os spans. O Jaeger transforma esses dados em uma linha do tempo visual da requisição.
