# Etapa 1 - Experimentos Oficiais

Este documento descreve a linha de base oficial da Etapa 1 do TCC. Ela e composta por nove execucoes do endpoint assincrono `POST /checkout`: tres cenarios de carga e tres repeticoes por cenario.

## Cenarios

Os nomes dos scripts representam apenas o perfil de carga:

- `c0b`: carga baixa.
- `c0m`: carga moderada.
- `c0a`: carga alta.

Todos usam o mesmo fluxo assincrono via `POST /checkout`.

## Parametros De Carga

Todos os cenarios usam `ramping-arrival-rate`, `timeUnit: '1s'`, `gracefulStop: '30s'`, timeout HTTP de `60s` e 90 segundos de carga programada.

| Cenario | Perfil | startRate | preAllocatedVUs | maxVUs | Estagios |
| --- | --- | ---: | ---: | ---: | --- |
| `c0b` | baixa | 1 | 20 | 50 | 2 por 20s, 5 por 30s, 8 por 30s, 0 por 10s |
| `c0m` | moderada | 1 | 50 | 150 | 10 por 20s, 20 por 30s, 30 por 30s, 0 por 10s |
| `c0a` | alta | 1 | 100 | 300 | 20 por 20s, 40 por 30s, 60 por 30s, 0 por 10s |

Os scripts validam o contrato atual: HTTP 201, JSON valido, ID do pedido, status `PENDING`, produto e quantidade.

## Reconstruir O Ambiente

Execute a partir da raiz do projeto:

```bash
docker compose down
docker compose build --no-cache
docker compose up -d
```

Confirme que os containers estao em execucao:

```bash
docker compose ps
```

## Executar As Repeticoes

O runner oficial executa uma unica repeticao por chamada e aborta se a pasta de resultado ja existir.

```bash
./scripts/run-experiment.sh --scenario c0b --run 1
./scripts/run-experiment.sh --scenario c0m --run 1
./scripts/run-experiment.sh --scenario c0a --run 1
```

Use `--base-url` se o k6 precisar enxergar a API por outra URL. Use `--no-cooldown` apenas para execucoes de verificacao, pois a linha de base oficial deve manter o intervalo de 120 segundos apos cada coleta.

A ordem recomendada para a linha de base e:

```text
c0b-run-1
c0m-run-1
c0a-run-1
c0b-run-2
c0m-run-2
c0a-run-2
c0b-run-3
c0m-run-3
c0a-run-3
```

## Arquivos Produzidos

Cada execucao cria `results/experiments/<cenario>-run-<n>/` com:

- `metadata.json`: parametros, versoes, commit e status da execucao.
- `k6-summary.json`: resumo estruturado exportado pelo k6.
- `k6.log`: saida completa do k6.
- `docker-stats.csv`: CPU, memoria, I/O, rede e PIDs dos containers.
- `rabbitmq.csv`: tamanho da fila, publicacoes, acknowledgements e consumidores.
- `prometheus.csv`: series consultadas via `query_range` em formato longo.
- `api.log`: logs da API desde o inicio da coleta.
- `worker.log`: logs do worker desde o inicio da coleta.
- `db-summary.json`: resumo final de pedidos e estoques.
- `drain-summary.json`: duracao e resultado da drenagem.

## Interpretacao

`dropped_iterations` indica iteracoes que o k6 nao conseguiu iniciar no ritmo programado. Em testes com `ramping-arrival-rate`, isso normalmente sinaliza saturacao do gerador, limite de VUs ou incapacidade do sistema de sustentar a taxa-alvo. O valor nao invalida automaticamente a coleta; ele deve ser analisado junto com latencia, checks, erros HTTP e consumo de recursos.

A duracao programada e sempre 90 segundos de carga. A duracao observada do k6 e o tempo real entre inicio e fim do processo k6, incluindo efeitos de `gracefulStop` e encerramento. O tempo de drenagem e medido depois do k6 e termina quando fila RabbitMQ e pedidos `PENDING`/`PROCESSING` chegam a zero, ou quando o limite de 180 segundos e atingido.

Esses nove testes formam a linha de base oficial da Etapa 1.
