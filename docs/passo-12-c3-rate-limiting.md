# Passo 12 — C3 com rate limiting na borda

## Objetivo e tratamento experimental

O C3 avalia isoladamente a proteção de entrada por rate limiting antes que as
requisições consumam recursos da aplicação. O tratamento experimental do C3 é,
explicitamente, a proteção de entrada realizada pelo gateway Kong em modo
DB-less. O rate limiting não é implementado como middleware da FastAPI.

A variável alterada em relação ao C1 é a presença do gateway com limitação
global de 20 requisições por segundo. Permanecem constantes:

- a aplicação, o código e as imagens de API e worker;
- uma réplica fixa da API e uma réplica fixa do worker;
- a ausência total de HPA e de KEDA;
- PostgreSQL, RabbitMQ e o fluxo assíncrono de checkout;
- Prometheus, OpenTelemetry e Jaeger;
- `preAllocatedVUs=100`, `maxVUs=300`, timeout de 60 segundos e
  `gracefulStop=30s`;
- o perfil oficial: 1 req/s inicial, 20 req/s por 20s, 22 req/s por 30s,
  22 req/s por 30s e 0 req/s por 10s.

Os resultados futuros serão gravados em `results/experiments/c3`. Uma execução
oficial é recusada pelo executor enquanto não existir ao menos um exploratório
C3 válido.

## Arquitetura

O k6 usa exclusivamente `http://gateway:8000`. O Service `gateway` aponta para
uma única réplica do Kong, que encaminha as requisições aceitas para
`http://api:8000`. A rota `POST /checkout` preserva o caminho ao encaminhá-lo.
A rota `GET /health` existe para o preflight comprovar o encadeamento
gateway → API e observar os cabeçalhos de rate limiting.

O Kong usa configuração declarativa montada pelo ConfigMap
`gateway-config`. Não existe banco do Kong nem Redis. O gateway não possui HPA
e seu Deployment usa estratégia `Recreate`, evitando sobreposição de réplicas
durante uma atualização.

## Configuração exata do rate limiting

- gateway: Kong `3.9.1-ubuntu`, nunca `latest`;
- modo: DB-less (`KONG_DATABASE=off`);
- escopo do plugin: serviço `checkout-api`;
- upstream: `http://api:8000`;
- rota da carga: `POST /checkout`;
- limite: 20 requisições;
- janela: 1 segundo;
- `limit_by: service`;
- `policy: local`;
- `hide_client_headers: false`;
- `fault_tolerant: false`;
- réplicas do gateway: exatamente 1.

O limite de 20 req/s foi escolhido porque o C1 apresentou comportamento normal
em 20 req/s e saturação em 22 req/s. Assim, nos estágios de 22 req/s o C3 deve
rejeitar de forma controlada o excesso antes que ele alcance a API.

## Significado das respostas

- `2xx`: requisição aceita pelo gateway e processada pela API;
- `429`: rejeição esperada da proteção, contabilizada separadamente;
- `5xx`: falha inesperada;
- erro de conexão: falha inesperada;
- qualquer outro status: falha inesperada.

Uma resposta 429 não é, por si só, falha, invalidade ou instabilidade. O k6 a
marca como status HTTP esperado, mede sua latência e verifica os cabeçalhos de
rate limiting. A igualdade abaixo deve ser satisfeita:

```text
requisições iniciadas = 2xx + 429 + 5xx + erros de conexão + outros status
```

Como o 429 é produzido no gateway, ele não pode criar pedido no PostgreSQL nem
publicar mensagem no RabbitMQ. O banco é a fonte autoritativa para verificar a
ausência desses efeitos. Após a drenagem, o resumo de proteção exige:

- `total_orders = responses_2xx`;
- `COMPLETED + PENDING + PROCESSING + FAILED = total_orders`;
- `COMPLETED = responses_2xx`;
- `PENDING = PROCESSING = FAILED = 0`.

Essas igualdades comprovam que somente as requisições aceitas criaram pedidos e
que todos os pedidos aceitos percorreram o fluxo assíncrono e foram processados
pelo worker.

## Métricas e evidências específicas

O k6 registra contagem e taxa de `2xx`, `429`, `5xx`, erros de conexão e outros
status; percentual de `429`; throughput total, aceito e rejeitado; falhas
inesperadas; e latência separada de `2xx` e `429`. O resumo por estágio inclui
as mesmas classes, taxas por segundo e a validação da igualdade de
classificação.

O coletor registra CPU, memória, reinicializações e quantidade de Pods do
gateway, além de preservar a coleta de API, worker, RabbitMQ, PostgreSQL,
Prometheus, OpenTelemetry e Jaeger. Cada execução C3 preserva:

- manifests do gateway usados na execução;
- configuração declarativa efetiva e recursos Kubernetes efetivos;
- configuração resumida do tratamento;
- cabeçalhos de rate limiting observados no preflight;
- logs do gateway, inclusive logs anteriores quando houver reinicialização;
- snapshots de recursos e Pods antes e depois;
- resumo de proteção com contagem e percentual de 429;
- resumo de drenagem, resumo do banco, traces e metadados completos.

O `rabbitmq_publish_delta`, obtido de `message_stats.publish` na API
administrativa do RabbitMQ, continua registrado no `protection-summary.json`.
Esse contador pode subcontar publicações na janela observada e, por isso, é uma
evidência auxiliar. O campo `rabbitmq_publish_counter_matches` informa se o
delta coincide com `responses_2xx`, mas uma divergência isolada é diagnóstica e
não invalida a execução.

## Critérios de validade

O procedimento é válido quando o preflight é aprovado; nó, API, worker e
gateway têm a configuração esperada; não há HPA nem resíduos de Jobs k6; banco,
estoques e fila começam no estado correto; o alvo do k6 é o gateway; a
configuração DB-less carregada contém o limite contratado; a classificação é
exaustiva; o banco reconcilia os `2xx` com os pedidos criados e concluídos, sem
pedidos pendentes, em processamento ou com falha após a drenagem; o coletor e
as exportações terminam corretamente; e todas as evidências obrigatórias
existem. A igualdade do contador administrativo de publicações do RabbitMQ não
é, isoladamente, um critério de invalidade.

A presença controlada de 429 demonstra que a proteção atuou. A ausência de 429
torna uma futura execução C3 inválida para demonstrar o tratamento, mas não é
classificada como falha interna da aplicação.

## Critérios de estabilidade

O sistema é estável quando não há `5xx`, erros de conexão, outros status
inesperados, reinicializações, perda do estado `Ready` nem drenagem incompleta
ou acima do objetivo de 180 segundos. Mudanças na quantidade de réplicas
também invalidam o contrato do C3: API, worker e gateway devem permanecer em
1 réplica. Respostas 429 controladas não tornam o sistema instável.

Validade do procedimento e estabilidade do sistema são avaliadas e exibidas
separadamente no `validity-checklist.md`.

## Limitações metodológicas

A política `local` mantém contadores no próprio nó Kong. Ela é adequada ao C3
porque há exatamente uma réplica do gateway, mas não representa um gateway
distribuído. A janela de um segundo pode produzir variação nas fronteiras de
janela, e o limite mede requisições vistas pelo gateway, não trabalho concluído
pela API. O gateway também acrescenta custo de proxy; portanto, a comparação
deve ser interpretada como efeito do tratamento completo de proteção na borda.
O teste não avalia Redis, múltiplas réplicas do gateway, HPA, KEDA ou o C4.

## Comandos para uso posterior

Preparação do ambiente C3:

```bash
bash scripts/bootstrap-k8s.sh --scenario c3
```

Primeiro exploratório, somente depois de revisar o bootstrap e o preflight:

```bash
bash scripts/run-k8s-experiment.sh \
  --scenario c3 \
  --type exploratory \
  --id c3-exploratory-001
```

Os valores padrão desse comando correspondem ao perfil contratado. Nenhum dos
comandos foi executado durante a implementação deste passo.
