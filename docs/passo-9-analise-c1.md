# Passo 9 - Analise do C1

Este documento fecha a analise do cenario C1 a partir das tres execucoes
oficiais validas. O C1 mantem uma replica da API e uma replica do worker, sem
HPA e sem rate limiting, e serve como controle para os cenarios posteriores.

## Objetivo

O objetivo deste passo e complementar os indicadores de carga e processamento
com metricas de capacidade aceita, latencia das respostas bem-sucedidas e custo
computacional da aplicacao durante a carga. A analise usa somente os artefatos
ja coletados nas execucoes `c1-run-1`, `c1-run-2` e `c1-run-3`.

## Janela De Medicao

A janela de CPU e memoria comeca em `timestamps.load_started_at` e termina em
`timestamps.load_finished_at`, conforme o `run-metadata.json` de cada execucao.
Esses limites representam o inicio e o termino reais do processo de carga,
incluindo o encerramento observado pelo executor.

| Execucao | Inicio da carga | Termino da carga | Duracao observada |
| --- | --- | --- | ---: |
| `c1-run-1` | `2026-09-10T18:10:03Z` | `2026-09-10T18:11:53Z` | 110 s |
| `c1-run-2` | `2026-09-10T18:19:12Z` | `2026-09-10T18:21:02Z` | 110 s |
| `c1-run-3` | `2026-09-10T18:37:55Z` | `2026-09-10T18:39:42Z` | 107 s |

As amostras anteriores ao inicio e posteriores ao termino sao usadas somente
para interpolar os valores nos limites. Coleta ociosa, drenagem e cooldown nao
entram nas integrais.

## Containers Incluidos E Excluidos

O calculo le `metrics/kubernetes-resources.csv` e inclui somente os containers
`api` e `worker`. Quando houver mais de um Pod do mesmo papel no mesmo instante,
os valores sao somados antes da integracao.

Os containers `postgres`, `rabbitmq`, `prometheus`, `otel-collector`, `jaeger` e
`k6` ficam fora do calculo. Assim, as metricas representam o custo da aplicacao
e de seu processamento assincrono, sem incorporar banco, mensageria,
observabilidade ou geracao de carga.

## Metodo E Formulas

O script valida separadamente que as series de `api` e `worker` possuem uma
amostra no inicio ou antes dele e outra no termino ou depois dele. Os valores
exatos dos dois limites sao obtidos por interpolacao linear. Entre pontos
consecutivos, a area e calculada pelo metodo trapezoidal:

```text
area do intervalo = (valor inicial + valor final) / 2 * duracao do intervalo
```

Para CPU, a soma das areas em cores por segundo produz CPU-segundos. Para
memoria, a soma em MiB por segundo e dividida por 60 para produzir MiB-minutos.
As integrais da API e do worker sao somadas:

```text
app_cpu_seconds_load = integral_cpu_api + integral_cpu_worker
app_memory_mib_minutes_load = integral_memoria_api + integral_memoria_worker
```

Os custos por 1.000 pedidos usam somente pedidos com status `COMPLETED`:

```text
CPU-s/1.000 pedidos = app_cpu_seconds_load / orders_completed * 1000
MiB-min/1.000 pedidos = app_memory_mib_minutes_load / orders_completed * 1000
```

O throughput aceito vem diretamente de `responses_2xx.rate`. Os percentis de
latencia das respostas aceitas vem de `response_duration_2xx`, sem misturar
respostas 5xx ou erros de conexao.

## Resultados

| Execucao | Pedidos concluidos | Throughput aceito (req/s) | p95 2xx (ms) | p99 2xx (ms) | CPU na carga (CPU-s) | Memoria na carga (MiB-min) | CPU-s/1.000 pedidos | MiB-min/1.000 pedidos |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `c1-run-1` | 640 | 5.717446 | 4017.955 | 28887.499 | 21.176854 | 277.897441 | 33.088834 | 434.214752 |
| `c1-run-2` | 740 | 6.623394 | 5286.602 | 30104.240 | 24.618707 | 281.599901 | 33.268523 | 380.540407 |
| `c1-run-3` | 812 | 7.548364 | 4584.267 | 30199.457 | 26.655464 | 268.728921 | 32.826926 | 330.946947 |

As estatisticas entre execucoes estao em
`results/consolidated/c1-aggregate-summary.csv`. Para as novas metricas, as
medias foram 6.629735 req/s aceitas, 4629.608 ms no p95 2xx, 29730.398667 ms no
p99 2xx, 24.150342 CPU-s e 276.075421 MiB-min durante a carga. Os custos medios
normalizados foram 33.061428 CPU-s e 381.900702 MiB-min por 1.000 pedidos
concluidos.

## Comparacao Entre As Execucoes

O `c1-run-3` apresentou o maior throughput aceito e concluiu mais pedidos. Seu
consumo absoluto de CPU foi o maior, mas o custo normalizado de CPU foi o menor.
Os tres valores normalizados de CPU ficaram proximos, entre 32.826926 e
33.268523 CPU-s por 1.000 pedidos.

O consumo absoluto de memoria variou menos do que a quantidade de pedidos
concluidos. Por isso, o custo normalizado caiu de 434.214752 MiB-min por 1.000
pedidos no `c1-run-1` para 330.946947 no `c1-run-3`. Essa reducao indica melhor
amortizacao da memoria residente quando mais pedidos sao concluidos na mesma
janela, e nao uma reducao equivalente do uso instantaneo de memoria.

O p95 das respostas 2xx foi pior no `c1-run-2`. O p99 ficou entre 28,9 e 30,2
segundos nas tres execucoes. Mesmo considerando somente respostas aceitas, a
cauda de latencia permaneceu alta e variavel.

## Limitacoes

- As metricas sao aproximacoes obtidas de amostras do Metrics Server, coletadas
  normalmente a cada cinco segundos, e nao contadores continuos de uso.
- A serie da API possui intervalos de ate 20.001 segundos ao redor das
  reinicializacoes observadas. O metodo trapezoidal liga as amostras vizinhas
  por interpolacao linear; ele nao reconstrui o comportamento interno desses
  intervalos.
- As tres execucoes foram classificadas como `UNSTABLE` e registraram uma
  reinicializacao. Portanto, a variacao inclui o efeito dessas falhas.
- A drenagem do `c1-run-2` nao terminou no limite de observacao: durou 607
  segundos e deixou um pedido pendente. A drenagem foi excluida do consumo aqui
  calculado, e a normalizacao usa os 740 pedidos efetivamente concluidos.
- O custo apresentado inclui somente API e worker. Banco, RabbitMQ,
  observabilidade, Kubernetes e gerador de carga tambem consomem recursos, mas
  pertencem a outra fronteira de medicao.
- Existem apenas tres repeticoes em uma unica maquina e configuracao. Os
  resultados descrevem este ambiente experimental e nao devem ser extrapolados
  diretamente para producao.

## Conclusao

O C1 cumpre o papel de cenario de controle reproduzivel: mantem topologia,
imagem e perfil de carga constantes e fornece uma referencia sem mecanismos de
escalabilidade ou protecao. O custo normalizado de CPU foi consistente entre as
tres repeticoes, enquanto throughput aceito, latencia de cauda e custo
normalizado de memoria apresentaram variacao relevante.

O controle tambem evidencia saturacao. O throughput aceito ficou abaixo dos
alvos de 20 e 22 req/s, as respostas 2xx chegaram a dezenas de segundos no p99,
houve descartes, erros de conexao e reinicializacoes, e uma das drenagens nao
terminou. Assim, o C1 e uma base empirica adequada para comparar C2, C3 e C4,
mas nao representa um estado operacional estavel ou uma meta de desempenho.
